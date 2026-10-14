"""Phase 2 HTTP/model boundaries; application code and persistence stay real."""

import json
import wave
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from dateutil.relativedelta import relativedelta
from sqlmodel import select

from sra.agents.schemas import StudyEntryDraft, StudyEntryDrafts
from sra.db.models import ReviewSchedule, StudyEntry, User


@dataclass
class InProcessCaptureAPI:
    requests: list[httpx.Request] = field(default_factory=list)
    clients: list[httpx.Client] = field(default_factory=list)
    drafts: list[StudyEntryDraft] = field(default_factory=lambda: [
        StudyEntryDraft(topic="Photosynthesis", subtopic="Light reactions", tags=["biology"], summary="Plants use light."),
        StudyEntryDraft(topic="Mitosis", tags=["biology", "cells"], summary="The phases of cell division."),
    ])
    model_error: Exception | None = None
    lose_response: bool = False
    captured_at: datetime = field(default_factory=lambda: datetime(2026, 1, 31, 12, tzinfo=UTC))


@pytest.fixture
def capture_api(client, monkeypatch, tmp_path):
    """Redirect HTTP to FastAPI, fake inference, and control the external clock.

    Neither ingest_transcript, the route, graph, repository, nor scheduler is
    patched. Even the response-loss scenario reaches the server and commits
    before the transport raises, modeling an ambiguous save correctly. A fixed
    month-end clock avoids midnight races and exercises real calendar offsets.
    """
    from sra.client import api_client

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("API_BASE_URL", "http://capture.test/")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "tiny")
    harness = InProcessCaptureAPI()

    class CaptureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return harness.captured_at.replace(tzinfo=None)
            return harness.captured_at.astimezone(tz)

    monkeypatch.setattr("sra.db.models.datetime", CaptureClock)

    class FakeExtractionModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is StudyEntryDrafts
            return self

        def invoke(self, messages):
            submitted = json.loads(harness.requests[-1].content)["transcript"]
            assert any(submitted in str(message) for message in messages)
            if harness.model_error is not None:
                raise harness.model_error
            return StudyEntryDrafts(drafts=harness.drafts)

    def send(request: httpx.Request) -> httpx.Response:
        harness.requests.append(request)
        response = client.request(
            request.method, str(request.url), content=request.content, headers=request.headers,
        )
        if harness.lose_response:
            assert response.status_code == 201, "This scenario loses a successful save response"
            raise httpx.ReadTimeout("Response lost after server processing", request=request)
        return httpx.Response(response.status_code, headers=response.headers, content=response.content, request=request)

    def create_client():
        connection = httpx.Client(transport=httpx.MockTransport(send), timeout=90)
        harness.clients.append(connection)
        return connection

    monkeypatch.setattr(api_client, "_create_http_client", create_client)
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", FakeExtractionModel)
    try:
        with client:
            yield harness
    finally:
        # Fixture cleanup is defensive; tests assert the wrapper closes them first.
        for connection in harness.clients:
            connection.close()


@pytest.fixture
def assert_saved_capture(session, client):
    """Assert observable storage and search results, independent of scheduler code."""
    def check(entries, transcript):
        assert len(entries) == 2
        ids = {entry["id"] for entry in entries}
        assert len(ids) == 2
        users = session.exec(select(User)).all()
        assert len(users) == 1
        assert users[0].email == "local@localhost"
        stored_entries = session.exec(select(StudyEntry)).all()
        assert {entry.id for entry in stored_entries} == ids
        for entry in stored_entries:
            assert entry.raw_transcript == transcript
            assert entry.user_id == users[0].id
            response_entry = next(row for row in entries if row["id"] == entry.id)
            assert response_entry["topic"] == entry.topic
            assert response_entry["summary"] == entry.summary
            assert response_entry["subtopic"] == entry.subtopic
            assert response_entry["tags"] == entry.tags
            # SQLite may return a naive UTC datetime; compare both as UTC.
            assert datetime.fromisoformat(response_entry["created_at"]).replace(tzinfo=UTC) == entry.created_at.replace(tzinfo=UTC)
            reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)).all()
            studied = entry.created_at.date()
            expected_dates = {
                "1d": studied + timedelta(days=1),
                "3d": studied + timedelta(days=3),
                "7d": studied + timedelta(days=7),
                "1mo": studied + relativedelta(months=1),
                "3mo": studied + relativedelta(months=3),
                "6mo": studied + relativedelta(months=6),
            }
            assert len(reviews) == 6
            assert {row.interval_label: row.scheduled_date for row in reviews} == expected_dates
            assert all(row.status == "pending" and row.completed_at is None for row in reviews)
            found = client.get("/api/v1/search", params={"q": entry.topic})
            assert found.status_code == 200
            assert entry.id in {row["id"] for row in found.json()}
        return ids

    return check


@pytest.fixture
def whisper_boundary(monkeypatch):
    """Fake only model construction; real transcription must consume a valid WAV."""
    from sra.capture import transcription

    boundary = SimpleNamespace(
        sizes=[], paths=[], audio_reads=[],
        segments=[" Today I studied photosynthesis", " and mitosis. "], error=None,
    )

    class FakeWhisper:
        def transcribe(self, path, *, vad_filter=False):
            assert vad_filter is True
            boundary.paths.append(path)
            with wave.open(str(path), "rb") as wav:
                boundary.audio_reads.append({
                    "path": Path(path), "sample_rate": wav.getframerate(),
                    "channels": wav.getnchannels(), "sample_width": wav.getsampwidth(),
                    "frames": wav.readframes(wav.getnframes()),
                })

            def segments():
                for text in boundary.segments:
                    yield SimpleNamespace(text=text)
                if boundary.error:
                    raise boundary.error

            return segments(), SimpleNamespace(language="en")

    def create_model(model_size):
        boundary.sizes.append(model_size)
        return FakeWhisper()

    transcription.load_model.cache_clear()
    monkeypatch.setattr(transcription, "_create_whisper_model", create_model)
    yield boundary
    transcription.load_model.cache_clear()
