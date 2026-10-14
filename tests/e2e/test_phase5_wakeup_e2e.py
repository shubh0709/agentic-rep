"""SRA-507 e2e — Wake-up loop end-to-end with a stubbed api_client and recording TTS."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.phase5


@pytest.fixture
def tts():
    from sra.capture import tts as _tts

    return _tts


@pytest.fixture
def wakeup():
    from sra.capture import wakeup as _wakeup

    return _wakeup


class _RecordingTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> None:
        self.calls.append(text)


@pytest.fixture
def recording_tts(monkeypatch, tts):
    backend = _RecordingTTS()
    tts.set_backend(backend)
    yield backend
    tts.reset_backend()


def test_phase5_wakeup_e2e_speaks_once_when_ready_and_exits_within_poll_interval(recording_tts, wakeup):
    ready_calls = {"count": 0}

    def ready_side_effect():
        ready_calls["count"] += 1
        if ready_calls["count"] == 1:
            return True
        return False

    client = SimpleNamespace()
    client.get_digest_ready = MagicMock(side_effect=ready_side_effect)
    client.get_digest_text = MagicMock(return_value="You have 1 review due today: Mitosis.")

    stop = threading.Event()
    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.05},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while not recording_tts.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recording_tts.calls == ["You have 1 review due today: Mitosis."]

    started = time.monotonic()
    stop.set()
    thread.join(timeout=1.0)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < 1.0, f"loop did not exit promptly: {elapsed:.3f}s"

    assert client.get_digest_text.call_count == 1
    assert client.get_digest_ready.call_count >= 1


def test_phase5_wakeup_e2e_does_not_speak_when_never_ready(recording_tts, wakeup):
    client = SimpleNamespace()
    client.get_digest_ready = MagicMock(return_value=False)
    client.get_digest_text = MagicMock(return_value="never reached")

    stop = threading.Event()
    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.02},
        daemon=True,
    )
    thread.start()
    time.sleep(0.2)
    stop.set()
    thread.join(timeout=1.0)

    assert recording_tts.calls == []
    assert client.get_digest_text.call_count == 0
