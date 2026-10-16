"""SRA-403 nightly quiz pregeneration job.

Walks every user, then every entry due today, and persists a QuizSet for
each one that does not already have one. Idempotent across re-runs the
same day: a second invocation skips entries whose (entry_id, today)
QuizSet already exists instead of duplicating work.

SRA-802 extends this job to also create an InterviewPlan per new
QuizSet (a structured outline the SRA-803 conversation graph expands
into per-turn questions). The interview counters are surfaced in the
dev pregeneration summary so operators can see planner success rate
alongside the MCQ counts.
"""

import logging
from datetime import date
from typing import TypedDict

from sqlmodel import Session, select

from sra.agents.quiz_graph import QuizGenerationError, generate_and_persist_questions
from sra.db import repository
from sra.db.models import InterviewPlan, QuizSet, StudyEntry, User

logger = logging.getLogger(__name__)


class PregenerationSummary(TypedDict):
    users: int
    entries_seen: int
    sets_created: int
    sets_skipped: int
    errors: int
    interview_plans_created: int
    interview_plans_skipped: int
    interview_plans_errored: int


def _resolve_user_quiz_preferences(user: User, session: Session) -> tuple[int, int, int]:
    """Return (question_count, option_count, interview_question_count) for this user.

    Reads the persisted per-user preferences via ``repository.get_user_preferences``.
    Defaults ("09:00", "UTC", 10, 4, 6) live on the ``User`` model itself, so a
    fresh row returns the same counts the Phase 4/5/8 design used to hard-code.
    """
    assert user.id is not None
    prefs = repository.get_user_preferences(user.id, session)
    return (
        prefs["quiz_question_count"],
        prefs["quiz_option_count"],
        prefs["interview_question_count"],
    )


def _maybe_create_interview_plan(
    *,
    quiz_set: QuizSet,
    entry: StudyEntry,
    interview_question_count: int,
    session: Session,
) -> str:
    """Create the InterviewPlan for ``quiz_set`` if one does not already exist.

    Returns ``"created"``, ``"skipped"``, or ``"errored"``. The unique
    ``(quiz_set_id)`` constraint on ``interview_plans`` makes the second
    invocation the same day return ``"skipped"`` without a planner LLM call.
    """
    from sra.agents.interview_planner import generate_interview_outline

    try:
        statement = select(InterviewPlan).where(
            InterviewPlan.quiz_set_id == quiz_set.id  # pyright: ignore[reportArgumentType]
        )
        already = session.exec(statement).first()
        if already is not None:
            return "skipped"

        outline = generate_interview_outline(entry, interview_question_count)
        if not outline.items:
            return "errored"
        plan = repository.create_interview_plan(
            quiz_set=quiz_set,
            outline=outline,
            target_questions=interview_question_count,
            session=session,
        )
        if plan is None:
            return "errored"
        return "created"
    except QuizGenerationError:
        logger.exception(
            "Interview planner LLM/validation failure for user_id=%s entry_id=%s",
            getattr(quiz_set, "user_id", None),
            getattr(entry, "id", None),
        )
        return "errored"
    except Exception:
        logger.exception(
            "Interview plan creation failed for user_id=%s entry_id=%s",
            getattr(quiz_set, "user_id", None),
            getattr(entry, "id", None),
        )
        return "errored"


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
        "interview_plans_created": 0,
        "interview_plans_skipped": 0,
        "interview_plans_errored": 0,
    }

    question_count, option_count, interview_question_count = _resolve_user_quiz_preferences(user, session)
    reviews = repository.get_due_reviews(user.id, today, session)
    due_entry_ids: set[int] = {r.entry_id for r in reviews if r.entry_id is not None}

    for entry_id in due_entry_ids:
        summary["entries_seen"] += 1

        existing_statement = (
            select(QuizSet)
            .where(QuizSet.entry_id == entry_id)  # pyright: ignore[reportArgumentType]
            .order_by(QuizSet.id.desc())  # pyright: ignore[reportArgumentType, reportAttributeAccessIssue, reportOptionalMemberAccess]
        )
        existing_quiz_set = session.exec(existing_statement).first()

        if existing_quiz_set is not None:
            summary["sets_skipped"] += 1
            outcome = _maybe_create_interview_plan(
                quiz_set=existing_quiz_set,
                entry=session.get(StudyEntry, entry_id),  # type: ignore[arg-type]
                interview_question_count=interview_question_count,
                session=session,
            )
            if outcome == "created":
                summary["interview_plans_created"] += 1
            elif outcome == "skipped":
                summary["interview_plans_skipped"] += 1
            else:
                summary["interview_plans_errored"] += 1
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
            statement = (
                select(QuizSet)
                .where(QuizSet.entry_id == entry_id)  # pyright: ignore[reportArgumentType]
.order_by(QuizSet.id.desc())  # pyright: ignore[reportArgumentType, reportAttributeAccessIssue, reportOptionalMemberAccess]
            )
            target = session.exec(statement).first()
            if target is not None:
                outcome = _maybe_create_interview_plan(
                    quiz_set=target,
                    entry=entry,
                    interview_question_count=interview_question_count,
                    session=session,
                )
                if outcome == "created":
                    summary["interview_plans_created"] += 1
                elif outcome == "skipped":
                    summary["interview_plans_skipped"] += 1
                else:
                    summary["interview_plans_errored"] += 1
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
        "interview_plans_created": 0,
        "interview_plans_skipped": 0,
        "interview_plans_errored": 0,
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
        aggregate["interview_plans_created"] += per_user["interview_plans_created"]
        aggregate["interview_plans_skipped"] += per_user["interview_plans_skipped"]
        aggregate["interview_plans_errored"] += per_user["interview_plans_errored"]

    return aggregate


__all__ = [
    "PregenerationSummary",
    "pregenerate_quizzes_for_day",
    "pregenerate_quizzes_for_user",
]