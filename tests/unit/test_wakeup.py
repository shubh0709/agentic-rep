"""SRA-507 — Wake-up delivery loop: poll, deliver, run until stop_event."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.phase5


@pytest.fixture
def wakeup():
    """Lazy-load the wakeup module so the test file collects before SRA-507 lands."""
    from sra.capture import wakeup as _wakeup

    return _wakeup


@pytest.fixture
def tts():
    from sra.capture import tts as _tts

    return _tts


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


def _stub_client(*, ready_sequence, text: str = "You have 1 review due today: Mitosis."):
    """Build a stub api_client whose get_digest_ready / get_digest_text return scripted values."""
    client = SimpleNamespace()
    client.get_digest_ready = MagicMock(side_effect=list(ready_sequence))
    client.get_digest_text = MagicMock(return_value=text)
    return client


def test_poll_digest_ready_is_a_thin_wrapper_around_get_digest_ready(wakeup):
    client = _stub_client(ready_sequence=[True])
    assert wakeup.poll_digest_ready(client) is True
    client.get_digest_ready.assert_called_once_with()


def test_poll_digest_ready_propagates_false_value(wakeup):
    client = _stub_client(ready_sequence=[False])
    assert wakeup.poll_digest_ready(client) is False


def test_deliver_digest_calls_get_digest_text_then_speak(recording_tts, wakeup):
    client = _stub_client(ready_sequence=[], text="digest sentence")

    wakeup.deliver_digest(client)

    client.get_digest_text.assert_called_once_with()
    assert recording_tts.calls == ["digest sentence"]


def test_deliver_digest_calls_get_then_speak_in_that_order(recording_tts, tts):
    order: list[str] = []
    client = SimpleNamespace()
    client.get_digest_text = MagicMock(side_effect=lambda: (order.append("get"), "the sentence")[1])
    backend = MagicMock(side_effect=lambda text: order.append("speak"))
    tts.set_backend(backend)

    from sra.capture import wakeup as _wakeup

    _wakeup.deliver_digest(client)

    assert order == ["get", "speak"]


def test_run_wakeup_loop_speaks_once_when_ready_then_exits(recording_tts, wakeup):
    stop = threading.Event()
    client = _stub_client(ready_sequence=[True, False])

    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.05},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while not recording_tts.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recording_tts.calls, "wake-up loop never reached ready=True"

    stop.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive()

    assert len(recording_tts.calls) == 1


def test_run_wakeup_loop_does_not_speak_when_never_ready(recording_tts, wakeup):
    stop = threading.Event()
    client = _stub_client(ready_sequence=[False])

    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.05},
        daemon=True,
    )
    thread.start()
    time.sleep(0.2)
    stop.set()
    thread.join(timeout=1.0)

    assert recording_tts.calls == []


def test_run_wakeup_loop_exits_within_poll_interval_of_stop(recording_tts, wakeup):
    stop = threading.Event()
    client = _stub_client(ready_sequence=[False])

    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.5},
        daemon=True,
    )
    thread.start()
    time.sleep(0.1)

    started = time.monotonic()
    stop.set()
    thread.join(timeout=1.5)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < 1.0, f"loop did not exit promptly: {elapsed:.3f}s"


def test_run_wakeup_loop_survives_a_per_iteration_exception(recording_tts, caplog, wakeup):
    stop = threading.Event()

    call_state = {"count": 0}

    def ready_storm():
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise RuntimeError("transient network blip")
        if call_state["count"] == 2:
            stop.set()
        return False

    client = SimpleNamespace()
    client.get_digest_ready = MagicMock(side_effect=ready_storm)
    client.get_digest_text = MagicMock(return_value="ignored")

    with caplog.at_level(logging.WARNING):
        thread = threading.Thread(
            target=wakeup.run_wakeup_loop,
            kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.05},
            daemon=True,
        )
        thread.start()
        thread.join(timeout=1.5)

    assert not thread.is_alive()
    assert any("transient network blip" in record.getMessage() for record in caplog.records)
    assert recording_tts.calls == []


def test_run_wakeup_loop_speaks_each_time_ready_becomes_true(recording_tts, wakeup):
    stop = threading.Event()
    client = _stub_client(
        ready_sequence=[True, False, True, False, True],
        text="repeat me",
    )

    thread = threading.Thread(
        target=wakeup.run_wakeup_loop,
        kwargs={"stop_event": stop, "api_client": client, "poll_interval_s": 0.02},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while len(recording_tts.calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    thread.join(timeout=1.0)

    assert len(recording_tts.calls) >= 3
    assert all(call == "repeat me" for call in recording_tts.calls)
