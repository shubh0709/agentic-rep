"""SRA-202 contract: fake inference, real files, and no optional dependencies."""

import subprocess
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sra.capture import transcription
from sra.capture.transcription import TranscriptionError

pytestmark = pytest.mark.phase2


@dataclass
class Segment:
    text: str


@pytest.fixture(autouse=True)
def clear_model_cache():
    transcription.load_model.cache_clear()
    yield
    transcription.load_model.cache_clear()


@pytest.fixture
def audio_file(tmp_path):
    """A valid small WAV; every caller must preserve its exact bytes."""
    path = tmp_path / "recording.wav"
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16000)
        recording.writeframes(b"\x00\x00" * 1600)
    original = path.read_bytes()
    yield path
    assert path.is_file(), "Transcription must not remove caller-owned audio"
    assert path.read_bytes() == original, "Transcription must not mutate audio"


def fake_inference(monkeypatch, segments):
    model = Mock()
    model.transcribe.return_value = (iter(segments), object())
    loader = Mock(return_value=model)
    monkeypatch.setattr(transcription, "load_model", loader)
    return model, loader


def test_import_does_not_require_optional_whisper_package():
    script = (
        "import sys; sys.modules['faster_whisper'] = None; "
        "from sra.capture.transcription import load_model, transcribe; "
        "assert callable(load_model) and callable(transcribe)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_spoken_fixture_contains_actual_audio_frames():
    path = Path(__file__).resolve().parents[1] / "fixtures" / "study_note.wav"
    with wave.open(str(path), "rb") as recording:
        assert recording.getnchannels() == 1
        assert recording.getsampwidth() == 2
        assert recording.getframerate() == 16000
        assert recording.getnframes() > 3 * recording.getframerate()
        assert any(recording.readframes(recording.getnframes())), "A silent/empty fixture cannot test speech recognition"


def test_factory_constructs_optional_whisper_with_requested_model(monkeypatch):
    model = Mock()
    constructor = Mock(return_value=model)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=constructor))

    assert transcription._create_whisper_model("small") is model

    constructor.assert_called_once()
    args, kwargs = constructor.call_args
    requested_size = args[0] if args else kwargs.get("model_size_or_path")
    assert requested_size == "small"
    # Device/compute settings are implementation choices; the test allows them.


def test_load_model_caches_each_size_independently(monkeypatch):
    base, tiny = Mock(), Mock()
    factory = Mock(side_effect=[base, tiny])
    monkeypatch.setattr(transcription, "_create_whisper_model", factory)

    assert transcription.load_model("base") is base
    assert transcription.load_model("base") is base
    assert transcription.load_model("tiny") is tiny
    assert transcription.load_model("tiny") is tiny
    assert transcription.load_model("base") is base
    assert [call.args for call in factory.call_args_list] == [("base",), ("tiny",)]


def test_model_construction_failure_is_typed_and_is_not_cached(monkeypatch):
    cause = RuntimeError("model weights unavailable")
    recovered_model = Mock()
    factory = Mock(side_effect=[cause, recovered_model])
    monkeypatch.setattr(transcription, "_create_whisper_model", factory)

    with pytest.raises(TranscriptionError) as raised:
        transcription.load_model("base")
    assert raised.value.__cause__ is cause
    assert str(raised.value).strip()
    assert transcription.load_model("base") is recovered_model
    assert transcription.load_model("base") is recovered_model
    assert factory.call_count == 2


def test_transcribe_consumes_segments_in_order_and_uses_requested_model(monkeypatch, audio_file):
    consumed = []

    def segments():
        for text in ["  Photosynthesis ", "\t", "uses  sunlight.\n", " Oxygen is released. "]:
            consumed.append(text)
            yield Segment(text)

    model, loader = fake_inference(monkeypatch, segments())

    assert transcription.transcribe(audio_file, model_size="tiny") == "Photosynthesis uses  sunlight. Oxygen is released."
    assert len(consumed) == 4
    loader.assert_called_once_with("tiny")
    model.transcribe.assert_called_once_with(str(audio_file))


def test_transcribe_defaults_to_base_model(monkeypatch, audio_file):
    _, loader = fake_inference(monkeypatch, [Segment("A study note.")])

    assert transcription.transcribe(audio_file) == "A study note."
    loader.assert_called_once_with("base")


@pytest.mark.parametrize("segments", [[], [Segment(""), Segment(" \n\t")]])
def test_no_speech_returns_empty_text(monkeypatch, audio_file, segments):
    model, _ = fake_inference(monkeypatch, segments)

    assert transcription.transcribe(audio_file) == ""
    model.transcribe.assert_called_once()


def test_missing_file_fails_before_loading_model(monkeypatch, tmp_path):
    loader = Mock()
    monkeypatch.setattr(transcription, "load_model", loader)
    missing = tmp_path / "missing.wav"

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(missing)

    assert isinstance(raised.value.__cause__, FileNotFoundError)
    assert missing.name in str(raised.value)
    loader.assert_not_called()
    assert not missing.exists()


def test_directory_is_not_treated_as_audio(monkeypatch, tmp_path):
    loader = Mock()
    monkeypatch.setattr(transcription, "load_model", loader)

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(tmp_path)

    assert isinstance(raised.value.__cause__, (IsADirectoryError, ValueError))
    loader.assert_not_called()
    assert tmp_path.is_dir()


def test_unreadable_file_is_typed_before_model_loading(monkeypatch, audio_file):
    loader = Mock()
    monkeypatch.setattr(transcription, "load_model", loader)
    cause = PermissionError("recording is unreadable")
    original_open = Path.open

    def denied_open(path, *args, **kwargs):
        if path == audio_file:
            raise cause
        return original_open(path, *args, **kwargs)

    # Deterministic under elevated users too: chmod-based tests can still read.
    with monkeypatch.context() as context:
        context.setattr(Path, "open", denied_open)
        with pytest.raises(TranscriptionError) as raised:
            transcription.transcribe(audio_file)

    assert raised.value.__cause__ is cause
    assert audio_file.name in str(raised.value)
    loader.assert_not_called()


def test_corrupt_audio_decoder_failure_preserves_file_and_cause(monkeypatch, tmp_path):
    path = tmp_path / "corrupt.wav"
    original = b"This is deliberately not a WAV file."
    path.write_bytes(original)
    cause = ValueError("invalid audio data")
    model, _ = fake_inference(monkeypatch, [])
    model.transcribe.side_effect = cause

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(path)

    assert raised.value.__cause__ is cause
    assert str(raised.value).strip()
    assert path.read_bytes() == original
    model.transcribe.assert_called_once_with(str(path))


def test_model_loading_error_reaches_caller_unchanged(monkeypatch, audio_file):
    failure = TranscriptionError("cannot load local model")
    failure.__cause__ = RuntimeError("bad model weights")
    loader = Mock(side_effect=failure)
    monkeypatch.setattr(transcription, "load_model", loader)

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(audio_file)

    assert raised.value is failure
    loader.assert_called_once_with("base")


def test_inference_start_failure_is_typed_and_not_retried(monkeypatch, audio_file):
    cause = RuntimeError("inference failed")
    model, _ = fake_inference(monkeypatch, [])
    model.transcribe.side_effect = cause

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(audio_file)

    assert raised.value.__cause__ is cause
    model.transcribe.assert_called_once()


def test_lazy_segment_failure_does_not_return_partial_transcript(monkeypatch, audio_file):
    cause = RuntimeError("decoder failed in the second segment")

    def broken_segments():
        yield Segment("A partial result must never escape.")
        raise cause

    model, _ = fake_inference(monkeypatch, broken_segments())

    with pytest.raises(TranscriptionError) as raised:
        transcription.transcribe(audio_file)

    assert raised.value.__cause__ is cause
    model.transcribe.assert_called_once()
