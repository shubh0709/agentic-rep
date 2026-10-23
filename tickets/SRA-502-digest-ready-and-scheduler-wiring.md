# SRA-502 — Digest ready endpoint and APScheduler wiring

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Add `GET /api/v1/digest/ready` and lifespan-owned APScheduler jobs for daily quiz pregeneration and per-minute wake-up ticks. The endpoint tells the desktop client whether today's due reviews have a quiz; the scheduler updates `wakeup_notification_sent_at` once per user's local day. `pregenerate_quizzes_for_day(today, session)` and `run_wakeup_tick(session)` share the repository's readiness queries so polling and delivery answer the same question. The server owns time; SRA-507 owns polling and speech.

## Unlocks

- The server can prepare quizzes and expose an authenticated readiness signal without requiring explicit job commands, while stopping cleanly with the FastAPI process.

## Files

| File | Change | Why |
|---|---|---|
| [`pyproject.toml`](../pyproject.toml), [`requirements-lock.txt`](../requirements-lock.txt) | edit | Adds and pins `apscheduler>=3.10,<4` (`APScheduler==3.11.3`, `tzlocal==5.4.4`). |
| [`src/sra/config/`](../src/sra/config/) | edit | Adds `scheduler_pregen_hour_utc=4`, `scheduler_wakeup_interval_s=60`, and `scheduler_enabled=True`. |
| [`src/sra/jobs/digest_scheduler.py`](../src/sra/jobs/digest_scheduler.py) | new | Implements `users_due_for_wakeup`, `run_wakeup_tick`, job builders, and scheduler lifecycle. |
| [`src/sra/api/routers/digest.py`](../src/sra/api/routers/digest.py) | edit | Adds protected `GET /api/v1/digest/ready` returning `{"ready": bool}`. |
| [`src/sra/api/app.py`](../src/sra/api/app.py) | edit | Starts and stops the scheduler in the FastAPI lifespan, symmetrically when disabled. |
| [`tests/integration/test_phase5_scheduler.py`](../tests/integration/test_phase5_scheduler.py) | new | Verifies real app lifespan registration and shutdown. |
| [`tests/unit/test_digest_scheduler.py`](../tests/unit/test_digest_scheduler.py), [`tests/unit/test_digest_routes.py`](../tests/unit/test_digest_routes.py) | edit | Cover timezone selection, idempotency, exception isolation, and per-user readiness. |

## Acceptance criteria

- [x] `Settings` carries the three new scheduler fields with documented defaults and validators
- [x] `users_due_for_wakeup` returns the right users for: (a) a tick just after the user's local `preferred_review_time`, (b) a tick already past `preferred_review_time` for that user today (excluded), (d) a tick the next day (included again because `wakeup_notification_sent_at` is older than today)
- [x] `run_wakeup_tick` is idempotent within a local day and continues past a per-user exception
- [x] `GET /api/v1/digest/ready` returns `{"ready": true}` when due reviews exist AND a `QuizSet` for today exists; `{"ready": false}` otherwise
- [x] A real `create_app()` lifespan starts the scheduler when `scheduler_enabled=True` and calls `scheduler.shutdown(wait=False)` on exit; the symmetric no-op path runs when `scheduler_enabled=False`
- [x] `apscheduler` is declared in `pyproject.toml`, pinned in `requirements-lock.txt`, and `pip check` reports no conflicts
- [x] All Phase 1/2/3/4/5-preceding tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_digest_scheduler.py -v
pytest tests/unit/test_digest_routes.py -v
pytest tests/integration/test_phase5_scheduler.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

Use a controlled UTC clock and per-user fixtures; do not depend on wall time. The phase regression must retain the documented Phase 4 baseline.

## Manual verification

- [ ] Start the app with a short wake-up interval, exercise `GET /api/v1/digest/ready` for two users, and confirm readiness is per-user and no quiz is regenerated.
- [ ] Trigger `pregenerate_quizzes_for_day(today=date.today(), session=...)`, record its summary, stop the process, and confirm the scheduler shuts down without an in-flight job blocking exit.

**Verification:**
- **2026-10-14, macOS, Python 3.13.12:** focused run reported **24 passed** (10 scheduler unit, 7 digest-route unit, 6 integration, 1 route-policy audit re-run).
- `pytest -m 'not phase5' --tb=no -q` recorded **482 passed, 13 failed, 14 skipped, 188 deselected**. The 13 were documented Phase 4 limitations; the stashed baseline had **16 failed**, so this implementation fixed three failures without introducing regressions.
- `ruff check .` passed; `pyright` reported **0 errors, 0 warnings, 0 informations**. `pip check` found one pre-existing unrelated conflict: `google-genai 2.28.0` requires `tenacity<9.2.0,>=8.2.3`, but `tenacity 9.2.1` was installed; uninstalling APScheduler reproduced it. APScheduler introduced no conflict.
- New module exports `users_due_for_wakeup`, `run_wakeup_tick`, `build_pregeneration_job`, `build_wakeup_job`, `start_scheduler`, and `stop_scheduler`. `GET /api/v1/digest/ready` is audited as user/`limit_authenticated`.
- The lifespan calls `start_scheduler(app)` and `stop_scheduler(app)`; with `SCHEDULER_ENABLED=false`, both are no-ops and `app.state.scheduler` remains `None`. Settings and dependency versions match the declared contract.
- A test setup bug was fixed by issuing `issue_app_credential` for `other_user`; its assertion remained unchanged. Although the body said `BlockingScheduler`, implementation uses `BackgroundScheduler` to avoid hanging the lifespan thread while preserving `start()`/`shutdown(wait=False)`.
- Manual Uvicorn smoke testing was deferred; test-client lifespan runs exercised startup and clean shutdown. The pre-existing `tenacity` conflict remains out of scope.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
