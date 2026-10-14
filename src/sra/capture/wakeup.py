"""SRA-507 — Wake-up delivery loop on the desktop.

Polls the server's ``GET /api/v1/digest/ready`` endpoint and speaks the
returned digest sentence via :mod:`sra.capture.tts` when the per-user
``wakeup_notification_sent_at`` flag is set for today. The server side
(SRA-502) decides whether a digest is ``ready``; the client side decides
whether to speak it. The loop owns no state beyond the ``stop_event`` and
the poll cadence.

Threading contract:

- ``run_wakeup_loop`` is meant to run on a background thread owned by the
  menu-bar app (SRA-205). ``stop_event`` is the only shutdown signal —
  callers must ``stop_event.set()`` to make the loop exit.
- ``stop_event.wait(poll_interval_s)`` is interruptible, so the loop shuts
  down within one ``poll_interval_s`` of the event firing.
- Per-iteration exceptions other than ``KeyboardInterrupt`` are logged and
  swallowed so a transient network/audio failure does not kill the loop.
  ``KeyboardInterrupt`` propagates so the menu-bar app can quit on Ctrl-C.
"""

import logging
from threading import Event
from typing import Protocol

from sra.capture import tts

_logger = logging.getLogger(__name__)


class DigestClient(Protocol):
    """Duck-typed shape ``run_wakeup_loop`` accepts as ``api_client``.

    Tests substitute a ``SimpleNamespace`` with the same methods; the real
    desktop callers pass the module-level ``sra.client.api_client``
    helpers, which expose the same callables.
    """

    def get_digest_ready(self) -> bool: ...

    def get_digest_text(self) -> str: ...


def poll_digest_ready(api_client: DigestClient) -> bool:
    """Return whether the server has a wake-up sentence ready for the user.

    Thin wrapper over ``api_client.get_digest_ready()``; the function exists
    so callers can mock the wake-up module's surface without mocking the
    api_client module's surface.
    """
    return api_client.get_digest_ready()


def deliver_digest(api_client: DigestClient) -> None:
    """Fetch the digest sentence and ``speak`` it.

    Calls ``api_client.get_digest_text()`` first, then ``tts.speak(text)``.
    ``speak`` blocks until playback finishes, so a transient blip in
    ``get_digest_text`` is the only thing that delays the next poll — the
    next outer-loop iteration retries the network call.
    """
    text = api_client.get_digest_text()
    tts.speak(text)


def run_wakeup_loop(
    stop_event: Event,
    *,
    api_client: DigestClient | None = None,
    poll_interval_s: float = 60,
) -> None:
    """Poll the server and speak it; run until ``stop_event`` is set.

    When ``api_client`` is ``None`` the loop calls the module-level
    ``sra.client.api_client.get_digest_ready`` / ``get_digest_text``
    helpers, which use ``ClientSettings.api_base_url`` for the server URL.
    Pass ``api_client`` to substitute behavior (the tests do; the eventual
    menubar wiring will pass the credential-bearing client).

    ``poll_interval_s`` is the gap between the end of one iteration and
    the start of the next. The wait is interruptible, so the loop exits
    within one ``poll_interval_s`` of ``stop_event.set()``.
    """
    if api_client is None:
        from sra.client import api_client as _api_module

        client: DigestClient = _api_module
    else:
        client = api_client

    while not stop_event.is_set():
        try:
            if poll_digest_ready(client):
                deliver_digest(client)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            _logger.warning("wake-up iteration failed: %s; continuing", exc, exc_info=True)

        if stop_event.wait(poll_interval_s):
            return