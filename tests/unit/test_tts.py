"""SRA-503 — Local TTS abstraction: speak(), set_backend, reset_backend."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false

from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.phase5


@pytest.fixture
def tts():
    """Import the production tts module lazily so this file collects before
    the implementation exists; once SRA-503 lands, the import resolves and
    every test in this module runs against the real module."""
    from sra.capture import tts as _tts

    return _tts


@pytest.fixture(autouse=True)
def _reset_tts_backend(tts):
    yield
    tts.reset_backend()


def test_speak_invokes_the_backend_exactly_once_with_full_text(tts):
    backend = MagicMock()
    tts.set_backend(backend)

    tts.speak("hello world")

    backend.assert_called_once_with("hello world")


@pytest.mark.parametrize("text", ["hi", "multiple words here", "with punctuation!", "0123456789"])
def test_speak_forwards_text_verbatim(text, tts):
    backend = MagicMock()
    tts.set_backend(backend)

    tts.speak(text)

    backend.assert_called_once_with(text)


def test_speak_with_empty_text_is_a_no_op_and_does_not_invoke_backend(tts):
    backend = MagicMock()
    tts.set_backend(backend)

    tts.speak("")

    backend.assert_not_called()


def test_speak_invokes_subprocess_run_with_say_and_check_true(tts, monkeypatch):
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(tts.subprocess, "run", run)
    tts.reset_backend()

    tts.speak("hello mac")

    run.assert_called_once()
    positional = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert positional == ["say", "hello mac"]
    assert kwargs.get("check") is True


def test_say_backend_raises_called_process_error_when_say_fails(tts, monkeypatch):
    from subprocess import CalledProcessError

    def fail(args, **kwargs):
        assert kwargs.get("check") is True
        raise CalledProcessError(returncode=1, cmd=args)

    monkeypatch.setattr(tts.subprocess, "run", fail)
    tts.reset_backend()

    with pytest.raises(CalledProcessError):
        tts.speak("will fail")


def test_set_backend_and_reset_backend_round_trip(tts):
    first = MagicMock(name="first")
    second = MagicMock(name="second")
    tts.set_backend(first)
    tts.speak("one")
    first.assert_called_once_with("one")

    tts.reset_backend()
    tts.speak("two")
    second.assert_not_called()

    tts.set_backend(second)
    tts.speak("three")
    second.assert_called_once_with("three")


def test_tts_module_does_not_invoke_subprocess_at_import_time(tts):
    """Importing the module must not run `say` or any other subprocess."""
    import importlib

    importlib.reload(tts)
    tts.reset_backend()


def test_tts_module_is_import_safe_on_non_macos(tts, monkeypatch):
    """Setting `say` to None on PATH must not raise; the import and the empty-text
    no-op must remain safe."""
    monkeypatch.setenv("PATH", "")
    import importlib

    importlib.reload(tts)
    tts.speak("")
    backend = MagicMock()
    tts.set_backend(backend)
    tts.speak("hi")
    backend.assert_called_once_with("hi")
