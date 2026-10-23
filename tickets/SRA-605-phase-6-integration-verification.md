# SRA-605 — Phase 6 integration verification

**Phase:** 6 — Open-source packaging · **Status:** verified — 2026-10-14

## Summary

Re-run the full repository test suite, the Phase 6 migration replay, the demo reset lifecycle, the rate-limit narrowing, the route-policy audit, the README / `.env.example` content checks, and the static Docker-file checks. Record the phase-level evidence in `PROGRESS.md` and in the Phase 6 entries of every prior ticket. This ticket introduces no new feature: it proves that SRA-601–604 together produce a self-hostable application, a working demo profile, tighter demo limits, and complete documentation, and that Phase 1–5 regressions stayed green. It inherits the verification pattern from the earlier phase-verification tickets (SRA-107, SRA-206, SRA-305, SRA-406, SRA-509): rerun the focused tests, rerun the integration tests, rerun the migration replay, verify the route audit, confirm Ruff and Pyright are clean, and record everything. Every new Phase 6 test landed with the ticket that produced the behaviour; SRA-605 only orchestrates the existing tests and manual checks.

## Unlocks

- The full `pytest -q` suite is green on the SRA-509-closed baseline plus every Phase 6 acceptance criterion from SRA-601–604; pre-existing Phase 3/4 failures stay unchanged in count.
- The Phase 6 migration chain (`c3d4e5f6a7b8` → revision `d4e5f6a7b8c9`) round-trips cleanly: upgrade adds `is_demo_user`, downgrade removes it, a second upgrade restores it, and every pre-Phase-6 row keeps `False`.
- The demo reset lifecycle is provable end to end: a demo user seeded through the API, an admin-triggered reset, the dependent tables empty, and the rate limiter narrowed.
- A clean clone plus `docker compose up` reaches `/api/health` returning 200, and `docker compose --profile demo up` boots a demo-mode instance (see SRA-602's verification record).

## Files

| File | Change | Why |
|---|---|---|
| [`tests/conftest.py`](../tests/conftest.py) | edit | Adds a `demo_user` fixture creating one `User` row with `is_demo_user=True` plus one seeded entry, schedule, quiz set, attempt, and answer. Existing fixtures are not modified. |
| [`tests/integration/test_phase6_end_to_end.py`](../tests/integration/test_phase6_end_to_end.py) | new | Composes the existing fixtures into one demo-instance lifecycle: `test_phase6_demo_reset_lifecycle_clears_all_dependents` seeds a demo user and a non-demo user with one row each in `study_entries`, `review_schedules`, `quiz_sets`, `quiz_questions`, `quiz_attempts`, and `quiz_answers`, calls `POST /api/v1/admin/demo/reset` as an admin with `DEMO_MODE=true`, asserts `{"deleted": 1}`, and asserts the demo user's dependents are gone while the non-demo user's survive. `test_phase6_migration_round_trip` runs `alembic upgrade head` from `c3d4e5f6a7b8` on a fresh isolated SQLite, `downgrade -1`, then `upgrade head`, checking a row inserted before the downgrade keeps `is_demo_user=False`. `test_rate_limit_narrows_under_demo_mode` compares limiter capacity and refill for `demo_mode=False` and `demo_mode=True`. |
| [`PROGRESS.md`](../PROGRESS.md) | edit | Replaces the Phase 6 single-line entry with a closing block matching the Phase 5 style: status line, scope, one-line summaries of SRA-601–605, acceptance evidence (test counts, lint/types, manual verification pointers), and a "Known limitations" section recording the pre-existing Phase 3/4 failures Phase 6 inherits. |
| [`tickets/README.md`](../tickets/README.md) | edit | Moves each Phase 6 board row from `Implemented — 2026-10-14` to `Done — 2026-10-14` and marks the Phase 6 dependency diagram as complete. |

## Acceptance criteria

- [ ] `pytest -q` is green (or no-worse than the pre-Phase-6 baseline) across Phases 1–6, with the documented pre-existing Phase 4 limitations unchanged in count
- [ ] `pytest -m 'not phase6' -q` keeps Phase 1–5 green — note that no test carries the `phase6` marker, so this command selects the same full 1261-test suite as `pytest -q` rather than isolating Phases 1–5
- [ ] `ruff check .` is clean
- [ ] `pyright` returns 0 errors, 0 warnings, 0 informations
- [ ] `tests/integration/test_phase6_migration.py` and `test_phase6_migration_replay.py` show the SRA-601 column lands and round-trips
- [ ] `tests/integration/test_phase6_end_to_end.py::test_phase6_demo_reset_lifecycle_clears_all_dependents` passes
- [ ] `tests/integration/test_phase6_end_to_end.py::test_rate_limit_narrows_under_demo_mode` passes
- [ ] `tests/integration/test_route_policy_audit.py::test_admin_routes_include_demo_reset` passes
- [ ] `PROGRESS.md` is updated with the Phase 6 closing entry matching the Phase 5 format
- [ ] `tickets/README.md` Phase 6 board statuses are updated to `Done — 2026-10-14`
- [ ] Pre-existing Phase 3/4 limitations remain unchanged in count

> **Note:** the route-policy criterion above names `test_admin_routes_include_demo_reset`, which was never created. The audit is covered by `test_every_mounted_route_has_an_explicit_authentication_and_limit_policy`, the only test in `tests/integration/test_route_policy_audit.py`, which enumerates `POST /api/v1/admin/demo/reset` under the admin policy with `limit_authenticated`.

## Tests

```bash
pytest tests/integration/test_phase6_end_to_end.py -v
pytest tests/integration/test_phase6_migration.py tests/integration/test_phase6_migration_replay.py -v
pytest tests/integration/test_route_policy_audit.py -v
pytest -q
```

No test carries the `phase6` marker, so `-m 'not phase6'` selects the same full 1261-test suite as `pytest -q`; the file-level commands above isolate the Phase 6 evidence. Lint, type check, and the full command list: [`tests/README.md`](../tests/README.md).

## Manual verification

| Check | Command | Expected |
|---|---|---|
| Clean-clone parity (SRA-602) | `git status --porcelain`; `docker --version` | empty porcelain; Docker available |
| Demo compose profile parses | `docker compose --profile demo config -q` | exits clean, no output |
| Demo reset refuses to run outside demo mode | `DEMO_MODE=false DEMO_DATA_RETENTION_DAYS=7 python -c "from sra.jobs.demo_reset import reset_demo_data; reset_demo_data(7)"` | raises `DemoModeDisabledError` |
| README commands parse | `grep -nE 'docker compose' README.md` | non-empty matches |
| Phase 6 env vars documented | `grep -nE 'DEMO_MODE' README.md .env.example` | non-empty matches in both files |

**Verification:**

- **Date and environment:** 2026-10-14, macOS, Python 3.13.
- **Result:** `PROGRESS.md` records the Phase 6 close as "**106 passed**; `demo_user` fixture, explicit `QuizAnswer` cascade, `is_demo_user` server default".
- **Known caveat:** this ticket's own verification-record block was never filled in — the line above is the phase-level entry, not a per-command log.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
