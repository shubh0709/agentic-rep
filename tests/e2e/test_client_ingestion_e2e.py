"""SRA-201: real text client → HTTP → route/graph → DB/reviews → search.

Only HTTP location and model inference are substituted. These are system
flows, whereas test_api_client.py isolates the HTTP wrapper from the server.
"""

import json

import httpx
import pytest
from sqlmodel import select

from sra.client.api_client import ApiClientError, ingest_transcript
from sra.db.models import ReviewSchedule, StudyEntry

pytestmark = pytest.mark.phase2


def test_text_client_creates_searchable_entries_with_server_ownership(capture_api, assert_saved_capture):
    transcript = "Today I studied photosynthesis and mitosis. Cells divide — आज का अध्ययन."

    entries = ingest_transcript(transcript)

    assert_saved_capture(entries, transcript)
    assert {entry["topic"] for entry in entries} == {"Photosynthesis", "Mitosis"}
    assert len(capture_api.requests) == 1
    request = capture_api.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v1/entries/ingest"
    assert json.loads(request.content) == {"transcript": transcript}
    assert "authorization" not in request.headers
    assert len(capture_api.clients) == 1
    assert all(connection.is_closed for connection in capture_api.clients)


def test_server_rejection_creates_nothing_and_next_submission_recovers(capture_api, session, assert_saved_capture):
    capture_api.model_error = RuntimeError("Fake extraction unavailable")

    with pytest.raises(ApiClientError) as failure:
        ingest_transcript("Today I studied photosynthesis and mitosis.")

    assert failure.value.status_code == 422
    assert "Could not process" in str(failure.value.detail)
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert len(capture_api.requests) == 1
    assert all(connection.is_closed for connection in capture_api.clients)

    capture_api.model_error = None
    transcript = "Retry by the user: photosynthesis and mitosis."
    assert_saved_capture(ingest_transcript(transcript), transcript)
    assert len(capture_api.requests) == 2
    assert len(capture_api.clients) == 2
    assert all(connection.is_closed for connection in capture_api.clients)


def test_lost_response_reports_uncertainty_without_duplicate_submission(capture_api, session, client, assert_saved_capture):
    capture_api.lose_response = True
    transcript = "Today I studied photosynthesis and mitosis."

    with pytest.raises(ApiClientError) as failure:
        ingest_transcript(transcript)

    # The server saved successfully, but the client never received its response.
    assert any(word in str(failure.value).lower() for word in ("may", "might", "unknown", "uncertain"))
    assert any(word in str(failure.value).lower() for word in ("saved", "stored", "committed", "outcome"))
    assert isinstance(failure.value.__cause__, httpx.ReadTimeout)
    assert len(capture_api.requests) == 1
    rows = session.exec(select(StudyEntry)).all()
    assert len(rows) == 2
    assert len(session.exec(select(ReviewSchedule)).all()) == 12
    recovered = []
    for row in rows:
        found = client.get("/api/v1/search", params={"q": row.topic})
        assert found.status_code == 200
        recovered.append(next(entry for entry in found.json() if entry["id"] == row.id))
    assert_saved_capture(recovered, transcript)
    assert len(capture_api.clients) == 1
    assert all(connection.is_closed for connection in capture_api.clients)
