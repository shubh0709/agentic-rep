from datetime import date

from sra.db.models import StudyEntry
from sra.db.scheduler import compute_review_dates, schedule_reviews_for_entry


def test_compute_review_dates_returns_six_labels_in_order():
    dates: list[tuple[str, date]] = compute_review_dates(date(2026, 1, 1))
    labels = [label for label, _ in dates]
    assert labels == ["1d", "3d", "7d", "1mo", "3mo", "6mo"]


def test_compute_review_dates_day_offsets_are_correct():
    dates = dict(compute_review_dates(date(2026, 1, 1)))
    assert dates["1d"] == date(2026, 1, 2)
    assert dates["3d"] == date(2026, 1, 4)
    assert dates["7d"] == date(2026, 1, 8)


def test_compute_review_dates_handles_end_of_month_rollover():
    # Jan 31 + 1 month must land on a real calendar date, never "Feb 31".
    dates = dict(compute_review_dates(date(2026, 1, 31)))
    assert dates["1mo"].day <= 29 or dates["1mo"].month != 2
    assert dates["1mo"].month in (2, 3)


def test_compute_review_dates_handles_year_rollover():
    dates = dict(compute_review_dates(date(2026, 11, 15)))
    assert dates["3mo"].year == 2027
    assert dates["3mo"].month == 2


def test_schedule_reviews_for_entry_persists_six_pending_rows(session, seeded_user):
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Photosynthesis",
        summary="Light + dark reactions",
        raw_transcript="today I learned about photosynthesis",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    assert entry.id is not None
    rows = schedule_reviews_for_entry(entry.id, date(2026, 1, 1), session)

    assert len(rows) == 6
    assert all(r.status == "pending" for r in rows)
    assert all(r.entry_id == entry.id for r in rows)
    assert {r.interval_label for r in rows} == {"1d", "3d", "7d", "1mo", "3mo", "6mo"}
