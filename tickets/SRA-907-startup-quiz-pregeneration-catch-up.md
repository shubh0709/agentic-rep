# SRA-907 — Catch up quiz pre-generation when the API starts

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** verified

## Summary

Runs the existing, idempotent daily quiz pre-generation job once when the API scheduler starts, then keeps its normal 04:00 UTC cron schedule. Today the job is registered only as a cron trigger, so a process stopped at 04:00 UTC and started later does not run until the following day — entries already due today remain without a `QuizSet` even though generation itself works. **Before:** starting Uvicorn at 17:00 UTC leaves today's due entries without quizzes until 04:00 UTC tomorrow. **After:** the scheduler immediately queues the same idempotent job, which creates only the missing quiz sets; the cron continues at 04:00 UTC on later days and restarting does not duplicate quiz sets.

## Unlocks

- Reliable dashboard quiz availability: a server started after the daily cron no longer leaves due entries without quizzes for the rest of the day.
- SRA-905 verification gets a deterministic way to populate a due entry's quiz set without waiting for a cron boundary.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/jobs/digest_scheduler.py` | edit | Register `sra.pregenerate` with an immediate first run while preserving its daily cron trigger, single-instance limit, and coalescing. |
| `tests/unit/test_digest_scheduler.py` | edit | Isolate scheduler registration and prove the first run is immediate, the recurring trigger remains 04:00 UTC, disabled mode stays inert, and restart-safe settings are retained. |
| `tests/integration/test_phase5_scheduler.py` | edit | Exercise the application lifespan with substituted inference/session boundaries; prove a due entry receives one quiz set after startup and no duplicate after restart. |
| `tickets/README.md`, `PROGRESS.md` | edit | Track the regression and record actual verification evidence. |

## Context

The configured database contains **six entries**. Three older entries have quiz sets, while one entry whose first review is due today has no quiz set. The focused Phase 9 generation suite passes, and a live authenticated dashboard request returns all six entries — so neither the database connection nor the quiz graph is the missing boundary. `start_scheduler()` registered `sra.pregenerate` for 04:00 UTC with no initial run.

## Contracts

- When `scheduler_enabled=True`, `start_scheduler()` queues one pre-generation run immediately and retains the daily 04:00 UTC cron schedule.
- The immediate run reuses `build_pregeneration_job()` and `pregenerate_quizzes_for_day()`; it does not introduce a second generation implementation.
- Existing per-entry idempotency remains the duplicate guard across restarts and repeated runs.
- `max_instances=1`, `coalesce=True`, shutdown behavior, demo reset, and the wake-up interval remain unchanged.
- When `scheduler_enabled=False`, no scheduler or catch-up work starts.
- Tests replace OpenAI and wall-clock waiting — the integration test waits on a deterministic synchronization primitive, not a sleep. No real model call, network, hardware, or production database is used.

## Acceptance criteria

- [x] Starting after 04:00 UTC produces a quiz set for an entry due today without waiting for the next day under substituted inference
- [x] Restarting after the catch-up leaves exactly one quiz set for that entry
- [x] The next recurring run remains scheduled for 04:00 UTC
- [x] Disabled scheduler mode performs no catch-up
- [x] Existing scheduler, quiz pre-generation, Phase 9 generation, lint, and type checks pass

## Tests

```bash
python -m pytest tests/unit/test_digest_scheduler.py tests/integration/test_phase5_scheduler.py -q
python -m pytest tests/unit/test_quiz_pregeneration.py tests/integration/test_phase9_quiz_graph_e2e.py -q
ruff check src/sra/jobs/digest_scheduler.py tests/unit/test_digest_scheduler.py tests/integration/test_phase5_scheduler.py
pyright src/sra/jobs/digest_scheduler.py
```

The unit test inspects the registered `sra.pregenerate` job and expects its first `next_run_time` to be at scheduler startup rather than the next 04:00 UTC boundary. Both new tests fail against the previous registration behavior.

## Manual verification

- [ ] Back up the configured database, start the API after 04:00 UTC with a due entry that has no quiz set, and watch for the pre-generation summary. Confirm the entry gains one quiz set and questions, restart once, and confirm its quiz-set count stays one.
- [ ] This check uses the configured OpenAI account and must not be presented as completed until explicitly run.

**Verification (2026-10-17, macOS, Python 3.13.12, SQLite and APScheduler with OpenAI replaced in automated tests):**

- Scheduler tests **19 passed**; quiz pre-generation regressions **19 passed**; Ruff clean; Pyright zero errors.
- The integration test created one quiz set for a due entry on startup and retained exactly one after an application restart.
- Pre-implementation the tests failed because the cron registration lacked `next_run_time` and startup never signalled the pre-generation event; adding an immediate first run to the existing cron job made both pass.
- The live reload exercised the catch-up path, but the configured OpenAI draft call still failed and **no live quiz row was created**.
- After explicit approval, one non-persisting real-model diagnostic returned **10 structured drafts**. SRA-908 fixed the downstream transcript-evidence gap; the reload-triggered startup catch-up then persisted **one quiz set with nine verifier-approved questions** for the previously missing due entry.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).