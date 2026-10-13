"""SRA-205: deterministic controller contracts plus an optional desktop import smoke.

These do not prove global key delivery, permissions, or Cocoa event-loop safety;
the ticket's real macOS checks remain required before closing SRA-205.
"""

import importlib.util
import os
import subprocess
import sys
from collections import deque
from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock

import pytest

from sra.capture.menubar_app import CaptureController
from sra.capture.recording import RecordingError

pytestmark = pytest.mark.phase2


class FakeRecorder:
    def __init__(self, path):
        self.path = path
        self.start = Mock(side_effect=self._start)
        self.stop = Mock(side_effect=self._stop)
        self.close = Mock(side_effect=self._close)

    def _start(self):
        self.path.write_bytes(b"recording")

    def _stop(self):
        self.path.write_bytes(b"finalized audio")
        return self.path

    def _close(self):
        self.path.unlink(missing_ok=True)


class ControllerHarness:
    """Explicit worker/UI queues reveal accidentally synchronous or late UI work."""

    def __init__(self, tmp_path):
        self.work: deque[Callable[[], None]] = deque()
        self.ui: deque[Callable[[], None]] = deque()
        self.recorders: list[FakeRecorder] = []
        self.messages: list[str] = []
        self.process = Mock(return_value=[{"id": 51, "topic": "Mitosis"}])
        self.stop_listener = Mock()
        self.in_worker = False

        def make_recorder():
            recorder = FakeRecorder(tmp_path / f"capture-{len(self.recorders)}.wav")
            self.recorders.append(recorder)
            return recorder

        def show_status(message):
            assert not self.in_worker, "UI updates must be dispatched back to its event loop"
            self.messages.append(message)

        self.controller = CaptureController(
            recorder_factory=make_recorder,
            process=self.process,
            submit=self.work.append,
            dispatch=self.ui.append,
            show_status=show_status,
            stop_listener=self.stop_listener,
        )

    def finish_worker(self):
        self.in_worker = True
        try:
            self.work.popleft()()
        finally:
            self.in_worker = False

    def drain_ui(self):
        while self.ui:
            self.ui.popleft()()

    def run_ui_callback(self, callback):
        """Model a UI event interleaving with a paused worker without real threads."""
        was_in_worker = self.in_worker
        self.in_worker = False
        try:
            callback()
        finally:
            self.in_worker = was_in_worker


@pytest.fixture
def make_harness(tmp_path):
    return lambda: ControllerHarness(tmp_path)


def test_import_is_safe_without_optional_desktop_or_audio_libraries():
    # A fresh process avoids a previously imported desktop module hiding a side effect.
    script = """
import importlib.abc
import sys
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sounddevice', 'rumps', 'pynput', 'whisper', 'faster_whisper'}:
            raise AssertionError('Import attempted optional runtime dependency: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
import sra.capture.recording
import sra.capture.menubar_app
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20, env=env)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


@pytest.mark.desktop
def test_desktop_import_smoke_with_real_dependencies_available():
    if sys.platform != "darwin":
        pytest.skip("Desktop import smoke requires macOS; controller tests run on every platform")
    missing = [name for name in ("rumps", "pynput", "sounddevice") if importlib.util.find_spec(name) is None]
    if missing:
        pytest.skip(f"Desktop import smoke requires desktop extra; missing: {', '.join(missing)}")
    from sra.capture.menubar_app import SpacedRepetitionAgentApp

    assert callable(SpacedRepetitionAgentApp.run)


def test_desktop_entry_point_launches_app_once_and_returns_success(monkeypatch):
    from sra.capture import menubar_app

    app = Mock()
    factory = Mock(return_value=app)
    monkeypatch.setattr(menubar_app, "SpacedRepetitionAgentApp", factory)
    assert menubar_app.main() == 0
    factory.assert_called_once_with()
    app.run.assert_called_once_with()


def test_desktop_entry_point_explains_setup_failure_and_returns_nonzero(monkeypatch, capsys):
    from sra.capture import menubar_app

    app = Mock()
    app.run.side_effect = RuntimeError("desktop extra missing: install desktop dependencies")
    monkeypatch.setattr(menubar_app, "SpacedRepetitionAgentApp", Mock(return_value=app))
    assert menubar_app.main() == 1
    output = capsys.readouterr()
    assert "desktop extra missing" in output.out + output.err


def test_initial_idle_state_has_no_recorder_or_worker(make_harness):
    harness = make_harness()
    assert harness.controller.state == "idle"
    assert not harness.recorders
    assert not harness.work
    assert "idle" in harness.messages[-1].lower()


def test_hotkey_toggle_runs_one_background_submission_and_marshals_success_to_ui(make_harness):
    harness = make_harness()
    controller = harness.controller
    controller.toggle()
    recorder = harness.recorders[0]
    recorder.start.assert_called_once_with()
    assert controller.state == "recording"
    assert "recording" in harness.messages[-1].lower()
    harness.process.assert_not_called()

    controller.toggle()
    recorder.stop.assert_called_once_with()
    assert controller.state == "processing"
    assert "processing" in harness.messages[-1].lower()
    assert len(harness.work) == 1
    harness.process.assert_not_called()
    assert recorder.path.exists()
    recorder.close.assert_not_called()

    for _ in range(5):
        controller.toggle()
    assert len(harness.recorders) == 1
    assert len(harness.work) == 1
    recorder.stop.assert_called_once_with()

    def consume(path):
        assert path.read_bytes() == b"finalized audio"
        recorder.close.assert_not_called()
        return [{"id": 51, "topic": "Mitosis"}, {"id": 52, "topic": "Meiosis"}]

    harness.process.side_effect = consume
    before_completion = list(harness.messages)
    harness.finish_worker()
    harness.process.assert_called_once_with(recorder.path)
    assert not recorder.path.exists()
    assert harness.messages == before_completion, "Worker must queue UI work rather than update UI directly"
    assert harness.ui
    harness.drain_ui()
    assert controller.state == "idle"
    assert any("saved" in message.lower() and "2" in message for message in harness.messages)

    controller.toggle()
    assert controller.state == "recording"
    assert len(harness.recorders) == 2


def test_silence_displays_no_speech_and_allows_another_capture(make_harness):
    harness = make_harness()
    harness.process.return_value = []
    harness.controller.toggle()
    harness.controller.toggle()
    harness.finish_worker()
    harness.drain_ui()
    assert harness.controller.state == "idle"
    assert any("no speech" in message.lower() for message in harness.messages)
    assert not any("saved" in message.lower() for message in harness.messages)
    assert not harness.recorders[0].path.exists()
    harness.controller.toggle()
    assert harness.controller.state == "recording"


@pytest.mark.parametrize("stage", ["start", "stop"])
def test_capture_error_closes_recorder_without_submission_and_next_attempt_succeeds(tmp_path, stage):
    first = FakeRecorder(tmp_path / "failed.wav")
    second = FakeRecorder(tmp_path / "retry.wav")
    getattr(first, stage).side_effect = RecordingError("microphone permission denied")
    factory = Mock(side_effect=[first, second])
    process = Mock()
    submit = Mock()
    messages = []
    controller = CaptureController(
        recorder_factory=factory,
        process=process,
        submit=submit,
        dispatch=Mock(),
        show_status=messages.append,
        stop_listener=Mock(),
    )
    controller.toggle()
    if stage == "stop":
        controller.toggle()
    assert controller.state == "idle"
    assert first.close.called
    assert not first.path.exists()
    submit.assert_not_called()
    process.assert_not_called()
    assert any("permission denied" in message.lower() for message in messages)
    controller.toggle()
    assert controller.state == "recording"
    second.start.assert_called_once_with()


@pytest.mark.parametrize("failure_kind", ["transcription", "server", "timeout"])
def test_processing_error_cleans_up_reports_useful_error_without_false_save_or_retry(make_harness, failure_kind):
    harness = make_harness()
    from sra.capture.transcription import TranscriptionError
    from sra.client.api_client import ApiClientError

    messages = {
        "transcription": "audio could not be decoded",
        "server": "server unavailable",
        "timeout": "request timed out; save outcome is unknown; check search before trying again",
    }
    error_type = TranscriptionError if failure_kind == "transcription" else ApiClientError
    harness.process.side_effect = error_type(messages[failure_kind])
    harness.controller.toggle()
    harness.controller.toggle()
    harness.finish_worker()
    harness.drain_ui()
    assert harness.controller.state == "idle"
    harness.process.assert_called_once()
    assert not harness.recorders[0].path.exists()
    assert any(messages[failure_kind] in message.lower() for message in harness.messages)
    assert not any("saved" in message.lower() for message in harness.messages)
    harness.process.side_effect = None
    harness.controller.toggle()
    harness.controller.toggle()
    harness.finish_worker()
    harness.drain_ui()
    assert harness.controller.state == "idle"
    assert harness.process.call_count == 2
    assert any("saved" in message.lower() for message in harness.messages)


def test_worker_scheduling_failure_releases_finalized_audio_and_returns_idle(tmp_path):
    recorder = FakeRecorder(tmp_path / "capture.wav")
    process = Mock()
    messages = []
    controller = CaptureController(
        recorder_factory=lambda: recorder,
        process=process,
        submit=Mock(side_effect=RuntimeError("worker unavailable")),
        dispatch=Mock(),
        show_status=messages.append,
        stop_listener=Mock(),
    )
    controller.toggle()
    controller.toggle()
    assert controller.state == "idle"
    assert not recorder.path.exists()
    assert recorder.close.called
    process.assert_not_called()
    assert any("worker unavailable" in message.lower() for message in messages)


@pytest.mark.parametrize("is_recording", [False, True])
def test_quit_stops_listener_once_closes_active_audio_and_never_submits(make_harness, is_recording):
    harness = make_harness()
    if is_recording:
        harness.controller.toggle()
    harness.controller.quit()
    harness.controller.quit()
    harness.controller.toggle()
    assert harness.controller.state == "closed"
    harness.stop_listener.assert_called_once_with()
    harness.process.assert_not_called()
    assert not harness.work
    if is_recording:
        assert not harness.recorders[0].path.exists()
        assert harness.recorders[0].close.called
        harness.recorders[0].stop.assert_not_called()
    else:
        assert not harness.recorders


@pytest.mark.parametrize("worker_fails", [False, True])
def test_quit_during_inflight_read_retains_audio_until_worker_finishes_and_ignores_late_ui(make_harness, worker_fails):
    harness = make_harness()
    harness.controller.toggle()
    harness.controller.toggle()
    recorder = harness.recorders[0]

    def consume(path: Path):
        assert path.exists()
        # Simulate UI quit while a background process is still using the path.
        harness.run_ui_callback(harness.controller.quit)
        harness.run_ui_callback(harness.controller.quit)
        harness.run_ui_callback(harness.controller.toggle)
        assert harness.controller.state == "closed"
        assert path.read_bytes() == b"finalized audio"
        recorder.close.assert_not_called()
        if worker_fails:
            raise RecordingError("processing interrupted")
        return [{"id": 91, "topic": "Mitosis"}]

    harness.process.side_effect = consume
    harness.finish_worker()
    assert not recorder.path.exists()
    assert recorder.close.called
    before_late_ui = list(harness.messages)
    harness.drain_ui()
    assert harness.messages == before_late_ui
    assert harness.controller.state == "closed"
    assert len(harness.recorders) == 1
    harness.process.assert_called_once()
    harness.stop_listener.assert_called_once_with()
