"""SRA-203 contract: composition and failure handling, with both edges isolated."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sra.capture import pipeline
from sra.capture.transcription import TranscriptionError
from sra.client.api_client import ApiClientError

pytestmark = pytest.mark.phase2


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "user-owned.wav"
    original = b"The transcription boundary is replaced in these unit tests."
    path.write_bytes(original)
    yield path
    assert path.is_file(), "The caller owns cleanup, including after failure"
    assert path.read_bytes() == original


@pytest.fixture
def boundaries(monkeypatch):
    settings = Mock(return_value=SimpleNamespace(whisper_model_size="small"))
    transcribe = Mock(return_value="A study note.")
    ingest = Mock(return_value=[{"id": 41, "topic": "Photosynthesis", "summary": "Light powers sugar production."}])
    monkeypatch.setattr(pipeline, "get_client_settings", settings)
    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline.api_client, "ingest_transcript", ingest)
    return settings, transcribe, ingest


def test_configured_transcription_is_submitted_once_and_entries_returned(boundaries, audio_file):
    settings, transcribe, ingest = boundaries
    transcript = "  Photosynthesis uses sunlight.\nOxygen is released.  "
    transcribe.return_value = transcript

    assert pipeline.process_recording(audio_file) == ingest.return_value

    settings.assert_called_once_with()
    transcribe.assert_called_once_with(audio_file, model_size="small")
    ingest.assert_called_once_with(transcript)


@pytest.mark.parametrize("transcript", ["", " ", "\n\t\r  "])
def test_no_speech_returns_no_entries_without_submission(boundaries, audio_file, transcript):
    _, transcribe, ingest = boundaries
    transcribe.return_value = transcript

    assert pipeline.process_recording(audio_file) == []

    transcribe.assert_called_once_with(audio_file, model_size="small")
    ingest.assert_not_called()


def test_transcription_failure_propagates_without_submission_or_retry(boundaries, audio_file):
    _, transcribe, ingest = boundaries
    failure = TranscriptionError("invalid audio")
    failure.__cause__ = ValueError("decoder rejected the input")
    transcribe.side_effect = failure

    with pytest.raises(TranscriptionError) as raised:
        pipeline.process_recording(audio_file)

    assert raised.value is failure
    transcribe.assert_called_once()
    ingest.assert_not_called()


def test_api_failure_propagates_without_retry(boundaries, audio_file):
    _, transcribe, ingest = boundaries
    failure = ApiClientError("server unavailable")
    failure.__cause__ = ConnectionError("connection refused")
    ingest.side_effect = failure

    with pytest.raises(ApiClientError) as raised:
        pipeline.process_recording(audio_file)

    assert raised.value is failure
    transcribe.assert_called_once()
    ingest.assert_called_once_with("A study note.")


def test_empty_ingestion_result_is_returned_without_retry(boundaries, audio_file):
    _, _, ingest = boundaries
    ingest.return_value = []

    assert pipeline.process_recording(audio_file) == []
    ingest.assert_called_once_with("A study note.")


def test_a_later_call_can_recover_from_transcription_failure(boundaries, audio_file):
    _, transcribe, ingest = boundaries
    transcribe.side_effect = [TranscriptionError("temporary decoder failure"), "Recovered note."]

    with pytest.raises(TranscriptionError):
        pipeline.process_recording(audio_file)
    ingest.assert_not_called()
    assert pipeline.process_recording(audio_file) == ingest.return_value

    assert transcribe.call_count == 2
    ingest.assert_called_once_with("Recovered note.")


def test_a_later_call_can_recover_from_api_failure(boundaries, audio_file):
    _, transcribe, ingest = boundaries
    entries = [{"id": 42, "topic": "Recovered", "summary": "The server is reachable."}]
    ingest.side_effect = [ApiClientError("server unavailable"), entries]

    with pytest.raises(ApiClientError):
        pipeline.process_recording(audio_file)
    assert ingest.call_count == 1
    assert pipeline.process_recording(audio_file) == entries

    assert transcribe.call_count == 2
    assert ingest.call_count == 2
