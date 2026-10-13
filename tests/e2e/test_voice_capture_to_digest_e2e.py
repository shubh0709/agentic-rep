"""SRA-203/206: WAV → real transcription/pipeline/client → API → due digest.

Real Whisper inference is covered separately by the opt-in integration test.
Physical microphone, global hotkey delivery, and macOS permissions need the
manual desktop scenarios in tickets SRA-204 through SRA-206.
"""

import json
import wave
from datetime import date, datetime, timedelta

import pytest
from sqlmodel import select

from sra.capture.pipeline import process_recording
from sra.capture.transcription import TranscriptionError
from sra.client.api_client import ApiClientError
from sra.db.models import ReviewSchedule, StudyEntry

pytestmark = pytest.mark.phase2


@pytest.fixture
def audio_input(tmp_path):
    audio = tmp_path / "my-recording.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)
    return audio


def test_voice_capture_is_searchable_then_due_tomorrow(
    capture_api, whisper_boundary, audio_input, assert_saved_capture, client, monkeypatch, session,
):
    before = audio_input.read_bytes()

    entries = process_recording(audio_input)

    transcript = "Today I studied photosynthesis and mitosis."
    ids = assert_saved_capture(entries, transcript)
    assert whisper_boundary.sizes == ["tiny"]
    assert whisper_boundary.paths == [str(audio_input)]
    assert len(capture_api.requests) == 1
    assert json.loads(capture_api.requests[0].content) == {"transcript": transcript}
    assert audio_input.read_bytes() == before
    assert len(capture_api.clients) == 1
    assert all(connection.is_closed for connection in capture_api.clients)
    original_dates = {row.id: row.scheduled_date for row in session.exec(select(ReviewSchedule)).all()}

    digest_day = datetime.fromisoformat(entries[0]["created_at"]).date()
    assert digest_day == capture_api.captured_at.date()

    class DigestDate(date):
        @classmethod
        def today(cls):
            return digest_day

    monkeypatch.setattr("sra.api.routers.digest.date", DigestDate)
    response = client.get("/api/v1/digest")
    assert response.status_code == 200
    assert response.json() == []

    digest_day += timedelta(days=1)
    response = client.get("/api/v1/digest")
    assert response.status_code == 200
    due = response.json()
    assert len(due) == 2
    assert {review["entry"]["id"] for review in due} == ids
    assert all(review["interval_label"] == "1d" and review["status"] == "pending" for review in due)
    assert all(review["scheduled_date"] == digest_day.isoformat() for review in due)
    assert {row.id: row.scheduled_date for row in session.exec(select(ReviewSchedule)).all()} == original_dates


@pytest.mark.parametrize("segments", [[], ["  ", "\n\t"]])
def test_silence_never_reaches_http_or_database(capture_api, whisper_boundary, audio_input, session, segments):
    whisper_boundary.segments = segments
    before = audio_input.read_bytes()

    assert process_recording(audio_input) == []

    assert capture_api.requests == []
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert audio_input.read_bytes() == before


def test_late_transcription_failure_prevents_partial_submission_and_recovers(
    capture_api, whisper_boundary, audio_input, session, assert_saved_capture,
):
    whisper_boundary.error = RuntimeError("Decoder failed after its first segment")
    before = audio_input.read_bytes()

    with pytest.raises(TranscriptionError) as failure:
        process_recording(audio_input)

    assert failure.value.__cause__ is whisper_boundary.error
    assert capture_api.requests == []
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert audio_input.read_bytes() == before

    whisper_boundary.error = None
    assert_saved_capture(process_recording(audio_input), "Today I studied photosynthesis and mitosis.")
    assert len(capture_api.requests) == 1
    assert audio_input.read_bytes() == before
    assert len(capture_api.clients) == 1
    assert all(connection.is_closed for connection in capture_api.clients)


def test_missing_audio_fails_before_model_or_http(capture_api, whisper_boundary, tmp_path, session):
    with pytest.raises(TranscriptionError):
        process_recording(tmp_path / "missing.wav")

    assert whisper_boundary.sizes == []
    assert capture_api.requests == []
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []


def test_voice_server_rejection_preserves_audio_and_recovers(
    capture_api, whisper_boundary, audio_input, session, assert_saved_capture,
):
    valid_drafts = capture_api.drafts
    capture_api.drafts = []
    before = audio_input.read_bytes()

    with pytest.raises(ApiClientError) as failure:
        process_recording(audio_input)

    assert failure.value.status_code == 422
    assert len(capture_api.requests) == 1
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert audio_input.read_bytes() == before
    assert all(connection.is_closed for connection in capture_api.clients)

    capture_api.drafts = valid_drafts
    assert_saved_capture(process_recording(audio_input), "Today I studied photosynthesis and mitosis.")
    assert len(capture_api.requests) == 2
    assert audio_input.read_bytes() == before
    assert len(capture_api.clients) == 2
    assert all(connection.is_closed for connection in capture_api.clients)
