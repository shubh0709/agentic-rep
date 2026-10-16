from unittest.mock import Mock

import pytest
from sqlmodel import select

from sra.agents.schemas import StudyEntryDraft, StudyEntryDrafts
from sra.db.models import ReviewSchedule, StudyEntry


def test_ingest_returns_and_persists_valid_topics_with_reviews(client, monkeypatch, session, seeded_user):
    drafts = [
        StudyEntryDraft(topic="Photosynthesis", subtopic="Light reactions", tags=["biology"], summary="Light reactions"),
        StudyEntryDraft(topic=" ", summary="Unusable topic"),
        StudyEntryDraft(topic="Mitosis", summary="Cell division"),
    ]
    model = Mock()
    model.with_structured_output.return_value = model
    model.invoke.return_value = StudyEntryDrafts(drafts=drafts)
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", lambda: model)
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


@pytest.mark.parametrize(
    ("transcript", "drafts"),
    [
        ("", []),
        (" \n\t", []),
        ("No useful study information", []),
        ("Incomplete notes", [StudyEntryDraft(topic="Mitosis", summary=" ")]),
    ],
)
def test_ingest_rejects_unusable_transcripts_without_writing_entries(client, monkeypatch, session, transcript, drafts):
    model = Mock()
    model.with_structured_output.return_value = model
    model.invoke.return_value = StudyEntryDrafts(drafts=drafts)
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", lambda: model)

    response = client.post("/api/v1/entries/ingest", json={"transcript": transcript})

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert response.json()["detail"]
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    if not transcript.strip():
        model.invoke.assert_not_called()


def test_ingest_returns_useful_error_on_model_failure(client, monkeypatch, session):
    model = Mock()
    model.with_structured_output.return_value = model
    model.invoke.side_effect = RuntimeError("private provider failure details")
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", lambda: model)

    response = client.post("/api/v1/entries/ingest", json={"transcript": "Today I studied mitosis"})

    assert response.status_code == 422
    assert response.json() == {"detail": "Could not process this transcript. Please try again."}
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []


@pytest.mark.parametrize("body", [{}, {"transcript": None}])
def test_ingest_rejects_malformed_request_body(client, monkeypatch, session, body):
    model_factory = Mock()
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", model_factory)

    response = client.post("/api/v1/entries/ingest", json=body)

    assert response.status_code == 422
    model_factory.assert_not_called()
    assert session.exec(select(StudyEntry)).all() == []
