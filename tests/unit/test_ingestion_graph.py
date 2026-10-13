import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr
from sqlmodel import select

from sra.agents.ingestion_graph import (
    IngestionState,
    build_ingestion_graph,
    extract_entries,
    persist_entries,
    request_clarification,
    validate_entries,
)
from sra.agents.schemas import StudyEntryDraft, StudyEntryDrafts
from sra.config import Settings
from sra.db.models import ReviewSchedule, StudyEntry


class FakeChatModel:
    """A stand-in for ChatOpenAI with a scripted structured response."""

    def __init__(self, drafts: list[StudyEntryDraft] | None = None, raise_error: bool = False):
        self._drafts = drafts or []
        self._raise_error = raise_error

    def with_structured_output(self, schema, **kwargs):
        assert schema is StudyEntryDrafts
        return self

    def invoke(self, *args, **kwargs):
        if self._raise_error:
            raise RuntimeError("simulated LLM failure")
        return StudyEntryDrafts(drafts=self._drafts)


def test_extract_entries_splits_multi_topic_transcript(monkeypatch):
    drafts = [
        StudyEntryDraft(topic="Photosynthesis", summary="light + dark reactions"),
        StudyEntryDraft(topic="Mitosis", summary="cell division phases"),
    ]
    monkeypatch.setattr(
        "sra.agents.ingestion_graph._get_chat_model",
        lambda: FakeChatModel(drafts=drafts),
    )

    state: IngestionState = {
        "transcript": "today I covered photosynthesis and mitosis",
        "user_id": 1,
        "session": None,
        "drafts": [],
        "entries": [],
        "error": None,
    }
    result = extract_entries(state)

    assert len(result["drafts"]) == 2
    assert {d.topic for d in result["drafts"]} == {"Photosynthesis", "Mitosis"}
    assert result["error"] is None


def test_extract_entries_sets_error_on_llm_failure(monkeypatch):
    monkeypatch.setattr(
        "sra.agents.ingestion_graph._get_chat_model",
        lambda: FakeChatModel(raise_error=True),
    )

    state: IngestionState = {"transcript": "...", "user_id": 1, "session": None, "drafts": [], "entries": [], "error": None}
    result = extract_entries(state)

    assert result["error"] is not None


@pytest.mark.parametrize("transcript", ["", " \n\t"])
def test_extract_entries_skips_model_for_blank_transcript(monkeypatch, transcript):
    def unexpected_model():
        pytest.fail("Blank transcripts should not construct a model")

    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", unexpected_model)
    previous = StudyEntryDraft(topic="Old topic", summary="From an earlier attempt")
    state: IngestionState = {
        "transcript": transcript,
        "user_id": 1,
        "session": None,
        "drafts": [previous],
        "entries": [],
        "error": None,
    }

    result = extract_entries(state)

    assert result["drafts"] == []
    assert result["error"]
    assert state["drafts"] == [previous]


@pytest.mark.parametrize("failure_stage", ["setup", "invocation"])
def test_extract_entries_clears_stale_drafts_on_failure(monkeypatch, failure_stage):
    def failing_model():
        if failure_stage == "setup":
            raise RuntimeError("private configuration details")
        return FakeChatModel(raise_error=True)

    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", failing_model)
    state: IngestionState = {
        "transcript": "I studied cell division",
        "user_id": 1,
        "session": None,
        "drafts": [StudyEntryDraft(topic="Old topic", summary="Old summary")],
        "entries": [],
        "error": None,
    }

    result = extract_entries(state)

    assert result["drafts"] == []
    assert result["error"] == "Could not process this transcript. Please try again."


@pytest.mark.parametrize(
    ("raw_drafts", "expected_topics"),
    [
        ([], []),
        (
            [{"topic": "Photosynthesis", "summary": "Light reactions"},
             {"topic": "Mitosis", "summary": "Cell division"}],
            ["Photosynthesis", "Mitosis"],
        ),
        ([{"topic": "Missing summary"}], None),
    ],
)
def test_extract_entries_parses_structured_model_response(monkeypatch, raw_drafts, expected_topics):
    """Exercise the real structured-output pipeline while replacing only model generation."""
    transcript = "Today I studied photosynthesis and mitosis."
    settings = Settings(openai_api_key=SecretStr("sk-test"))
    monkeypatch.setattr("sra.agents.ingestion_graph.get_settings", lambda: settings)
    seen_messages = []

    def fake_generate(self, messages, **kwargs):
        seen_messages.extend(messages)
        message = AIMessage(
            content="",
            tool_calls=[{
                "name": StudyEntryDrafts.__name__,
                "args": {"drafts": raw_drafts},
                "id": "test-extraction",
            }],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    monkeypatch.setattr(ChatOpenAI, "_generate", fake_generate)
    state: IngestionState = {
        "transcript": transcript,
        "user_id": 1,
        "session": None,
        "drafts": [],
        "entries": [],
        "error": "Previous attempt failed",
    }

    result = extract_entries(state)

    assert len(seen_messages) == 2
    assert seen_messages[0].type == "system"
    assert seen_messages[1].type == "human"
    assert seen_messages[1].content == transcript
    if expected_topics is None:
        assert result["drafts"] == []
        assert result["error"] == "Could not process this transcript. Please try again."
    else:
        assert all(isinstance(draft, StudyEntryDraft) for draft in result["drafts"])
        assert [draft.topic for draft in result["drafts"]] == expected_topics
        assert result["error"] is None


@pytest.mark.parametrize(("topic", "summary"), [("", ""), (" \t", "Valid summary"), ("Valid topic", "\n ")])
def test_validate_entries_drops_empty_drafts_and_sets_error(topic, summary):
    state: IngestionState = {
        "transcript": "...",
        "user_id": 1,
        "session": None,
        "drafts": [StudyEntryDraft(topic=topic, summary=summary)],
        "entries": [],
        "error": None,
    }
    result = validate_entries(state)

    assert result["drafts"] == []
    assert result["error"] is not None


def test_validate_entries_keeps_valid_drafts():
    valid = StudyEntryDraft(topic="Photosynthesis", summary="light reactions")
    state: IngestionState = {"transcript": "...", "user_id": 1, "session": None, "drafts": [valid], "entries": [], "error": None}

    result = validate_entries(state)

    assert result["drafts"] == [valid]
    assert result["error"] is None


def test_validate_entries_keeps_valid_drafts_among_invalid_ones():
    valid = StudyEntryDraft(topic="Mitosis", summary="Cell division")
    drafts = [
        StudyEntryDraft(topic=" ", summary="Invalid topic"),
        valid,
        StudyEntryDraft(topic="Invalid summary", summary=""),
    ]
    state: IngestionState = {
        "transcript": "...",
        "user_id": 1,
        "session": None,
        "drafts": drafts,
        "entries": [],
        "error": None,
    }

    result = validate_entries(state)

    assert result["drafts"] == [valid]
    assert result["error"] is None
    assert state["drafts"] == drafts


@pytest.mark.parametrize("drafts", [[], [StudyEntryDraft(topic="Mitosis", summary="Cell division")]])
def test_validate_entries_preserves_extraction_error(drafts):
    state: IngestionState = {
        "transcript": "...",
        "user_id": 1,
        "session": None,
        "drafts": drafts,
        "entries": [],
        "error": "Could not process this transcript. Please try again.",
    }

    result = validate_entries(state)

    assert result["error"] == state["error"]


def test_validate_entries_sets_error_for_no_drafts():
    state: IngestionState = {
        "transcript": "...",
        "user_id": 1,
        "session": None,
        "drafts": [],
        "entries": [],
        "error": None,
    }

    result = validate_entries(state)

    assert result["drafts"] == []
    assert result["error"]


def test_persist_entries_creates_one_row_per_draft(session, seeded_user):
    drafts = [
        StudyEntryDraft(topic="Photosynthesis", subtopic="Light reactions", tags=["biology"], summary="light reactions"),
        StudyEntryDraft(topic="Mitosis", summary="cell division"),
    ]
    state: IngestionState = {
        "transcript": "...",
        "user_id": seeded_user.id,
        "session": session,
        "drafts": drafts,
        "entries": [],
        "error": None,
    }

    result = persist_entries(state)

    assert len(result["entries"]) == 2
    assert all(isinstance(e, StudyEntry) for e in result["entries"])
    assert all(e.user_id == seeded_user.id for e in result["entries"])
    assert len(session.exec(select(StudyEntry)).all()) == 2
    for entry, draft in zip(result["entries"], drafts, strict=True):
        assert entry.id is not None
        assert entry.topic == draft.topic
        assert entry.subtopic == draft.subtopic
        assert entry.tags == draft.tags
        assert entry.summary == draft.summary
        assert entry.raw_transcript == state["transcript"]
        reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)).all()
        assert len(reviews) == 6
        assert {review.interval_label for review in reviews} == {"1d", "3d", "7d", "1mo", "3mo", "6mo"}
    assert state["entries"] == []


def test_request_clarification_preserves_error_message():
    state: IngestionState = {
        "transcript": "...",
        "user_id": 1,
        "session": None,
        "drafts": [],
        "entries": [],
        "error": "no topics found",
    }
    result = request_clarification(state)
    assert result["error"] == "no topics found"


@pytest.mark.parametrize("error", [None, ""])
def test_request_clarification_supplies_missing_message(error):
    state: IngestionState = {
        "transcript": "...", "user_id": 1, "session": None, "drafts": [], "entries": [], "error": error,
    }

    result = request_clarification(state)

    assert result["error"] == "Please describe the topics you studied and what you learned."


def test_build_ingestion_graph_end_to_end_persists_expected_rows(monkeypatch, session, seeded_user):
    drafts = [StudyEntryDraft(topic="Photosynthesis", summary="light + dark reactions")]
    monkeypatch.setattr(
        "sra.agents.ingestion_graph._get_chat_model",
        lambda: FakeChatModel(drafts=drafts),
    )

    graph = build_ingestion_graph()
    result = graph.invoke(
        {
            "transcript": "today I studied photosynthesis",
            "user_id": seeded_user.id,
            "drafts": [],
            "entries": [],
            "error": None,
            "session": session,
        }
    )

    assert len(result["entries"]) == 1
    assert result["entries"][0].topic == "Photosynthesis"


def test_build_ingestion_graph_routes_to_clarification_on_empty_transcript(monkeypatch, session, seeded_user):
    monkeypatch.setattr(
        "sra.agents.ingestion_graph._get_chat_model",
        lambda: FakeChatModel(drafts=[]),
    )

    graph = build_ingestion_graph()
    result = graph.invoke(
        {"transcript": "", "user_id": seeded_user.id, "drafts": [], "entries": [], "error": None, "session": session}
    )

    assert result["entries"] == []
    assert result["error"] is not None


def test_build_ingestion_graph_routes_llm_failure_to_clarification(monkeypatch, session, seeded_user):
    monkeypatch.setattr(
        "sra.agents.ingestion_graph._get_chat_model",
        lambda: FakeChatModel(raise_error=True),
    )
    graph = build_ingestion_graph()
    updates = list(graph.stream(
        {
            "transcript": "Today I studied mitosis",
            "user_id": seeded_user.id,
            "session": session,
            "drafts": [],
            "entries": [],
            "error": None,
        },
        stream_mode="updates",
    ))

    assert [next(iter(update)) for update in updates] == [
        "extract_entries", "validate_entries", "request_clarification",
    ]
    assert updates[-1]["request_clarification"]["error"] == "Could not process this transcript. Please try again."
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
