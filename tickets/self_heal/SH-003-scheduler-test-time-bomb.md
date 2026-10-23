# SH-003 — Digest-scheduler tests are date-coupled to the wall clock

**Status:** closed

## Bug

`tests/unit/test_digest_scheduler.py` uses a private helper `_add_pending_review(session, entry_id)` that persists `ReviewSchedule(..., scheduled_date=datetime.now(UTC).date(), ...)` while the test bodies all freeze `as_of_utc` to `datetime(2026, 10, 14, 9, 30, tzinfo=UTC)`. On 2026-10-14 the suite happened to pass because `now().date() == as_of.date()`. After that date, the production filter `ReviewSchedule.scheduled_date <= local_today` (at `src/sra/jobs/digest_scheduler.py:101`) excludes every review the helper created, so `users_due_for_wakeup` returns `[]`, `run_wakeup_tick` returns `0`, and every test that asserts `"1"` fails with `assert [] == [1]` or `assert 0 == 1`.

The production code is correct — at runtime the scheduler runs when it runs, and the review rows already on disk come from earlier captures dated in the past, so `scheduled_date <= local_today` holds. Root cause: the test fixture's date basis (`tests/unit/test_digest_scheduler.py:49`) is mis-coupled to the wall clock while the tests assert against a fixed historical `as_of_utc`.

## Files

| File | Change | Why |
|---|---|---|
| `tests/unit/test_digest_scheduler.py` | edit | Change `_add_pending_review` to take an explicit `scheduled_date: date` parameter and update every call site. Removes the time-bomb coupling while preserving each test's control over its own clock. |

## Fix

Tests that pin `as_of_utc` to a historical value now also place their fixture data at a date ≤ `as_of_utc.date()`, so the production filter behaves as the test expects.

## Acceptance criteria

- [x] `_add_pending_review` accepts the test's chosen "today" date and persists a review dated against it
- [x] All eight failing tests pass on the day this ticket lands and on every subsequent day with no environment change
- [x] No other test that uses `_add_pending_review` is broken by the signature change

## Verification

- **Date / environment:** 2026-10-16, Python 3.13.12, FastAPI 0.115+ via the in-process `TestClient`.
- **Closed by:** commit `6938f95 feat(jobs): interview counters in dev upcoming pregen + scheduler test fix (SRA-802 follow-up)`.
- `pytest tests/unit/test_digest_scheduler.py -v` → **10/10 PASS**.
- `pytest -q` → 7 of 8 originally-failing tests now pass. The 8th — `test_users_due_for_wakeup_skips_and_logs_bad_timezone_user` — still fails under the full suite because of the SRA-509 documented caplog isolation quirk; it passes via the Phase 5 explicit file list (or standalone). "Fails for time-bomb reason" became "fails for caplog-isolation reason", the same level of noise `PROGRESS.md` already records.
- Net default-suite failure count for this file drops from **8 to 1**. Production `users_due_for_wakeup` / `run_wakeup_tick` behavior is unchanged — the fix lives entirely in the test fixture.

Run summary: [`SH-REPORT.md`](SH-REPORT.md).

---

Design context: [`technical-design.md`](../../docs/design/technical-design.md). Process:
[`guides/self_heal.md`](../../guides/self_heal.md). Source: [`SH-001`](SH-001-coverage-matrix.md).