"""Spaced-repetition interval math and the persistence that hangs off it."""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta
from sqlmodel import Session

from sra.db.models import ReviewSchedule, ReviewScheduleCreate

# (interval_label, offset_in_days). "1mo"/"3mo"/"6mo" are handled specially
# in compute_review_dates -- see its docstring -- this list only drives the
# day-based intervals plus the label order every caller should expect back.
INTERVALS: list[tuple[str, int | None]] = [
    ("1d", 1),
    ("3d", 3),
    ("7d", 7),
    ("1mo", None),
    ("3mo", None),
    ("6mo", None),
]


def compute_review_dates(studied_on: date) -> list[tuple[str, date]]:
    """
    Return the six (interval_label, scheduled_date) pairs for an
    entry studied on `studied_on`, in the fixed order 1d, 3d, 7d,
    1mo, 3mo, 6mo.

    Concept: pure function, no I/O -- the whole spaced-repetition
    schedule is deterministic calendar math, which is exactly what
    makes it cheap to unit test exhaustively (see
    tests/unit/test_scheduler.py).

    The day-based intervals (1d/3d/7d) are simple `studied_on +
    timedelta(days=N)`. The month-based ones (1mo/3mo/6mo) need real
    calendar arithmetic, not `+ 30/90/180 days` -- naive day-counting
    drifts across months of different lengths. Look at Python's
    `calendar.monthrange` or the third-party `python-dateutil`
    package's `relativedelta` for adding calendar months correctly,
    and think about what should happen when `studied_on` is the 31st
    of a month and the target month is shorter (e.g. Jan 31 -> Feb).
    """

    dates: list[tuple[str, date]] = []

    for label, offset in INTERVALS:
        if offset is not None:
            scheduled_date = studied_on + timedelta(days=offset)
        else:
            months: int = {
                "1mo": 1,
                "3mo": 3,
                "6mo": 6,
            }[label]

            scheduled_date = studied_on + relativedelta(months=months)

        dates.append((label, scheduled_date))

    return dates


def schedule_reviews_for_entry(entry_id: int, studied_on: date, session: Session) -> list[ReviewSchedule]:
    """
    Persist the six ReviewSchedule rows for a newly created entry and
    return them.

    Concept: calls compute_review_dates(), wraps each pair in a
    ReviewSchedule(status="pending"), and commits them in one
    transaction -- a batch-insert pattern you'll see again once more
    tables show up in later phases.
    """
    rows = [
        ReviewSchedule.model_validate(
            ReviewScheduleCreate(entry_id=entry_id, interval_label=label, scheduled_date=scheduled_date)
        )
        for label, scheduled_date in compute_review_dates(studied_on)
    ]

    # add() only stages each row in the session's pending changes -- nothing
    # is sent to the DB yet, so a mid-loop error here can't leave partial rows.
    for row in rows:
        session.add(row)

    # One commit for all six rows -- a single INSERT transaction, not six
    # separate ones. This is the "one transaction" the docstring above refers to.
    session.commit()

    # commit() writes to the DB but doesn't automatically update the Python
    # objects with DB-generated values (like the autoincremented `id`).
    # refresh() re-reads each row from the DB so `row.id` etc. are populated
    # on the objects we're about to return.
    for row in rows:
        session.refresh(row)

    return rows
