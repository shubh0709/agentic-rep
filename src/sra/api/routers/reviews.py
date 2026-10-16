"""Calendar / timeline views over ``ReviewSchedule`` rows.

The single new route ``GET /api/v1/reviews/upcoming`` returns the pending
reviews scheduled for a date window so the dashboard's "upcoming topics on
various days" panel can render a day-grouped timeline. Today and earlier are
already served by ``GET /api/v1/digest``; this endpoint picks up where that
one stops so the two never overlap.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from sra.api.deps import get_session, require_user
from sra.api.schemas import EntryOut, ReviewDayOut, ReviewOut
from sra.db.models import StudyEntry, User
from sra.db.repository import list_upcoming_reviews

router = APIRouter(prefix="/reviews", tags=["reviews"])


def _today_utc() -> date:
    """Today's date in UTC.

    Isolated so tests can monkeypatch a fixed date without reaching into the
    stdlib ``datetime`` module -- this function is the only time-of-day seam
    this router relies on.
    """
    return datetime.now(UTC).date()


@router.get("/upcoming", response_model=list[ReviewDayOut])
def upcoming_reviews(
    days: int = Query(
        30,
        ge=1,
        le=365,
        description="How many days ahead of today to include (inclusive of `days` days from tomorrow).",
    ),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[ReviewDayOut]:
    """Return pending reviews scheduled between tomorrow and ``today + days``.

    The response is bucketed by ``scheduled_date`` in the API so the dashboard
    can render day tiles without re-grouping on the client. Empty days are
    omitted -- a user with nothing scheduled on a particular date should not
    see a blank tile, only the surrounding days with content.
    """

    assert user.id is not None
    today = _today_utc()
    # Strictly after "due today" so the digest endpoint stays the source of
    # truth for today's pending reviews.
    from_date = today + timedelta(days=1)
    to_date = today + timedelta(days=days)

    reviews = list_upcoming_reviews(
        user_id=user.id,
        from_date=from_date,
        to_date=to_date,
        session=session,
    )

    grouped: dict[date, list[ReviewOut]] = {}
    for review in reviews:
        entry = session.get(StudyEntry, review.entry_id)
        assert entry is not None
        assert entry.id is not None
        assert review.id is not None
        review_out = ReviewOut(
            id=review.id,
            entry=EntryOut(
                id=entry.id,
                topic=entry.topic,
                subtopic=entry.subtopic,
                tags=entry.tags,
                summary=entry.summary,
                raw_transcript=entry.raw_transcript,
                created_at=entry.created_at,
            ),
            interval_label=review.interval_label,
            scheduled_date=review.scheduled_date,
            status=review.status,
        )
        grouped.setdefault(review.scheduled_date, []).append(review_out)

    # Yield days in chronological order so the dashboard can stop accumulating
    # once it has rendered the requested window.
    return [
        ReviewDayOut(date=day, reviews=grouped[day])
        for day in sorted(grouped.keys())
    ]