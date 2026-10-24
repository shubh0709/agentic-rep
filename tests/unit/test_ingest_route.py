from dataclasses import dataclass, field

import pytest
from sqlmodel import select

from sra.agents.schemas import StudyEntryDraft
from sra.db.models import ReviewSchedule, StudyEntry


@dataclass
class ScriptedEntryExtractor:
    drafts: list[StudyEntryDraft] = field(default_factory=list)
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def extract(self, transcript: str) -> list[StudyEntryDraft]:
        self.calls.append(transcript)
        if self.error is not None:
            raise self.error
        return self.drafts


def test_ingest_returns_and_persists_valid_topics_with_reviews(
    client, override_entry_extractor, session, seeded_user
):
    drafts = [
        StudyEntryDraft(topic="Photosynthesis", subtopic="Light reactions", tags=["biology"], summary="Light reactions"),
        StudyEntryDraft(topic=" ", summary="Unusable topic"),
        StudyEntryDraft(topic="Mitosis", summary="Cell division"),
    ]
    extractor = ScriptedEntryExtractor(drafts=drafts)
    override_entry_extractor(extractor)
    transcript = "Today I studied photosynthesis and mitosis."

    response = client.post("/api/v1/entries/ingest", json={"transcript": transcript})

    assert response.status_code == 201
    body = response.json()
    assert [entry["topic"] for entry in body] == ["Photosynthesis", "Mitosis"]
    assert body[0]["subtopic"] == "Light reactions"
    assert body[0]["tags"] == ["biology"]
    assert body[0]["summary"] == "Light reactions"
    assert body[1]["subtopic"] is None
    assert body[1]["tags"] == []
    for entry in body:
        assert set(entry) == {"id", "topic", "subtopic", "tags", "summary", "raw_transcript", "created_at"}
        assert isinstance(entry["id"], int)
        assert entry["created_at"]

    rows = session.exec(select(StudyEntry)).all()
    assert len(rows) == 2
    assert {row.id for row in rows} == {entry["id"] for entry in body}
    assert all(row.user_id == seeded_user.id and row.raw_transcript == transcript for row in rows)
    for row in rows:
        reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == row.id)).all()
        assert len(reviews) == 6
        assert all(review.status == "pending" for review in reviews)

    search_response = client.get("/api/v1/search", params={"q": "mitosis"})
    assert search_response.status_code == 200
    assert [entry["id"] for entry in search_response.json()] == [body[1]["id"]]
    assert extractor.calls == [transcript]


@pytest.mark.parametrize(
    ("transcript", "drafts"),
    [
        ("", []),
        (" \n\t", []),
        ("No useful study information", []),
        ("Incomplete notes", [StudyEntryDraft(topic="Mitosis", summary=" ")]),
    ],
)
def test_ingest_rejects_unusable_transcripts_without_writing_entries(
    client, override_entry_extractor, session, transcript, drafts
):
    extractor = ScriptedEntryExtractor(drafts=drafts)
    override_entry_extractor(extractor)

    response = client.post("/api/v1/entries/ingest", json={"transcript": transcript})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert response.json()["detail"]
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    if not transcript.strip():
        assert extractor.calls == []
    else:
        assert extractor.calls == [transcript]


def test_ingest_returns_useful_error_on_extractor_failure(client, override_entry_extractor, session):
    extractor = ScriptedEntryExtractor(error=RuntimeError("private provider failure details"))
    override_entry_extractor(extractor)

    response = client.post("/api/v1/entries/ingest", json={"transcript": "Today I studied mitosis"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Could not process this transcript. Please try again."}
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []


@pytest.mark.parametrize("body", [{}, {"transcript": None}])
def test_ingest_rejects_malformed_request_body(client, override_entry_extractor, session, body):
    extractor = ScriptedEntryExtractor(error=AssertionError("invalid requests must not reach inference"))
    override_entry_extractor(extractor)

    response = client.post("/api/v1/entries/ingest", json=body)

    assert response.status_code == 422
    assert extractor.calls == []
    assert session.exec(select(StudyEntry)).all() == []
