"""SRA-204/206: terminal → real recorder/WAV/pipeline/client → API and storage.

Only microphone PCM, terminal input, model inference, clock, and HTTP location
are controlled. Recorder, main, transcription, pipeline, routes, and DB are real.
These tests cover application composition; physical device checks remain manual.
"""

import struct
from types import SimpleNamespace

import pytest
from sqlmodel import select

from sra.capture import recording
from sra.db.models import ReviewSchedule, StudyEntry

pytestmark = pytest.mark.phase2


@pytest.fixture
def terminal_source(monkeypatch, tmp_path):
    owned_directory = tmp_path / "recordings"
    owned_directory.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(owned_directory))
    source = SimpleNamespace(
        directory=owned_directory, streams=[], inputs=0,
        pcm=struct.pack("<hhhh", -1000, 0, 1000, 42) * 400,
        cancel_on_stop=False,
    )

    class MicrophoneStream:
        def __init__(self, callback, channels):
            self.callback = callback
            self.channels = channels
            self.started = False
            self.closed = False

        def start(self):
            assert not self.started and not self.closed
            self.started = True

        def stop(self):
            self.started = False

        def close(self):
            self.closed = True
            self.started = False

    def open_stream(*, sample_rate, channels, callback):
        assert sample_rate == 16000 and channels == 1
        stream = MicrophoneStream(callback, channels)
        source.streams.append(stream)
        return stream

    def press_enter(prompt=""):
        source.inputs += 1
        if source.inputs == 1:
            assert source.streams == [], "The first Enter authorizes microphone access"
            return ""
        assert source.inputs == 2, "One invocation records once; it must not retry automatically"
        assert len(source.streams) == 1
        stream = source.streams[0]
        assert stream.started and not stream.closed
        stream.callback(source.pcm, len(source.pcm) // (2 * stream.channels), None, False)
        if source.cancel_on_stop:
            raise KeyboardInterrupt
        return ""

    monkeypatch.setattr(recording, "_open_audio_stream", open_stream)
    monkeypatch.setattr("builtins.input", press_enter)
    return source


def assert_owned_audio_released(source, whisper):
    assert len(source.streams) == 1
    assert source.streams[0].closed
    assert list(source.directory.iterdir()) == []
    for observed in whisper.audio_reads:
        assert observed["frames"] == source.pcm
        assert observed["sample_rate"] == 16000
        assert observed["channels"] == 1
        assert observed["sample_width"] == 2
        assert not observed["path"].exists()


def test_terminal_capture_saves_exact_new_ids_and_cleans_up_after_transcription(
    capture_api, whisper_boundary, terminal_source, client, assert_saved_capture, capsys,
):
    assert recording.main() == 0

    response = client.get("/api/v1/search", params={"q": ""})
    assert response.status_code == 200
    entries = response.json()
    assert_saved_capture(entries, "Today I studied photosynthesis and mitosis.")
    assert len(whisper_boundary.audio_reads) == 1
    assert whisper_boundary.sizes == ["tiny"]
    assert len(capture_api.requests) == len(capture_api.clients) == 1
    assert capture_api.clients[0].is_closed
    assert_owned_audio_released(terminal_source, whisper_boundary)
    output = capsys.readouterr()
    messages = (output.out + output.err).lower()
    assert "recording" in messages and "processing" in messages
    for entry in entries:
        assert str(entry["id"]) in messages
        assert entry["topic"].lower() in messages


def test_terminal_silence_cleans_up_without_http_or_saved_entries(
    capture_api, whisper_boundary, terminal_source, session, capsys,
):
    whisper_boundary.segments = []

    assert recording.main() == 0

    assert len(whisper_boundary.audio_reads) == 1
    assert capture_api.requests == []
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert_owned_audio_released(terminal_source, whisper_boundary)
    output = capsys.readouterr()
    assert "no speech" in (output.out + output.err).lower()


def test_terminal_lost_response_reports_unknown_outcome_and_releases_audio_without_retry(
    capture_api, whisper_boundary, terminal_source, client, assert_saved_capture, capsys,
):
    capture_api.lose_response = True

    assert recording.main() == 1

    # The terminal reports failure, yet the server really saved the note once.
    response = client.get("/api/v1/search", params={"q": ""})
    assert response.status_code == 200
    assert_saved_capture(response.json(), "Today I studied photosynthesis and mitosis.")
    assert len(capture_api.requests) == len(capture_api.clients) == 1
    assert capture_api.clients[0].is_closed
    assert len(whisper_boundary.audio_reads) == 1
    assert_owned_audio_released(terminal_source, whisper_boundary)
    output = capsys.readouterr()
    message = (output.out + output.err).lower()
    assert any(word in message for word in ("may", "might", "unknown", "uncertain"))
    assert any(word in message for word in ("saved", "stored", "committed", "outcome"))


def test_terminal_cancel_mid_capture_releases_resources_without_transcription_or_submission(
    capture_api, whisper_boundary, terminal_source, session,
):
    terminal_source.cancel_on_stop = True

    assert recording.main() == 130

    assert whisper_boundary.sizes == []
    assert whisper_boundary.audio_reads == []
    assert capture_api.requests == []
    assert session.exec(select(StudyEntry)).all() == []
    assert session.exec(select(ReviewSchedule)).all() == []
    assert_owned_audio_released(terminal_source, whisper_boundary)
