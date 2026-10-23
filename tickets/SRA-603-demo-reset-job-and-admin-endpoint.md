# SRA-603 — Demo reset job and admin endpoint

**Phase:** 6 — Open-source packaging · **Status:** implemented — 2026-10-14

## Summary

Implement `src/sra/jobs/demo_reset.py` (`reset_demo_data(retention_days, *, as_of_utc, session_factory=None)` → `{"deleted": int}`), wire the job into the existing APScheduler when `Settings.demo_mode` is `True`, and expose `POST /api/v1/admin/demo/reset` as an admin-only on-demand reset. Outside demo mode the job raises a typed `DemoModeDisabledError` before touching the database, so a self-hoster cannot accidentally wipe real data. The job is the only component that distinguishes demo from self-host data: the cascade relies on `User.is_demo_user == True` (added by SRA-601) and the OAuth flow never carries that flag. Resetting is destructive and needs admin authorization rather than being a casual background side effect, so the scheduler tick is once an hour, not once a minute, and the on-demand endpoint requires `require_admin`. The job is a thin wrapper around SRA-601's `list_old_demo_users` and `delete_user_cascade` helpers and reuses the Phase 5 `start_scheduler`/`stop_scheduler` lifecycle. It lives in its own module because the two schedulers differ in scope, cadence, and failure path.

## Unlocks

- The hosted demo registers an hourly APScheduler job that calls `reset_demo_data(retention_days=7)`; stale demo accounts and every dependent entry, schedule, quiz set, attempt, and answer disappear without operator intervention.
- An admin can force a reset out of band: `curl -X POST -H "Authorization: Bearer $ADMIN_TOKEN" http://demo.example.com/api/v1/admin/demo/reset` returns `{"deleted": N}`.
- Self-host deployments get a hard guarantee that the destructive path is unreachable unless `DEMO_MODE=true` is set.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/jobs/demo_reset.py`](../src/sra/jobs/demo_reset.py) | new | `DemoModeDisabledError(RuntimeError)`, `reset_demo_data(retention_days: int, *, as_of_utc: datetime \| None = None, session_factory: SessionFactory \| None = None) -> dict[str, int]` returning `{"deleted": int}`, and `build_demo_reset_job(session_factory, retention_days)` returning a callable for APScheduler. When `Settings.demo_mode == False` it raises `DemoModeDisabledError` before touching the database. |
| [`src/sra/api/routers/admin.py`](../src/sra/api/routers/admin.py) | edit | Adds `POST /admin/demo/reset` behind the existing `Depends(require_admin), Depends(limit_authenticated)` chain. Calls `reset_demo_data(retention_days=settings.demo_data_retention_days, as_of_utc=datetime.now(UTC))` and returns `{"deleted": count}`; raises `HTTPException(503, detail="Demo mode is not enabled")` when demo mode is off. |
| [`src/sra/jobs/digest_scheduler.py`](../src/sra/jobs/digest_scheduler.py) | edit | `start_scheduler` registers the demo reset interval **only** when `Settings.demo_mode == True`, with `id="sra.demo_reset"` and `seconds=settings.demo_reset_interval_s`. `stop_scheduler` is unchanged. |
| [`src/sra/config/`](../src/sra/config/) | edit | Adds `demo_reset_interval_s: int = Field(default=3600, ge=60)` to `Settings` — at most one tick per 60 seconds, hourly by default. |
| [`src/sra/api/rate_limit.py`](../src/sra/api/rate_limit.py) | edit | Under `demo_mode=True`, `RateLimitState.from_settings` divides `rate_limit_authenticated_capacity` and `rate_limit_anonymous_capacity` by 10 (clamped at 1) and the matching refill rates by 10. Self-hosters are unaffected unless they set `DEMO_MODE=true`. |
| [`tests/unit/test_demo_reset.py`](../tests/unit/test_demo_reset.py) | new | Raises `DemoModeDisabledError` when `demo_mode=False`; deletes only demo users older than the retention window; never deletes non-demo users; returns the count; idempotent on a second invocation inside the same window. |
| [`tests/unit/test_rate_limit_demo_mode.py`](../tests/unit/test_rate_limit_demo_mode.py) | new | Policy narrowing under `demo_mode=True` (capacity divided by 10 and clamped at 1, refill divided by 10) and unchanged behaviour under `demo_mode=False`. |
| [`tests/integration/test_phase6_demo_reset.py`](../tests/integration/test_phase6_demo_reset.py) | new | Through the live FastAPI app: admin `POST /admin/demo/reset` removes a seeded demo user's data and returns `{"deleted": 1}`; a non-admin caller gets `403`; an unauthenticated caller gets `401`. |
| [`tests/integration/test_phase6_scheduler_demo_reset.py`](../tests/integration/test_phase6_scheduler_demo_reset.py) | new | Boots the app under `demo_mode=True`, advances the scheduler clock, and asserts the demo reset interval job is registered and calls `reset_demo_data` exactly once. |

## Acceptance criteria

- [ ] `reset_demo_data` raises `DemoModeDisabledError` when `Settings.demo_mode == False`; the database is unchanged
- [ ] `reset_demo_data` deletes every demo user whose oldest related timestamp is older than `retention_days` and returns the count; non-demo users and fresh demo users are untouched
- [ ] `reset_demo_data` is idempotent within the same `as_of_utc` window (a second invocation right after the first returns `{"deleted": 0}` without touching the database)
- [ ] `POST /api/v1/admin/demo/reset` returns `{"deleted": N}` for an admin caller; returns `403` for an authenticated non-admin; returns `401` for an unauthenticated caller; returns `503` when `Settings.demo_mode == False`
- [ ] `start_scheduler` registers the demo reset interval only when `Settings.demo_mode == True`; it is absent when `Settings.demo_mode == False`
- [ ] The rate limiter's authenticated capacity and refill are divided by 10 (clamped at 1) under `demo_mode=True`; unchanged under `demo_mode=False`
- [ ] `Settings.demo_reset_interval_s` defaults to `3600`, rejects values `<60`
- [ ] All Phase 1/2/3/4/5 tests remain green; Ruff and Pyright pass with no new warnings

## Tests

```bash
pytest tests/unit/test_demo_reset.py tests/unit/test_rate_limit_demo_mode.py -v
pytest tests/integration/test_phase6_demo_reset.py tests/integration/test_phase6_scheduler_demo_reset.py -v
```

No test carries the `phase6` marker, so `pytest -m 'not phase6' -q` selects the whole 1261-test suite rather than Phases 1–5; use the commands above to isolate this ticket. Lint, type check, and the full command list: [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] With `DEMO_MODE=true` and `DEMO_DATA_RETENTION_DAYS=7`, `uvicorn sra.api.app:create_app --factory --port 8000` serves `POST /api/v1/admin/demo/reset`; an admin bearer token returns `{"deleted": 1}` with status `200` (or `{"deleted": 0}` when there is no demo data).
- [ ] The same request with a non-admin bearer token returns `403`.
- [ ] The same request with no `Authorization` header returns `401`.
- [ ] `DEMO_MODE=false DEMO_DATA_RETENTION_DAYS=7 python -c "from sra.jobs.demo_reset import reset_demo_data; reset_demo_data(7)"` raises `sra.jobs.demo_reset.DemoModeDisabledError: reset_demo_data requires Settings.demo_mode=True`.

**Verification:**

- **Date and environment:** 2026-10-14, macOS, Python 3.13.
- **Result:** `PROGRESS.md` records SRA-604/603 as "demo-reset job, `POST /admin/demo/reset`; 17 doc + **24** tests".
- **Known caveat:** this ticket's own verification-record block was never filled in — the line above is the phase-level entry, not a per-command log.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
