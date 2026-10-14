"""
Data-access functions. Every function is scoped by `user_id` and
never trusts a caller-supplied row's ownership -- that discipline
matters more once Phase 3 adds real multi-user auth, but it costs
nothing to establish now while there's only one local user.
"""

from datetime import UTC, date, datetime, time

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, String, cast, or_, select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizAnswer,
    QuizAnswerCreate,
    QuizAttempt,
    QuizQuestion,
    QuizQuestionCreate,
    QuizSet,
    QuizSetCreate,
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
    _utcnow,
)
from sra.db.scheduler import schedule_reviews_for_entry


def create_entry(*, entry_create: StudyEntryCreate, user_id: int, session: Session) -> StudyEntry:
    """
    Insert a StudyEntry scoped to user_id, then call
    scheduler.schedule_reviews_for_entry for it so every entry is
    born with its six review checkpoints already in place.

    Takes a StudyEntryCreate (not loose kwargs) so id/user_id/created_at
    can never be supplied by a caller -- see the convention note atop
    db/models.py.
    """

    row = StudyEntry.model_validate(entry_create, update={"user_id": user_id})

    session.add(row)
    session.commit()
    session.refresh(row)

    # refresh() above populates the autoincremented id from the DB.
    assert row.id is not None
    schedule_reviews_for_entry(row.id, row.created_at.date(), session)

    return row


def get_due_reviews(user_id: int, as_of: date, session: Session) -> list[ReviewSchedule]:
    """
    Return this user's pending ReviewSchedule rows with
    scheduled_date <= as_of, newest scheduled_date last so the most
    overdue items surface first when a caller reverses the list (or
    sort however you find most useful for a digest -- just be
    explicit about the order, since the API layer renders it as-is).

    Concept: the query behind both GET /api/v1/digest and (in Phase
    later) the query agent's due-reviews tool -- one function, two
    callers, so the definition of "due" never drifts between them.
    """

    statement = (
        select(ReviewSchedule)
        .join(StudyEntry)
        .where(
            StudyEntry.user_id == user_id,
            ReviewSchedule.status == "pending",
            ReviewSchedule.scheduled_date <= as_of,
        )
        .order_by(ReviewSchedule.scheduled_date)  # pyright: ignore[reportArgumentType] -- SQLModel field typed as `date`, not a column, at the class level
    )

    return list(session.exec(statement).all())


def search_entries(
    user_id: int,
    query: str,
    session: Session,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[StudyEntry]:
    """
    Case-insensitive substring match over topic/subtopic/tags/summary
    for this user only, optionally bounded by [date_from, date_to],
    newest first. An empty query returns all of this user's entries.

    Concept: build the SQL filter conditionally (append predicates
    only for the filters that were actually passed) rather than
    branching into several near-duplicate queries.
    """

    # SQLModel fields are typed as their plain Python type (str/datetime/...)
    # at the class level, so pyright can't see the InstrumentedAttribute that
    # actually exists at runtime -- ignores below match the one already on
    # get_due_reviews's order_by() for the same reason.
    conditions: list = [StudyEntry.user_id == user_id]  # pyright: ignore[reportArgumentType]

    if query:
        pattern = f"%{query}%"
        conditions.append(
            or_(
                StudyEntry.topic.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue]
                StudyEntry.subtopic.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue, reportOptionalMemberAccess]
                StudyEntry.summary.ilike(pattern),  # pyright: ignore[reportAttributeAccessIssue]
                cast(StudyEntry.tags, String).ilike(pattern),
            )
        )

    if date_from is not None:
        conditions.append(StudyEntry.created_at >= datetime.combine(date_from, time.min, tzinfo=UTC))

    if date_to is not None:
        conditions.append(StudyEntry.created_at <= datetime.combine(date_to, time.max, tzinfo=UTC))

    statement = (
        select(StudyEntry)
        .where(*conditions)
        .order_by(StudyEntry.created_at.desc())  # pyright: ignore[reportAttributeAccessIssue]
    )

    return list(session.exec(statement).all())


def mark_review_done(user_id: int, review_id: int, status: str, session: Session) -> ReviewSchedule:
    """
    Flip a review's status ("done" or "skipped") and stamp
    completed_at (use `datetime.now(timezone.utc)` -- this project's
    SQLModel setup rejects naive datetimes on write, see
    db/models.py's `_utcnow` helper). Raise a plain ValueError if
    review_id doesn't resolve to a review owned (via its parent
    StudyEntry) by user_id -- never silently no-op on a mismatched id.
    """

    statement = (
        select(ReviewSchedule)
        .join(StudyEntry)
        .where(
            StudyEntry.user_id == user_id,
            ReviewSchedule.id == review_id,
        )
    )
    review = session.exec(statement).first()

    if review is None:
        raise ValueError(f"review_id {review_id} does not belong to user_id {user_id}")

    review.status = status
    review.completed_at = datetime.now(UTC)

    session.add(review)
    session.commit()
    session.refresh(review)

    return review


def create_quiz_set(
    *,
    entry: StudyEntry,
    user_id: int,
    session: Session,
    generated_on: date,
    question_count: int,
    option_count: int,
    drafts: list[MCQDraft],
) -> QuizSet:
    """
    Insert a QuizSet scoped to user_id along with one QuizQuestion row per
    draft, all in one transaction.

    Server-owned fields (user_id, entry_id, generated_on, snapshot counts) are
    applied via `model_validate(QuizSetCreate(entry_id=entry.id),
    update={...})` so a caller can never override them through the Create
    schema. The `(entry_id, generated_on)` unique constraint makes the nightly
    pregeneration idempotent -- a duplicate call surfaces as a typed
    ValueError rather than a raw SQLAlchemy IntegrityError.
    """

    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": user_id,
            "entry_id": entry.id,
            "question_count": question_count,
            "option_count": option_count,
            "generated_on": generated_on,
        },
    )
    session.add(quiz_set)

    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"quiz_set for entry_id={entry.id} on generated_on={generated_on} already exists"
        ) from exc

    assert quiz_set.id is not None

    for order_index, draft in enumerate(drafts):
        question = QuizQuestion.model_validate(
            QuizQuestionCreate(
                quiz_set_id=quiz_set.id,
                question_text=draft.question_text,
                options=list(draft.options),
                correct_option_index=draft.correct_option_index,
                explanation=draft.explanation,
                order_index=order_index,
            )
        )
        session.add(question)

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValueError(
            f"quiz_questions for quiz_set_id={quiz_set.id} violated a constraint"
        ) from exc

    session.refresh(quiz_set)
    question_rows = list(
        session.exec(
            select(QuizQuestion)
            .where(QuizQuestion.quiz_set_id == quiz_set.id)
            .order_by(QuizQuestion.order_index)  # pyright: ignore[reportArgumentType]
        )
    )
    try:
        quiz_set.questions = question_rows  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        pass
    return quiz_set


def get_quiz_set(user_id: int, quiz_set_id: int, session: Session) -> QuizSet | None:
    """
    Fetch a QuizSet by id, scoped to user_id. Returns None when the set
    does not exist or belongs to another user -- never raises on a
    mismatched id, because the dashboard must silently skip another
    user's rows.
    """

    statement = select(QuizSet).where(
        QuizSet.id == quiz_set_id,  # pyright: ignore[reportArgumentType]
        QuizSet.user_id == user_id,  # pyright: ignore[reportArgumentType]
    )
    return session.exec(statement).first()


def list_today_quiz_sets(user_id: int, today: date, session: Session) -> list[QuizSet]:
    """
    Return this user's QuizSet rows with generated_on == today, ordered by
    entry_id so a stable ordering reaches the dashboard.
    """

    statement = (
        select(QuizSet)
        .where(
            QuizSet.user_id == user_id,  # pyright: ignore[reportArgumentType]
            QuizSet.generated_on == today,  # pyright: ignore[reportArgumentType]
        )
        .order_by(QuizSet.entry_id)  # pyright: ignore[reportArgumentType]
    )
    return list(session.exec(statement).all())


