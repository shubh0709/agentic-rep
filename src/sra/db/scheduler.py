"""Spaced-repetition interval math and the persistence that hangs off it."""

from datetime import date

from sqlmodel import Session

from sra.db.models import ReviewSchedule

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
    raise NotImplementedError


def schedule_reviews_for_entry(entry_id: int, studied_on: date, session: Session) -> list[ReviewSchedule]:
    """
    Persist the six ReviewSchedule rows for a newly created entry and
    return them.

    Concept: calls compute_review_dates(), wraps each pair in a
    ReviewSchedule(status="pending"), and commits them in one
    transaction -- a batch-insert pattern you'll see again once more
    tables show up in later phases.
    """
    raise NotImplementedError
