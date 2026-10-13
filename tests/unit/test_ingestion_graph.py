import pytest
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


