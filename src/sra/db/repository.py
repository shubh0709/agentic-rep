"""
Data-access functions. Every function is scoped by `user_id` and
never trusts a caller-supplied row's ownership -- that discipline
matters more once Phase 3 adds real multi-user auth, but it costs
nothing to establish now while there's only one local user.
"""

from datetime import date

from sqlmodel import Session

from sra.db.models import ReviewSchedule, StudyEntry, StudyEntryCreate


def create_entry(*, entry_create: StudyEntryCreate, user_id: int, session: Session) -> StudyEntry:
    """
    Insert a StudyEntry scoped to user_id, then call
    scheduler.schedule_reviews_for_entry for it so every entry is
    born with its six review checkpoints already in place.

    Takes a StudyEntryCreate (not loose kwargs) so id/user_id/created_at
    can never be supplied by a caller -- see the convention note atop
    db/models.py.
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


def mark_review_done(user_id: int, review_id: int, status: str, session: Session) -> ReviewSchedule:
    """
    Flip a review's status ("done" or "skipped") and stamp
    completed_at (use `datetime.now(timezone.utc)` -- this project's
    SQLModel setup rejects naive datetimes on write, see
    db/models.py's `_utcnow` helper). Raise a plain ValueError if
    review_id doesn't resolve to a review owned (via its parent
    StudyEntry) by user_id -- never silently no-op on a mismatched id.
    """
    raise NotImplementedError
