# SRA-102 — Spaced-repetition scheduler

**Phase:** 1 — Core loop · **Status:** implemented

## Summary

The interval math lives in `src/sra/db/scheduler.py`: given the date something
was studied, compute the six spaced-repetition checkpoint dates (1 day, 3 days,
7 days, 1 month, 3 months, 6 months) and persist them. One pure function does
the arithmetic and one function writes the rows, so the repository layer never
re-invents calendar logic. The interesting part is the trap the module docstring
calls out: **do not** compute `1mo`/`3mo`/`6mo` with `+ 30/90/180 days`.

## Unlocks

- SRA-103's `create_entry` can call `schedule_reviews_for_entry` and get six
  pending checkpoints per entry.
- SRA-104's digest has dated rows to return, and SRA-105's ingestion route has
  something to schedule against.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/db/scheduler.py`](../src/sra/db/scheduler.py) | edit | `compute_review_dates(studied_on)` stays pure; `schedule_reviews_for_entry(entry_id, studied_on, session)` wraps each pair in a `ReviewSchedule(status="pending")`, persists, and returns the six rows. |
| [`src/sra/db/models.py`](../src/sra/db/models.py) | read | `ReviewSchedule` field names — the docstrings in `scheduler.py` repeat them. |
| [`tests/unit/test_scheduler.py`](../tests/unit/test_scheduler.py) | run | 5 scaffold-supplied tests, including the month-length boundary cases. |

## Acceptance criteria

- [ ] `compute_review_dates` always returns exactly 6 pairs, labels in the fixed
  order `1d, 3d, 7d, 1mo, 3mo, 6mo`
- [ ] Day-based offsets (`1d`, `3d`, `7d`) are exact
- [ ] Month-based offsets land on a real calendar date across month-length
  boundaries (e.g. studied Jan 31 → the `1mo` checkpoint is a valid February or
  March date, never "Feb 31")
- [ ] Month-based offsets correctly roll over the year (e.g. studied Nov 15 →
  `3mo` lands in February of the *next* year)
- [ ] `schedule_reviews_for_entry` persists exactly 6 rows, all `status="pending"`,
  all linked to the given `entry_id`

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_scheduler.py -v
```

Expected once implemented: 5 passed.

## Manual verification

- [ ] The `1mo` line prints a sane date (not an error, not Feb 31st):

      ```bash
      python3 -c "
      from datetime import date
      from sra.db.scheduler import compute_review_dates
      for label, d in compute_review_dates(date(2026, 1, 31)):
          print(label, d)
      "
      ```

- [ ] `pytest -q` — SRA-103's repository tests should now get further before
      failing; check their failure has moved past scheduler code.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).