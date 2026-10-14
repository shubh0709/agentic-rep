"""SRA-503 — Local text-to-speech via macOS ``say``.

The desktop client speaks to the user for two flows: the wake-up delivery
loop (SRA-507) reads the day's digest aloud at the user's preferred review
time, and the voice quiz client (SRA-508) reads each quiz question aloud
before recording an answer. Both call ``speak(text)`` and block until
playback finishes — callers (the wake-up loop and the voice quiz driver)
need to know when it is safe to start listening again.

This module is intentionally thin: a single public ``speak(text)`` function
backed by the macOS ``say`` binary. No streaming, no caching, no voice
selection. Cross-platform TTS is out of scope; the project targets macOS for
the desktop client and ``say`` ships with the OS, so no extra Python
dependency is required.

The default backend is the ``_say_backend`` function, which invokes
``subprocess.run(["say", text], check=True)``. ``check=True`` ensures a
non-zero exit (for example, when ``say`` is not on ``PATH``) raises
``CalledProcessError`` rather than silently swallowing the failure.

Test seam: ``set_backend(backend)`` swaps the backend for the rest of the
process, and ``reset_backend()`` restores ``say``. The autouse fixture in
``tests/unit/test_tts.py`` calls ``reset_backend`` after every test, so
tests do not leak state into each other.

The module is import-safe on non-macOS platforms: ``say`` is only invoked
when ``speak(text)`` is called with non-empty text, never at import time.
"""

import subprocess
from collections.abc import Callable

Backend = Callable[[str], None]


def _say_backend(text: str) -> None:
    """Invoke the macOS ``say`` binary with ``text``; block until playback finishes."""
    subprocess.run(["say", text], check=True)


_backend: Backend = _say_backend


def speak(text: str) -> None:
    """Synthesize ``text`` aloud on the local machine; block until playback finishes.

    Empty text is a no-op: there is no playback to wait for, and the
    configured backend is not invoked.
    """
    if not text:
        return
    return _backend(text)


def set_backend(backend: Backend) -> None:
    """Swap the backend for the rest of the process; used by tests to substitute behavior."""
    global _backend
    _backend = backend


def reset_backend() -> None:
    """Restore the default ``say`` backend; used by test teardown."""
    global _backend
    _backend = _say_backend