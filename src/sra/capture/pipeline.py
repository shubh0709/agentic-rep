"""SRA-203 exercise: compose local transcription with remote ingestion."""

from pathlib import Path
from typing import Any

from sra.capture.transcription import transcribe
from sra.client import api_client
from sra.config import get_client_settings


def process_recording(audio_path: Path) -> list[dict[str, Any]]:
    """Submit one recording and return the entries created by the server.

    Learning task: read get_client_settings().whisper_model_size, then call
    transcribe(audio_path, model_size=...) once. If its result is empty or only
    whitespace, return [] without submitting anything. Otherwise pass the
    original transcript to api_client.ingest_transcript once and return the
    entries. Let TranscriptionError and ApiClientError reach the caller unchanged;
    do not retry, turn an error into silence, or clean up this caller-owned file.

    Unit tests isolate these two boundaries. The separate end-to-end tests use
    this function, the real transcription wrapper, and the real HTTP client
    through the API, repository, and scheduler with external inference replaced.
    """
    settings = get_client_settings()
    transcript = transcribe(audio_path, model_size=settings.whisper_model_size)
    if not transcript.strip():
        return []
    return api_client.ingest_transcript(transcript)
