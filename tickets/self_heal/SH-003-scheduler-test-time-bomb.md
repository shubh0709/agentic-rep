SH-003 — Digest-scheduler tests are date-coupled to the wall clock

**Type:** Test / expectation defect (production code is correct)
**Source:** Application Self-Heal pass 1 (`tickets/self_heal/SH-001-coverage-matrix.md`)
**Priority:** High (8 tests fail today; the breakage is silently date-bombed and will fail every day after 2026-10-14)
**Status:** Confirmed

## Summary

`tests/unit/test_digest_scheduler.py` uses a private helper
`_add_pending_review(session, entry_id)` that persists
`ReviewSchedule(..., scheduled_date=datetime.now(UTC).date(), ...)` while the
test bodies all freeze `as_of_utc` to `datetime(2026, 10, 14, 9, 30, tzinfo=UTC)`.
On 2026-10-14 the suite happened to pass because `now().date() == as_of.date()`.
After that date, the production filter
`ReviewSchedule.scheduled_date <= local_today` (in
`src/sra/jobs/digest_scheduler.py:101`) excludes every review the helper created,
so `users_due_for_wakeup` returns `[]`, `run_wakeup_tick` returns `0`, and
every test that asserts "1" fails.

The production code is correct — at runtime, the scheduler runs at the
moment it runs, and the review rows already on disk come from earlier
captures dated in the past, so `scheduled_date <= local_today` holds. The
bug is purely in the test fixture's date basis.

## Preconditions / minimal reproduction

1. Run the focused set after 2026-10-14:
   `pytest tests/unit/test_digest_scheduler.py -v`.
2. Eight tests fail with messages like `assert [] == [1]` or `assert 0 == 1`.
3. Running with a `datetime.now()` source patched to 2026-10-14 makes them
   pass again — proof that the production code is sound and only the test
   fixture's date basis is mis-coupled.

## Expected behavior

Tests that pin `as_of_utc` to a historical value should also place their
fixture data at a date ≤ `as_of_utc.date()` so the production filter
behaves as the test expects.

## Actual behavior (sanitized)

```
>       assert [u.id for u in due] == [user.id]
E       assert [] == [1]
>       assert count == 1
E       assert 0 == 1
```

## Root cause

`_add_pending_review` (in `tests/unit/test_digest_scheduler.py:49`) hard-codes
`scheduled_date=datetime.now(UTC).date()`. The scheduler under
test treats `scheduled_date` as the date a review becomes due in the
user's local timezone; passing the wall-clock date while the test asserts
against a fixed historical `as_of_utc` decouples the two and the production
filter rejects the fixture row.

## Acceptance criteria

- [ ] `_add_pending_review` accepts the test's chosen "today" date and
      persists a review dated against it.
- [ ] All eight failing tests pass on the day this ticket lands and on
      every subsequent day with no environment change.
- [ ] No other test that uses `_add_pending_review` is broken by the
      signature change.

## Files to change

| File | Action | Reason |
|---|---|---|
| `tests/unit/test_digest_scheduler.py` | change `_add_pending_review` to take an explicit `scheduled_date: date` parameter (and update every call site) | Removes the time-bomb coupling from the helper while preserving each test's ability to control its own clock. |

## Verification record

- **Date / environment:** 2026-10-16, Python 3.13.12, FastAPI 0.115+ via
  the in-process `TestClient`.
- **Closed by:** commit `6938f95 feat(jobs): interview counters in dev
  upcoming pregen + scheduler test fix (SRA-802 follow-up)`.
- **Commands:**
  - `pytest tests/unit/test_digest_scheduler.py -v` → 10/10 PASS.
  - `pytest -q` → 7 of 8 originally-failing tests now pass. The 8th —
    `test_users_due_for_wakeup_skips_and_logs_bad_timezone_user` —
    still fails under the full suite because of the SRA-509
    documented caplog isolation quirk; it passes when run via the
    Phase 5 explicit file list (or standalone). The "fails for
    time-bomb reason" semantics was replaced with "fails for
    caplog-isolation reason", which is the same level of noise
    `PROGRESS.md` already records.
- **Result:** Net default-suite failure count for `test_digest_scheduler.py`
  drops from 8 to 1 (the SRA-509 caplog test). Production
  `users_due_for_wakeup` / `run_wakeup_tick` behavior unchanged — the
  fix lives entirely in the test fixture.
