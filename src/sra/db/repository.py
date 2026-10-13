"""
Data-access functions. Every function is scoped by `user_id` and
never trusts a caller-supplied row's ownership -- that discipline
matters more once Phase 3 adds real multi-user auth, but it costs
nothing to establish now while there's only one local user.
"""

from datetime import UTC, date, datetime, time

from sqlmodel import Session, String, cast, or_, select

from sra.db.models import ReviewSchedule, StudyEntry, StudyEntryCreate
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
