"""
LangGraph agent: turn a raw transcript into structured, persisted
study entries.

Graph shape (see docs/plans/.../plan.md KTD4):

    transcript -> [extract_entries] -> [validate_entries] -> [persist_entries] -> END
                                              |-> invalid/empty -> [request_clarification] -> END
"""

from functools import partial
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from sra.agents.entry_extractor import EntryExtractor, get_entry_extractor
from sra.agents.schemas import StudyEntryDraft
from sra.db import repository
from sra.db.models import StudyEntry, StudyEntryCreate


class IngestionState(TypedDict):
    transcript: str
    user_id: int
    session: Any  # sqlmodel.Session -- typed loosely so the graph doesn't import the DB layer at module load time
    drafts: list[StudyEntryDraft]
    entries: list[StudyEntry]
    error: str | None


def extract_entries(
    state: IngestionState,
    *,
    extractor: EntryExtractor | None = None,
) -> IngestionState:
    """
    Ask the configured EntryExtractor to turn state["transcript"] into drafts,
    then place those application values in state["drafts"].
    A single transcript may cover multiple topics -- return
    one draft per topic, not one draft that mushes them together.

    On an LLM call failure, set state["error"] to a short message
    instead of raising, so the graph's conditional edge can route to
    request_clarification rather than crash the whole request.

    The production adapter owns LangChain's structured-output binding. This
    node receives already validated application values through the protocol.
    """
    if not state["transcript"].strip():
        return {**state, "drafts": [], "error": "Please describe what you studied before submitting."}

    try:
        resolved_extractor = extractor if extractor is not None else get_entry_extractor()
        drafts = resolved_extractor.extract(state["transcript"])
    except Exception:
        return {**state, "drafts": [], "error": "Could not process this transcript. Please try again."}

    return {**state, "drafts": drafts, "error": None}


def validate_entries(state: IngestionState) -> IngestionState:
    """
    Drop any draft in state["drafts"] with an empty topic or summary.
    If none survive (or state["error"] is already set from
    extract_entries), set state["error"] so the conditional edge
    below routes to request_clarification instead of persist_entries.
    """
    drafts = [draft for draft in state["drafts"] if draft.topic.strip() and draft.summary.strip()]
    error = state["error"]
    if not drafts and not error:
        error = "No usable study topics were found. Please describe what you studied in more detail."
    return {**state, "drafts": drafts, "error": error}


def persist_entries(state: IngestionState) -> IngestionState:
    """
    For each surviving draft, build a StudyEntryCreate from it (topic,
    subtopic, tags, summary from the draft; raw_transcript from
    state["transcript"]) and call
    repository.create_entry(entry_create=..., user_id=state["user_id"],
    session=state["session"]), collecting the results into
    state["entries"].

    Concept: this is the one node in the graph with a side effect (a
    DB write) -- keeping it isolated here is what lets
    extract_entries and validate_entries stay pure and easy to test
    against a handwritten extractor, with no database involved.
    """
    entries: list[StudyEntry] = []
    for draft in state["drafts"]:
        entry_create = StudyEntryCreate(
            topic=draft.topic,
            subtopic=draft.subtopic,
            tags=draft.tags,
            summary=draft.summary,
            raw_transcript=state["transcript"],
        )
        entry = repository.create_entry(
            entry_create=entry_create,
            user_id=state["user_id"],
            session=state["session"],
        )
        entries.append(entry)

    return {**state, "entries": entries}


def request_clarification(state: IngestionState) -> IngestionState:
    """
    Terminal node for the invalid/empty branch. Ensure state["error"]
    is a clear, user-facing message (not a raw exception string) so
    the API layer can surface it as-is.
    """
    error = state["error"] or "Please describe the topics you studied and what you learned."
    return {**state, "error": error}


def build_ingestion_graph(*, extractor: EntryExtractor | None = None):
    """
    Wire extract_entries -> validate_entries -> persist_entries, with
    a conditional edge out of validate_entries: to persist_entries
    when state["error"] is falsy, to request_clarification otherwise.
    Return the compiled graph (`.compile()`).

    Concept: this is LangGraph's core wiring API --
    `StateGraph(IngestionState)`, repeated `.add_node(name, fn)`
    calls, `.add_edge(a, b)` for unconditional transitions, and
    `.add_conditional_edges(node, router_fn, {branch_value: target})`
    for the fork above. `router_fn` just needs to read
    `state["error"]` and return which branch name to take.
    """
    resolved_extractor = extractor if extractor is not None else get_entry_extractor()
    graph = StateGraph(IngestionState)
    graph.add_node("extract_entries", partial(extract_entries, extractor=resolved_extractor))
    graph.add_node("validate_entries", validate_entries)
    graph.add_node("persist_entries", persist_entries)
    graph.add_node("request_clarification", request_clarification)

    graph.add_edge(START, "extract_entries")
    graph.add_edge("extract_entries", "validate_entries")

    def route_after_validation(state: IngestionState) -> str:
        if state["error"]:
            return "invalid"
        return "valid"

    graph.add_conditional_edges(
        "validate_entries",
        route_after_validation,
        {"valid": "persist_entries", "invalid": "request_clarification"},
    )
    graph.add_edge("persist_entries", END)
    graph.add_edge("request_clarification", END)
    return graph.compile(name="sra-ingestion")
