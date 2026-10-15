"""SRA-603 — demo instance cleanup.

Only meaningful when ``Settings.demo_mode`` is True. The hosted demo uses
this to bound its storage; self-hosters should leave ``DEMO_MODE`` unset.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlmodel import Session

import sra.config
from sra.config import get_settings  # noqa: F401  -- module attribute for test monkeypatching
from sra.db.repository import (
    delete_user_cascade,
    list_old_demo_users,
)

logger = logging.getLogger(__name__)


class DemoModeDisabledError(RuntimeError):
    """Raised when a demo-only operation runs outside ``Settings.demo_mode``."""


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _default_session_factory() -> Session:
    from sra.db.session import get_engine

    return Session(get_engine())


def reset_demo_data(
    retention_days: int,
    *,
    as_of_utc: datetime | None = None,
    session_factory: SessionFactory | None = None,
) -> dict[str, int]:
    """Delete every demo user whose oldest related row is older than the cutoff.

    Raises ``DemoModeDisabledError`` when ``Settings.demo_mode`` is False. The
    database is never touched in that case. A second invocation within the
    same ``as_of_utc`` window returns ``{"deleted": 0}`` because the cutoff
    has not advanced.

    The caller owns the session's lifecycle: the function neither opens nor
    closes the session returned by ``session_factory``. Production callers
    use the default ``_default_session_factory`` inside a ``with`` block;
    tests that pass a shared session expect it to remain open after the call.
    """
    settings = sra.config.get_settings()
    if not settings.demo_mode:
        raise DemoModeDisabledError(
            "reset_demo_data requires Settings.demo_mode=True"
        )
    if as_of_utc is None:
        as_of_utc = datetime.now(UTC)
    factory = session_factory or _default_session_factory
    session = factory()
    deleted = 0
    for user, _oldest in list_old_demo_users(retention_days, as_of_utc, session):
        delete_user_cascade(user, session)
        deleted += 1
    session.commit()
    return {"deleted": deleted}


def build_demo_reset_job(
    session_factory: SessionFactory,
    retention_days: int,
) -> Callable[[], None]:
    """Return a callable that runs ``reset_demo_data`` and logs the result."""

    def _job() -> None:
        try:
            result = reset_demo_data(
                retention_days,
                as_of_utc=datetime.now(UTC),
                session_factory=session_factory,
            )
            if result["deleted"]:
                logger.info(
                    "Demo reset deleted %s user(s)", result["deleted"]
                )
        except DemoModeDisabledError:
            logger.debug("Demo reset skipped: Settings.demo_mode is False")
        except Exception:
            logger.exception("Demo reset job failed")

    return _job