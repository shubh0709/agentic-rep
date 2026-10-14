"""SRA-502 — wake-up tick and APScheduler wiring.

Owns server-side time-of-day delivery: every minute, mark each user whose
local ``preferred_review_time`` has just passed since the previous tick so
the desktop client (SRA-507) only needs to poll ``GET /digest/ready``.
Daily quiz pregeneration (SRA-403) is wired through the same scheduler so
the lifespan owns both jobs.
"""

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import FastAPI
from sqlmodel import Session, select

from sra.db.models import ReviewSchedule, StudyEntry, User
from sra.db.session import get_engine

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


def _session_factory() -> Session:
    return Session(get_engine())


def _parse_review_time(value: str) -> time:
    """Parse an ``HH:MM`` string into a ``time`` instance."""
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute))


def _local_date_for(dt: datetime, tz_name: str) -> date:
    return dt.astimezone(ZoneInfo(tz_name)).date()


def _already_notified_today(user: User, as_of_utc: datetime) -> bool:
    """Return True when ``user.wakeup_notification_sent_at`` falls on or after
    the user's local date for ``as_of_utc``."""
    sent_at = user.wakeup_notification_sent_at
    if sent_at is None:
        return False
    sent_local_date = _local_date_for(sent_at, user.timezone)
    today_local = _local_date_for(as_of_utc, user.timezone)
    return sent_local_date >= today_local


def users_due_for_wakeup(
    *,
    as_of_utc: datetime,
    session: Session,
) -> list[User]:
    """Return users whose local time has just crossed ``preferred_review_time``.

    Includes a user exactly once per local day:

    * their local clock has reached or passed ``preferred_review_time`` at ``as_of_utc``;
    * they have at least one ``ReviewSchedule`` with ``status == "pending"`` and
      ``scheduled_date <= today`` (their local today);
    * ``wakeup_notification_sent_at`` is either ``None`` or older than today's
      local date in their own timezone.

    Bad timezones are logged and the user is skipped, never raised: one
    corrupt row cannot break the rest of the batch.
    """
    due: list[User] = []
    for user in session.exec(select(User)).all():
        try:
            zone = ZoneInfo(user.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning(
                "Skipping user_id=%s with unknown timezone %r in users_due_for_wakeup",
                getattr(user, "id", None),
                user.timezone,
            )
            continue

        local_now = as_of_utc.astimezone(zone)
        review_at = _parse_review_time(user.preferred_review_time)
        if local_now.time() < review_at:
            continue
        if _already_notified_today(user, as_of_utc):
            continue

        local_today = local_now.date()
        pending = session.exec(
            select(ReviewSchedule)
            .join(StudyEntry)
            .where(
                StudyEntry.user_id == user.id,
                ReviewSchedule.status == "pending",
                ReviewSchedule.scheduled_date <= local_today,
            )
        ).all()
        if not pending:
            continue

        due.append(user)
    return due


def run_wakeup_tick(
    *,
    session: Session,
    as_of_utc: datetime,
) -> int:
    """Mark every due user as notified for their local today.

    Sets ``wakeup_notification_sent_at = as_of_utc`` for each included user
    and commits once at the end. A per-user exception (bad timezone, etc.)
    is logged and the loop continues so a single broken row never aborts
    the rest of the batch. Returns the number of users marked.
    """
    due = users_due_for_wakeup(as_of_utc=as_of_utc, session=session)
    notified = 0
    for user in due:
        try:
            user.wakeup_notification_sent_at = as_of_utc
            session.add(user)
            notified += 1
        except Exception:
            logger.exception(
                "Failed to mark wakeup_notification_sent_at for user_id=%s",
                getattr(user, "id", None),
            )
            session.rollback()
            continue

    if notified:
        session.commit()
    return notified


def build_pregeneration_job(
    session_factory: SessionFactory,
) -> Callable[[], None]:
    """Return a callable that opens a session and runs today's pregeneration."""

    def _job() -> None:
        with session_factory() as session:
            from sra.jobs.quiz_pregeneration import pregenerate_quizzes_for_day

            summary = pregenerate_quizzes_for_day(today=date.today(), session=session)
            logger.info(
                "Daily quiz pregeneration finished: users=%s entries_seen=%s created=%s skipped=%s errors=%s",
                summary["users"],
                summary["entries_seen"],
                summary["sets_created"],
                summary["sets_skipped"],
                summary["errors"],
            )

    return _job


def build_wakeup_job(
    session_factory: SessionFactory,
) -> Callable[[], None]:
    """Return a callable that opens a session and runs the wake-up tick."""

    def _job() -> None:
        with session_factory() as session:
            count = run_wakeup_tick(session=session, as_of_utc=datetime.now(UTC))
            if count:
                logger.info("Wake-up tick notified %s user(s)", count)

    return _job


def start_scheduler(app: FastAPI) -> None:
    """Start the APScheduler-owned pregeneration cron and wake-up interval.

    When ``scheduler_enabled`` is False, this function records ``None`` on
    ``app.state.scheduler`` and returns without registering any jobs, so
    ``stop_scheduler`` is symmetric for tests that disable the scheduler.
    """
    from apscheduler.schedulers.background import BackgroundScheduler

    from sra.config import get_settings

    settings = get_settings()
    if not settings.scheduler_enabled:
        app.state.scheduler = None
        return

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        build_pregeneration_job(_session_factory),
        trigger="cron",
        hour=settings.scheduler_pregen_hour_utc,
        minute=0,
        id="sra.pregenerate",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        build_wakeup_job(_session_factory),
        trigger="interval",
        seconds=settings.scheduler_wakeup_interval_s,
        id="sra.wakeup_tick",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC) + timedelta(seconds=settings.scheduler_wakeup_interval_s),
    )
    scheduler.start()
    app.state.scheduler = scheduler


def stop_scheduler(app: FastAPI) -> None:
    """Shut down the scheduler without blocking on an in-flight job."""
    scheduler: BackgroundScheduler | None = getattr(app.state, "scheduler", None)
    if scheduler is not None:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            logger.exception("Failed to shut down scheduler cleanly")
    app.state.scheduler = None


__all__ = [
    "build_pregeneration_job",
    "build_wakeup_job",
    "run_wakeup_tick",
    "start_scheduler",
    "stop_scheduler",
    "users_due_for_wakeup",
]