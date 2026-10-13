"""
LangGraph agent: turn a raw transcript into structured, persisted
study entries.

Graph shape (see docs/plans/.../plan.md KTD4):

    transcript -> [extract_entries] -> [validate_entries] -> [persist_entries] -> END
                                              |-> invalid/empty -> [request_clarification] -> END
"""

from typing import Any, TypedDict

from langchain_openai import ChatOpenAI

from sra.agents.schemas import StudyEntryDraft
from sra.config import get_settings
from sra.db.models import StudyEntry


class IngestionState(TypedDict):
    transcript: str
    user_id: int
    session: Any  # sqlmodel.Session -- typed loosely so the graph doesn't import the DB layer at module load time
    drafts: list[StudyEntryDraft]
    entries: list[StudyEntry]
    error: str | None


def _get_chat_model() -> ChatOpenAI:
    """
    Construct the OpenAI model used by extract_entries.
    Structured-output setup and invocation happen in extract_entries.

    Kept separate so tests can monkeypatch
    `sra.agents.ingestion_graph._get_chat_model` with a fake model,
    avoiding network calls and API keys.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=2048,
        timeout=30,
        max_retries=1,
    )


def extract_entries(state: IngestionState) -> IngestionState:
    """
    Get a model from _get_chat_model(), bind the StudyEntryDrafts
    schema, and invoke it with a prompt built from state["transcript"].
    Read the resulting container's drafts into state["drafts"].
    A single transcript may cover multiple topics -- return
    one draft per topic, not one draft that mushes them together.

    On an LLM call failure, set state["error"] to a short message
    instead of raising, so the graph's conditional edge can route to
    request_clarification rather than crash the whole request.

    Concept: LangChain's structured-output pattern -- bind a Pydantic
    schema to the model call so you get validated Python objects
    back, not a string you have to parse yourself.
    """
    raise NotImplementedError


def validate_entries(state: IngestionState) -> IngestionState:
    """
    Drop any draft in state["drafts"] with an empty topic or summary.
    If none survive (or state["error"] is already set from
    extract_entries), set state["error"] so the conditional edge
    below routes to request_clarification instead of persist_entries.
    """
    raise NotImplementedError


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
    against a fake LLM, with no database involved.
    """
    raise NotImplementedError


def request_clarification(state: IngestionState) -> IngestionState:
    """
    Terminal node for the invalid/empty branch. Ensure state["error"]
    is a clear, user-facing message (not a raw exception string) so
    the API layer can surface it as-is.
    """
    raise NotImplementedError


def build_ingestion_graph():
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
    raise NotImplementedError
