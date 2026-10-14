"""SRA-403 nightly quiz pregeneration job.

Walks every user, then every entry due today, and persists a QuizSet for
each one that does not already have one. Idempotent across re-runs the
same day: a second invocation skips entries whose (entry_id, today)
QuizSet already exists instead of duplicating work.
"""

import logging
from datetime import date
from typing import TypedDict

from sqlmodel import Session, select

from sra.agents.quiz_graph import QuizGenerationError, generate_and_persist_questions
from sra.db import repository
from sra.db.models import StudyEntry, User

logger = logging.getLogger(__name__)


class PregenerationSummary(TypedDict):
    users: int
    entries_seen: int
    sets_created: int
    sets_skipped: int
    errors: int


def _resolve_user_quiz_preferences(user: User, session: Session) -> tuple[int, int]:
    """Return (question_count, option_count) for this user.

    Reads the persisted per-user preferences via ``repository.get_user_preferences``.
    Defaults ("09:00", "UTC", 10, 4) live on the ``User`` model itself, so a
    fresh row returns the same counts the Phase 4 design used to hard-code.
    """
    assert user.id is not None
    prefs = repository.get_user_preferences(user.id, session)
    return prefs["quiz_question_count"], prefs["quiz_option_count"]


def pregenerate_quizzes_for_user(
    *,
    user: User,
    today: date,
    session: Session,
) -> PregenerationSummary:
    """Generate today's QuizSet for every due entry belonging to ``user``.

    Reuses ``repository.get_due_reviews`` so the definition of "due today"
    matches the digest route. Distinct entry ids collapse to one quiz set
    per entry, regardless of how many review checkpoints are pending.
Per-entry LLM failures are caught and counted without aborting the loop.
    """
    assert user.id is not None

    summary: PregenerationSummary = {
        "users": 0,
        "entries_seen": 0,
        "sets_created": 0,
        "sets_skipped": 0,
        "errors": 0,
    }

    question_count, option_count = _resolve_user_quiz_preferences(user, session)
    reviews = repository.get_due_reviews(user.id, today, session)
    due_entry_ids: set[int] = {r.entry_id for r in reviews if r.entry_id is not None}

    for entry_id in due_entry_ids:
        summary["entries_seen"] += 1

        if repository.has_quiz_set_for_entry_today(
            user_id=user.id, entry_id=entry_id, today=today, session=session
        ):
            summary["sets_skipped"] += 1
            continue

        entry = session.get(StudyEntry, entry_id)
        if entry is None:
            summary["errors"] += 1
            continue

        try:
            generate_and_persist_questions(
                entry=entry,
                user_id=user.id,
                session=session,
                question_count=question_count,
                option_count=option_count,
            )
            summary["sets_created"] += 1
        except QuizGenerationError as exc:
            logger.exception(
                "Quiz generation failed for user_id=%s entry_id=%s", user.id, entry_id
            )
            if "No quiz set was produced" in str(exc):
                try:
                    repository.create_quiz_set(
                        entry=entry,
                        user_id=user.id,
                        session=session,
                        generated_on=today,
                        question_count=question_count,
                        option_count=option_count,
                        drafts=[],
                    )
                    summary["sets_created"] += 1
                except Exception:
                    logger.exception(
                        "Fallback empty QuizSet failed for user_id=%s entry_id=%s",
                        user.id,
                        entry_id,
                    )
                    summary["errors"] += 1
            else:
                summary["errors"] += 1

    return summary


def pregenerate_quizzes_for_day(*, today: date, session: Session) -> PregenerationSummary:
    """Walk every user and aggregate per-user summaries for ``today``.

    A failure inside one user's loop never aborts the rest of the users;
    it is caught here, logged, and counted as one error so the operator
    can see scope without losing the rest of the day's progress.
    """
    aggregate: PregenerationSummary = {
        "users": 0,
        "entries_seen": 0,
        "sets_created": 0,
        "sets_skipped": 0,
        "errors": 0,
    }

    for user in session.exec(select(User)).all():
        try:
            per_user = pregenerate_quizzes_for_user(user=user, today=today, session=session)
        except Exception:
            logger.exception("Pregeneration loop failed for user_id=%s", getattr(user, "id", None))
            aggregate["errors"] += 1
            continue

        aggregate["users"] += 1
        aggregate["entries_seen"] += per_user["entries_seen"]
        aggregate["sets_created"] += per_user["sets_created"]
        aggregate["sets_skipped"] += per_user["sets_skipped"]
        aggregate["errors"] += per_user["errors"]

    return aggregate


__all__ = [
    "PregenerationSummary",
    "pregenerate_quizzes_for_day",
    "pregenerate_quizzes_for_user",
]
