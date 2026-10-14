"""SRA-204 behavior tests: fake the device/HTTP pipeline, keep WAV ownership real."""

import struct
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from sra.capture import recording
from sra.capture.recording import Recorder, RecordingError

pytestmark = pytest.mark.phase2


class FakeStream:
    """RawInputStream-shaped source that emits deterministic signed 16-bit PCM."""

    def __init__(self, callback, channels):
        self.callback = callback
        self.channels = channels
        self.started = False
        self.closed = False
        self.start_error = None
        self.stop_error = None

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True

    def emit(self, pcm, *, status=False):
        assert self.started and not self.closed
        self.callback(pcm, len(pcm) // (2 * self.channels), None, status)

    def stop(self):
        self.started = False
        if self.stop_error:
            raise self.stop_error

    def close(self):
        self.started = False
        self.closed = True


@pytest.fixture
def audio_source(monkeypatch):
    streams = []

    def open_stream(*, sample_rate, channels, callback):
        stream = FakeStream(callback, channels)
        streams.append(stream)
        return stream

    factory = Mock(side_effect=open_stream)
    monkeypatch.setattr(recording, "_open_audio_stream", factory)
    return factory, streams


def test_audio_adapter_requests_raw_signed_16_bit_pcm_without_starting_device(monkeypatch):
    import sys

    stream = Mock()
    raw_input_stream = Mock(return_value=stream)
    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=raw_input_stream))
    callback = Mock()
    actual = recording._open_audio_stream(sample_rate=22050, channels=2, callback=callback)
    assert actual is stream
    raw_input_stream.assert_called_once_with(samplerate=22050, channels=2, dtype="int16", callback=callback)
    stream.start.assert_not_called()


@pytest.mark.parametrize("sample_rate,channels", [(16000, 1), (44100, 2)])
def test_stop_finalizes_readable_wav_and_close_removes_only_owned_audio(tmp_path, audio_source, sample_rate, channels):
    factory, streams = audio_source
    user_audio = tmp_path / "my-existing-note.wav"
    user_audio.write_bytes(b"keep this user file")
    pcm = struct.pack("<hhhhhhhh", -32768, -100, 0, 100, 32767, 4, -2, 1)

    with Recorder(temp_dir=tmp_path, sample_rate=sample_rate, channels=channels) as recorder:
        factory.assert_not_called()
        recorder.start()
        assert streams[0].started
        # A real callback buffer may be overwritten immediately after it returns.
        first_chunk = bytearray(pcm[:8])
        streams[0].emit(first_chunk)
        first_chunk[:] = bytes(8)
        streams[0].emit(pcm[8:])
        path = recorder.stop()

        assert isinstance(path, Path)
        assert path.parent == tmp_path
        assert path != user_audio
        assert streams[0].closed
        assert factory.call_args.kwargs["sample_rate"] == sample_rate
        assert factory.call_args.kwargs["channels"] == channels
        with wave.open(str(path), "rb") as wav:
            assert wav.getframerate() == sample_rate
            assert wav.getnchannels() == channels
            assert wav.getsampwidth() == 2
            assert wav.getnframes() == len(pcm) // (2 * channels)
            assert wav.readframes(wav.getnframes()) == pcm
        assert path.exists(), "Pipeline must be able to read the finalized WAV before close"

    assert not path.exists()
    assert user_audio.read_bytes() == b"keep this user file"


def test_recorders_own_unique_paths_and_cannot_delete_each_others_audio(tmp_path, audio_source):
    _, streams = audio_source
    first = Recorder(temp_dir=tmp_path)
    second = Recorder(temp_dir=tmp_path)
    try:
        first.start()
        streams[0].emit(struct.pack("<hh", 1, 2))
        first_path = first.stop()
        second.start()
        streams[1].emit(struct.pack("<hh", 3, 4))
        second_path = second.stop()
        assert first_path != second_path
        first.close()
        assert not first_path.exists()
        assert second_path.exists()
    finally:
        first.close()
        second.close()
    assert not list(tmp_path.iterdir())


def test_invalid_transitions_do_not_acquire_extra_streams(tmp_path, audio_source):
    factory, streams = audio_source
    recorder = Recorder(temp_dir=tmp_path)
    with pytest.raises(RecordingError):
        recorder.stop()
    factory.assert_not_called()
    recorder.start()
    with pytest.raises(RecordingError):
        recorder.start()
    assert factory.call_count == 1
    assert streams[0].started
    recorder.stop()
    with pytest.raises(RecordingError):
        recorder.stop()
    recorder.close()
    recorder.close()
    with pytest.raises(RecordingError):
        recorder.start()
    assert factory.call_count == 1
    assert not list(tmp_path.iterdir())


def test_close_before_start_is_idempotent_and_never_opens_device(tmp_path, audio_source):
    factory, _ = audio_source
    recorder = Recorder(temp_dir=tmp_path)
    recorder.close()
    recorder.close()
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("sample_rate,channels", [(0, 1), (-1, 1), (16000, 0), (16000, -1)])
def test_invalid_capture_format_is_rejected_before_device_access(tmp_path, audio_source, sample_rate, channels):
    factory, _ = audio_source
    with pytest.raises(ValueError):
        Recorder(temp_dir=tmp_path, sample_rate=sample_rate, channels=channels)
    factory.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("failure", [RuntimeError("processing failed"), KeyboardInterrupt()])
def test_context_exit_cleans_unfinished_capture_and_preserves_original_exception(tmp_path, audio_source, failure):
    _, streams = audio_source
    recorder = Recorder(temp_dir=tmp_path)
    with pytest.raises(type(failure)) as caught, recorder:
        recorder.start()
        streams[0].emit(struct.pack("<hh", 1, 2))
        raise failure
    assert caught.value is failure
    assert streams[0].closed
    assert not list(tmp_path.iterdir())


def test_context_keeps_finalized_wav_for_consumer_then_cleans_up_on_consumer_failure(tmp_path, audio_source):
    _, streams = audio_source
    failure = RuntimeError("ingestion failed")
    recorder = Recorder(temp_dir=tmp_path)
    with pytest.raises(RuntimeError) as caught, recorder:
        recorder.start()
        streams[0].emit(struct.pack("<hh", 1, 2))
        path = recorder.stop()
        assert path.is_file()
        raise failure
    assert caught.value is failure
    assert streams[0].closed
    assert not path.exists()


@pytest.mark.parametrize("stage", ["open", "start"])
def test_device_failure_is_typed_cleans_partial_resources_and_next_capture_works(tmp_path, audio_source, stage):
    factory, streams = audio_source
    original_factory = factory.side_effect
    failure = OSError("microphone permission denied")

    def fail_once(**kwargs):
        if stage == "open":
            raise failure
        stream = original_factory(**kwargs)
        stream.start_error = failure
        return stream

    factory.side_effect = fail_once
    with pytest.raises(RecordingError, match="permission denied"), Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
    assert all(stream.closed for stream in streams)
    assert not list(tmp_path.iterdir())

    factory.side_effect = original_factory
    with Recorder(temp_dir=tmp_path) as retry:
        retry.start()
        streams[-1].emit(struct.pack("<hh", 1, 2))
        assert retry.stop().is_file()
    assert not list(tmp_path.iterdir())


def test_stop_failure_closes_device_and_discards_unusable_audio(tmp_path, audio_source):
    _, streams = audio_source
    with Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
        streams[0].stop_error = OSError("device disconnected")
        with pytest.raises(RecordingError, match="disconnected"):
            recorder.stop()
        assert streams[0].closed
        assert not list(tmp_path.iterdir())


def test_callback_capture_error_cannot_be_reported_as_successful_wav(tmp_path, audio_source):
    _, streams = audio_source
    with Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
        streams[0].emit(struct.pack("<hh", 1, 2), status="input overflow")
        with pytest.raises(RecordingError, match="(?i)overflow|capture"):
            recorder.stop()
    assert streams[0].closed
    assert not list(tmp_path.iterdir())


def test_stopped_recorder_cannot_restart_or_lose_its_owned_path(tmp_path, audio_source):
    factory, _ = audio_source
    with Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
        path = recorder.stop()
        with pytest.raises(RecordingError):
            recorder.start()
        assert path.exists()
        assert factory.call_count == 1
    assert not path.exists()


def test_interrupted_start_cleans_partial_resources_without_relying_on_context(tmp_path, audio_source):
    factory, streams = audio_source
    original_factory = factory.side_effect

    def interrupt_start(**kwargs):
        stream = original_factory(**kwargs)
        stream.start_error = KeyboardInterrupt()
        return stream

    factory.side_effect = interrupt_start
    recorder = Recorder(temp_dir=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        recorder.start()
    assert streams[0].closed
    assert not list(tmp_path.iterdir())
    recorder.close()


@pytest.mark.parametrize("failure", [RuntimeError("pipeline failed"), KeyboardInterrupt()])
def test_shutdown_failure_does_not_mask_original_exception_or_leave_audio(tmp_path, audio_source, failure):
    _, streams = audio_source
    with pytest.raises(type(failure)) as caught, Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
        streams[0].stop_error = OSError("device shutdown failed")
        raise failure
    assert caught.value is failure
    assert streams[0].closed
    assert not list(tmp_path.iterdir())


def test_close_failure_discards_audio_and_can_retry_device_cleanup(tmp_path, audio_source):
    _, streams = audio_source
    recorder = Recorder(temp_dir=tmp_path)
    recorder.start()
    real_close = streams[0].close
    streams[0].close = Mock(side_effect=OSError("device close failed"))
    with pytest.raises(RecordingError, match="device close failed"):
        recorder.stop()
    assert not list(tmp_path.iterdir())
    streams[0].close = real_close
    recorder.close()
    assert streams[0].closed


def test_wav_write_failure_releases_device_and_discards_partial_file(tmp_path, audio_source, monkeypatch):
    _, streams = audio_source
    recorder = Recorder(temp_dir=tmp_path)
    recorder.start()
    streams[0].emit(struct.pack("<hh", 1, 2))
    monkeypatch.setattr(wave.Wave_write, "writeframesraw", Mock(side_effect=OSError("disk full")))
    with pytest.raises(RecordingError, match="disk full"):
        recorder.stop()
    assert streams[0].closed
    assert not list(tmp_path.iterdir())


def test_callback_buffer_failure_is_reported_on_stop(tmp_path, audio_source):
    _, streams = audio_source
    with Recorder(temp_dir=tmp_path) as recorder:
        recorder.start()
        streams[0].callback(b"\x00", 1, None, False)
        with pytest.raises(RecordingError, match="incomplete PCM"):
            recorder.stop()
    assert streams[0].closed
    assert not list(tmp_path.iterdir())


class TerminalRecorder:
    """Fake only the recorder boundary for terminal interaction tests."""

    def __init__(self, path):
        self.path = path
        self.started = False
        self.stopped = False
        self.closed = False
        self.start_error = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True
        self.path.write_bytes(b"unfinished capture")

    def stop(self):
        assert self.started and not self.stopped
        self.stopped = True
        with wave.open(str(self.path), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(struct.pack("<hh", 42, -42))
        return self.path

    def close(self):
        self.closed = True
        self.path.unlink(missing_ok=True)


@pytest.fixture
def terminal_capture(monkeypatch, tmp_path):
    recorder = TerminalRecorder(tmp_path / "terminal.wav")
    monkeypatch.setattr(recording, "Recorder", lambda: recorder)
    monkeypatch.setattr("builtins.input", Mock(side_effect=["", ""]))
    process = Mock(return_value=[])
    monkeypatch.setattr(recording, "process_recording", process)
    return recorder, process


@pytest.mark.parametrize("entries", [[{"id": 73, "topic": "Mitosis"}, {"id": 74, "topic": "Meiosis"}], []])
def test_terminal_processes_one_finalized_wav_keeps_it_until_return_and_reports_result(terminal_capture, capsys, entries):
    recorder, process = terminal_capture

    def consume(path):
        assert path == recorder.path
        assert recorder.stopped and not recorder.closed
        with wave.open(str(path), "rb") as wav:
            assert wav.getnframes() == 2
        return entries

    process.side_effect = consume
    assert recording.main() == 0
    process.assert_called_once_with(recorder.path)
    output = capsys.readouterr()
    messages = (output.out + output.err).lower()
    assert "recording" in messages
    assert "processing" in messages
    if entries:
        for entry in entries:
            assert str(entry["id"]) in messages
            assert entry["topic"].lower() in messages
    else:
        assert "no speech" in messages
    assert recorder.closed
    assert not recorder.path.exists()


@pytest.mark.parametrize("cancel", [KeyboardInterrupt, EOFError])
@pytest.mark.parametrize("before_start", [True, False])
def test_terminal_cancellation_never_submits_unfinished_audio(terminal_capture, monkeypatch, cancel, before_start):
    recorder, process = terminal_capture
    inputs: list[Any] = [cancel()] if before_start else ["", cancel()]
    monkeypatch.setattr("builtins.input", Mock(side_effect=inputs))
    assert recording.main() == 130
    process.assert_not_called()
    assert not recorder.path.exists()
    if not before_start:
        assert recorder.closed


def test_terminal_capture_failure_reports_error_and_never_submits(terminal_capture, capsys):
    recorder, process = terminal_capture
    recorder.start_error = RecordingError("microphone permission denied")
    assert recording.main() == 1
    process.assert_not_called()
    output = capsys.readouterr()
    assert "permission denied" in output.out + output.err
    assert recorder.closed
    assert not recorder.path.exists()


def test_terminal_pipeline_failure_keeps_unknown_save_message_and_does_not_retry(terminal_capture, capsys):
    from sra.client.api_client import ApiClientError

    recorder, process = terminal_capture
    process.side_effect = ApiClientError("Request timed out; save outcome is unknown. Check search before trying again.")
    assert recording.main() == 1
    process.assert_called_once_with(recorder.path)
    output = capsys.readouterr()
    messages = (output.out + output.err).lower()
    assert "save outcome is unknown" in messages
    assert "saved" not in messages
    assert recorder.closed
    assert not recorder.path.exists()


def test_terminal_transcription_failure_reports_error_and_cleans_up(terminal_capture, capsys):
    from sra.capture.transcription import TranscriptionError

    recorder, process = terminal_capture
    process.side_effect = TranscriptionError("audio could not be decoded")
    assert recording.main() == 1
    process.assert_called_once_with(recorder.path)
    output = capsys.readouterr()
    messages = (output.out + output.err).lower()
    assert "audio could not be decoded" in messages
    assert "saved" not in messages
    assert recorder.closed
    assert not recorder.path.exists()


def test_terminal_interruption_during_processing_releases_audio(terminal_capture):
    recorder, process = terminal_capture
    process.side_effect = KeyboardInterrupt()
    assert recording.main() == 130
    process.assert_called_once_with(recorder.path)
    assert recorder.closed
    assert not recorder.path.exists()
