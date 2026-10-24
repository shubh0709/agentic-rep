from dataclasses import dataclass, field

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
from sra.agents.schemas import StudyEntryDraft
from sra.db.models import ReviewSchedule, StudyEntry


@dataclass
class ScriptedEntryExtractor:
    """Application-level fake for the one external ingestion boundary."""

    drafts: list[StudyEntryDraft] = field(default_factory=list)
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def extract(self, transcript: str) -> list[StudyEntryDraft]:
        self.calls.append(transcript)
        if self.error is not None:
            raise self.error
        return self.drafts


def test_extract_entries_splits_multi_topic_transcript():
    drafts = [
        StudyEntryDraft(topic="Photosynthesis", summary="light + dark reactions"),
        StudyEntryDraft(topic="Mitosis", summary="cell division phases"),
    ]
    extractor = ScriptedEntryExtractor(drafts=drafts)

    state: IngestionState = {
        "transcript": "today I covered photosynthesis and mitosis",
        "user_id": 1,
        "session": None,
        "drafts": [],
        "entries": [],
        "error": None,
    }
    result = extract_entries(state, extractor=extractor)

    assert extractor.calls == [state["transcript"]]
    assert len(result["drafts"]) == 2
    assert {d.topic for d in result["drafts"]} == {"Photosynthesis", "Mitosis"}
    assert result["error"] is None


def test_extract_entries_sets_error_on_extractor_failure():
    extractor = ScriptedEntryExtractor(error=RuntimeError("simulated provider failure"))

    state: IngestionState = {"transcript": "...", "user_id": 1, "session": None, "drafts": [], "entries": [], "error": None}
    result = extract_entries(state, extractor=extractor)

    assert result["error"] is not None


@pytest.mark.parametrize("transcript", ["", " \n\t"])
def test_extract_entries_skips_extractor_for_blank_transcript(transcript):
    extractor = ScriptedEntryExtractor(error=AssertionError("Blank transcripts must not reach inference"))
    previous = StudyEntryDraft(topic="Old topic", summary="From an earlier attempt")
    state: IngestionState = {
        "transcript": transcript,
        "user_id": 1,
        "session": None,
        "drafts": [previous],
        "entries": [],
        "error": None,
    }

    result = extract_entries(state, extractor=extractor)

    assert extractor.calls == []
    assert result["drafts"] == []
    assert result["error"]
    assert state["drafts"] == [previous]


def test_extract_entries_clears_stale_drafts_on_failure():
    extractor = ScriptedEntryExtractor(error=RuntimeError("private configuration details"))
    state: IngestionState = {
        "transcript": "I studied cell division",
        "user_id": 1,
        "session": None,
        "drafts": [StudyEntryDraft(topic="Old topic", summary="Old summary")],
        "entries": [],
        "error": None,
    }

    result = extract_entries(state, extractor=extractor)

    assert result["drafts"] == []
    assert result["error"] == "Could not process this transcript. Please try again."


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


def test_build_ingestion_graph_end_to_end_persists_expected_rows(session, seeded_user):
    drafts = [StudyEntryDraft(topic="Photosynthesis", summary="light + dark reactions")]
    extractor = ScriptedEntryExtractor(drafts=drafts)

    graph = build_ingestion_graph(extractor=extractor)
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


def test_build_ingestion_graph_routes_to_clarification_on_empty_transcript(session, seeded_user):
    extractor = ScriptedEntryExtractor()

    graph = build_ingestion_graph(extractor=extractor)
    result = graph.invoke(
        {"transcript": "", "user_id": seeded_user.id, "drafts": [], "entries": [], "error": None, "session": session}
    )

    assert result["entries"] == []
    assert result["error"] is not None
    assert extractor.calls == []


def test_build_ingestion_graph_routes_extractor_failure_to_clarification(session, seeded_user):
    extractor = ScriptedEntryExtractor(error=RuntimeError("simulated provider failure"))
    graph = build_ingestion_graph(extractor=extractor)
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
