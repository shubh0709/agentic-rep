"""Opt-in integration: the actual WAV and actual local Whisper inference."""

import importlib.util
import os
from pathlib import Path

import pytest

from sra.capture.transcription import load_model, transcribe

pytestmark = [
    pytest.mark.phase2,
    pytest.mark.whisper,
    pytest.mark.skipif(
        os.getenv("SRA_RUN_WHISPER_TESTS") != "1",
        reason="Set SRA_RUN_WHISPER_TESTS=1 to run real Whisper (may download model weights)",
    ),
]


def test_real_spoken_study_note_contains_expected_keywords():
    assert importlib.util.find_spec("faster_whisper") is not None, (
        "Real Whisper was explicitly requested but is not installed. Run: pip install -e '.[dev,capture]'"
    )
    path = Path(__file__).resolve().parents[1] / "fixtures" / "study_note.wav"
    original = path.read_bytes()
    load_model.cache_clear()
    try:
        result = transcribe(path, model_size=os.getenv("WHISPER_MODEL_SIZE", "base"))
        words = result.casefold()
        for expected in ["photosynthesis", "plants", "sunlight", "oxygen"]:
            assert expected in words, f"Missing {expected!r} in real transcript: {result!r}"
    finally:
        load_model.cache_clear()
        assert path.read_bytes() == original
