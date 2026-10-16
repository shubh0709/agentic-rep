"""SRA-701 — dev-only affordances to verify the daily-quiz pipeline end to end.

Mounted under ``/api/v1/me/dev/*`` and protected by ``require_user`` + ``limit_authenticated``.
Gated behind ``Settings.dev_mode = True`` so a self-hoster without ``DEV_MODE=true``
cannot accidentally trigger it in production; outside dev mode the route returns ``503``.

Never mutates ``ReviewSchedule`` rows — it only generates ``QuizSet`` /
``QuizQuestion`` records and returns a summary so the caller can immediately
read the generated MCQs through ``GET /api/v1/quiz/{id}`` or drive a
``POST /api/v1/quiz/{quiz_set_id}/attempts`` web attempt.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from sra.agents.quiz_graph import QuizGenerationError, generate_and_persist_questions
from sra.api.deps import get_session, require_user
from sra.config import get_settings
from sra.db import repository
from sra.db.models import QuizSet, StudyEntry, User
from sra.jobs.quiz_pregeneration import (
    PregenerationSummary,
    pregenerate_quizzes_for_user,
)

router = APIRouter(prefix="/me/dev", tags=["dev"])


def _generate_for_upcoming_entries(
    *,
    user_id: int,
    today: date,
    session: Session,
    exclude_entry_ids: set[int] | None = None,
) -> PregenerationSummary:
    """Generate a ``QuizSet`` for every entry ``user_id`` owns that doesn't have one for ``today``.

    Mirrors the per-user loop in :func:`pregenerate_quizzes_for_user` but ignores the
    "review is due today" filter — useful in dev when every freshly-ingested
    entry's first review is tomorrow, so the natural pregeneration is a no-op.
    Honors the ``(entry_id, generated_on)`` unique constraint, so a re-run is a
    no-op for entries already covered today. Per-entry LLM failures are caught
    and counted, never raised.

    ``exclude_entry_ids`` lets the caller skip entries the natural pregeneration
    already visited (one per unique entry even if the natural path reported it
    as skipped), so the combined ``sets_seen/sets_skipped`` counts aren't
    double-bumped across the two passes.
    """
    summary: PregenerationSummary = {
        "users": 0,
        "entries_seen": 0,
        "sets_created": 0,
        "sets_skipped": 0,
        "errors": 0,
    }
    excluded = exclude_entry_ids or set()

    prefs = repository.get_user_preferences(user_id, session)
    question_count = prefs["quiz_question_count"]
    option_count = prefs["quiz_option_count"]

    already_covered_stmt = select(QuizSet.entry_id).where(  # pyright: ignore[reportArgumentType]
        QuizSet.user_id == user_id,
        QuizSet.generated_on == today,
    )
    already_covered = {
        row
        for row in session.exec(already_covered_stmt).all()  # pyright: ignore[reportArgumentType]
        if isinstance(row, int)
    }

    entries = list(
        session.exec(
            select(StudyEntry).where(StudyEntry.user_id == user_id)  # pyright: ignore[reportArgumentType]
        ).all()
    )

    for entry in entries:
        assert entry.id is not None
        if entry.id in excluded:
            continue
        if entry.id in already_covered:
            summary["entries_seen"] += 1
            summary["sets_skipped"] += 1
            continue

        summary["entries_seen"] += 1
        try:
            generate_and_persist_questions(
                entry=entry,
                user_id=user_id,
                session=session,
                question_count=question_count,
                option_count=option_count,
            )
            summary["sets_created"] += 1
        except QuizGenerationError:
            summary["errors"] += 1

    return summary


@router.post("/pregenerate-now")
def pregenerate_now(
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    include_upcoming: bool = Query(
        default=True,
        description=(
            "When true (default), also generate a QuizSet for every entry the "
            "user owns that doesn't have one for today — even if its first review "
            "is scheduled in the future. Lets a developer see the MCQs "
            "immediately after a fresh ingest, without waiting for the 1d/3d/... "
            "schedule to land. Never modifies ReviewSchedule rows."
        ),
    ),
) -> dict[str, object]:
    """Run today's quiz pregeneration synchronously for the calling user.

    Two modes:

    * ``include_upcoming=false`` — exactly what the daily APScheduler cron
      does for one user, just on demand. Only generates a QuizSet for entries
      with at least one pending review due today. Returns 503 unless
      ``Settings.dev_mode`` is true.
    * ``include_upcoming=true`` (default) — additionally covers fresh entries
      whose first review is still in the future, so the developer can read
      the MCQs through ``GET /api/v1/quiz/{id}`` immediately after ingesting.

    Both modes honor the natural review schedule; neither backdates nor
    rewrites ``ReviewSchedule`` rows.
    """
    settings = get_settings()
    if not settings.dev_mode:
        raise HTTPException(
            status_code=503,
            detail="Dev mode is not enabled (DEV_MODE=false)",
        )
    assert user.id is not None

    # Re-fetch from the request's own session so SQLAlchemy's identity map keeps
    # the row alive across the multiple commits inside the pregeneration
    # helpers — `require_user` resolves the user on a separate per-dependency
    # session that closes before this handler runs.
    fresh_user = session.get(User, user.id)
    if fresh_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user = fresh_user

    today = date.today()
    natural: PregenerationSummary = pregenerate_quizzes_for_user(
        user=user, today=today, session=session
    )
    natural_entry_ids = {
        entry_id
        for r in (
            session.exec(
                select(QuizSet).where(  # pyright: ignore[reportArgumentType]
                    QuizSet.user_id == user.id,
                    QuizSet.generated_on == today,
                )
            ).all()
        )
        for entry_id in [r.entry_id]
        if entry_id is not None
    }

    if include_upcoming:
        upcoming = _generate_for_upcoming_entries(
            user_id=user.id,  # pyright: ignore[reportArgumentType]
            today=today,
            session=session,
            exclude_entry_ids=natural_entry_ids,
        )
        merged: PregenerationSummary = {
            "users": natural["users"] + upcoming["users"],
            "entries_seen": natural["entries_seen"] + upcoming["entries_seen"],
            "sets_created": natural["sets_created"] + upcoming["sets_created"],
            "sets_skipped": natural["sets_skipped"] + upcoming["sets_skipped"],
            "errors": natural["errors"] + upcoming["errors"],
        }
    else:
        merged = natural

    quiz_sets = list(
        session.exec(
            select(QuizSet).where(  # pyright: ignore[reportArgumentType]
                QuizSet.user_id == user.id,
                QuizSet.generated_on == today,
            )
        ).all()
    )
    quiz_sets.sort(key=lambda qs: (qs.entry_id, qs.id if qs.id is not None else 0))

    topics_by_entry_id: dict[int, str | None] = {}
    if quiz_sets:
        entry_ids = sorted({qs.entry_id for qs in quiz_sets if qs.entry_id is not None})
        topic_rows = session.exec(
            select(StudyEntry.id, StudyEntry.topic).where(  # pyright: ignore[reportArgumentType]
                StudyEntry.id.in_(entry_ids)  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
            )
        ).all()
        for row in topic_rows:
            row_id = row[0]
            row_topic = row[1]
            topics_by_entry_id[row_id] = row_topic  # pyright: ignore[reportArgumentType]

    return {
        "summary": merged,
        "today": today.isoformat(),
        "include_upcoming": include_upcoming,
        "quiz_sets": [
            {
                "quiz_set_id": qs.id,
                "entry_id": qs.entry_id,
                "topic": topics_by_entry_id.get(qs.entry_id),
                "generated_on": qs.generated_on.isoformat(),
                "question_count": qs.question_count,
                "option_count": qs.option_count,
            }
            for qs in quiz_sets
        ],
    }
