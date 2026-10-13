# SRA-102 — Spaced-repetition scheduler

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** High
**Blocked by:** none
**Blocks:** SRA-103

## Summary

Implement the interval math in `src/sra/db/scheduler.py`: given a date something was studied, compute the six spaced-repetition checkpoint dates (1 day, 3 days, 7 days, 1 month, 3 months, 6 months) and persist them.

## Files

- `src/sra/db/scheduler.py`

## What to implement

- `compute_review_dates(studied_on: date) -> list[tuple[str, date]]` — pure function, no DB access. Returns the six `(interval_label, scheduled_date)` pairs in order: `1d, 3d, 7d, 1mo, 3mo, 6mo`.
- `schedule_reviews_for_entry(entry_id: int, studied_on: date, session: Session) -> list[ReviewSchedule]` — calls `compute_review_dates`, wraps each pair in a `ReviewSchedule(status="pending")`, persists them, and returns the six rows.

## Where to look

The docstrings in `scheduler.py` spell out the trap to avoid: **don't** compute `1mo`/`3mo`/`6mo` with `+ 30/90/180 days` — that drifts across months of different lengths. Use real calendar-month arithmetic (`calendar.monthrange`, or the `dateutil.relativedelta` package if you want to add it as a dependency) and think through what should happen when `studied_on` is the 31st of a month and the target month is shorter.

## Acceptance criteria

- [ ] `compute_review_dates` always returns exactly 6 pairs, labels in the fixed order `1d, 3d, 7d, 1mo, 3mo, 6mo`
- [ ] Day-based offsets (`1d`, `3d`, `7d`) are exact
- [ ] Month-based offsets land on a real calendar date across month-length boundaries (e.g. studied Jan 31 → the `1mo` checkpoint is a valid February or March date, never "Feb 31")
- [ ] Month-based offsets correctly roll over the year (e.g. studied Nov 15 → `3mo` lands in February of the *next* year)
- [ ] `schedule_reviews_for_entry` persists exactly 6 rows, all `status="pending"`, all linked to the given `entry_id`

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_scheduler.py -v
```

Expected once implemented: 5 passed.

## Manual verification

```bash
python3 -c "
from datetime import date
from sra.db.scheduler import compute_review_dates
for label, d in compute_review_dates(date(2026, 1, 31)):
    print(label, d)
"
```

Confirm the `1mo` line prints a sane date (not an error, not Feb 31st).

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `pytest tests/unit/test_scheduler.py -v` — 5 passed
- [ ] Full suite: `pytest -q` (SRA-103's repository tests that call `schedule_reviews_for_entry` should now get further before failing — check their failure has moved past scheduler code)
