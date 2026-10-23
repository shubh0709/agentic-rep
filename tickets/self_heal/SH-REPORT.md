# Application Self-Heal — Completion Report

Run: **2026-10-16**, branch `master`. Resulting HEAD after this pass: `6938f95 feat(jobs): interview
counters in dev upcoming pregen + scheduler test fix (SRA-802 follow-up)`, which incorporated the
SH-002, SH-003, and SH-004 fixes alongside the broader SRA-802 follow-up work.

Process guide: [`guides/self_heal.md`](../../guides/self_heal.md). Per-ticket detail:
[SH-001](SH-001-coverage-matrix.md) · [SH-002](SH-002-route-audit-refresh.md) ·
[SH-003](SH-003-scheduler-test-time-bomb.md) · [SH-004](SH-004-dev-py-pregensummary-fields.md) ·
[SH-005](SH-005-memory-store-save-pair.md).

> **Note (added 2026-10-23):** the SRA-509 `caplog`-isolation limitation recorded in "Open limitations"
> below was live **as of the 2026-10-16 pass** and has since been fixed — `pytest -m phase5 -q` is now
> green (212 passed, 1049 deselected). Current status: [`PROGRESS.md`](../../PROGRESS.md).

## Outcome

| Status | Test count (was → now) |
|---|---|
| Default `pytest -q` passes | 962 → 970 (+8) |
| Default `pytest -q` fails | 25 → 17 (−8 confirmed bugs closed) |
| Skipped (opt-in / platform) | 26 → 26 (unchanged) |
| `ruff check .` errors | 10 → 8 (−2; remaining 8 are in user WIP files) |
| `pyright` errors | 16 → 4 (−12; remaining 4 are in `tests/playwright/`, which the user is actively iterating) |

Net reduction in broken tests: **8**. The remaining 17 failures are non-actionable: documented limitations from SRA-405 / SRA-404 / SRA-509 and Phase 7 / Phase 8 work-in-progress the user is iterating on (`tickets/SRA-801..809`).

## Tickets closed in this pass

| ID | Title | Commit | Verified by |
|---|---|---|---|
| **SH-002** | `/api/v1/auth/refresh` and dashboard SSR routes missing from route-policy audit | broad audit refresh landed in SRA-805 commit `d6f080a`; this pass verified the audit covers every mounted route in `sra.api.app.create_app()` | `pytest tests/integration/test_route_policy_audit.py -v` PASS |
| **SH-003** | `tests/unit/test_digest_scheduler.py::_add_pending_review` hard-coded to `datetime.now(UTC).date()` while tests froze `as_of_utc` to 2026-10-14 — a date bomb that failed silently every day after 2026-10-14 | `6938f95` (the helper now takes an explicit `scheduled_date` parameter and every failing call site passes `date(2026, 10, 14)`) | All 10 scheduler unit tests pass; `pytest -q` standalone is green for 7 of 8 originally-failing `digest_scheduler` tests; the 8th (caplog-based) flips from "fails for time-bomb reason" to "fails for SRA-509 caplog-isolation reason" |
| **SH-004** | `POST /api/v1/me/dev/pregenerate-now` declared as `PregenerationSummary` but the `include_upcoming=true` path dropped the three `interview_plans_*` counters created by the natural pregen pass | `6938f95` (the upcoming summary literal and the merge in `pregenerate_now` now carry `interview_plans_created/skipped/errored`) | `pyright src/sra/api/routers/dev.py` clean; `pytest tests/unit/test_dev_pregenerate_route.py -v` PASS (6/6) |
| **SH-005** | `tests/unit/test_auth_client.py::MemoryCredentialStore` did not implement `save_pair` (added to the `CredentialStore` protocol in SRA-306) — left pyright complaining in 8 call-sites | `2d33a4e test(auth): pin CredentialStore.save_pair test double (SRA-306 follow-up)` (the fake now exposes `save_pair(*, server_origin, account, credential, refresh_credential)`) | `pytest tests/unit/test_auth_client.py -v` PASS (26/26); `pyright tests/unit/test_auth_client.py` clean (8 → 0 errors) |

## Coverage demonstrated

- **25 user/admin mounted routes** (live in `sra.api.app.create_app()`) are now explicitly enumerated by `(method, path) → policy` in the audit dict, covering the Phase 1–8 surface plus the SRA-701 SSR dashboard mount.
- The digest scheduler is exercised in isolation: `users_due_for_wakeup` returns the right users for "just after local review time", "already notified today", "next local day", "across timezones" (LA/PDT), and "bad timezone user". `run_wakeup_tick` sets `wakeup_notification_sent_at`, is idempotent within a local day, is per-user exception-tolerant, and crosses midnight to the next day.
- `POST /api/v1/me/dev/pregenerate-now` returns the full `PregenerationSummary` shape — five MCQ counters plus three interview counters — and the contract holds at the type level.
- The `MemoryCredentialStore` test fake is a structural `CredentialStore`.

## Open limitations (NOT closed in this pass)

These are the 17 remaining default-suite failures; none are actionable in this pass, all are documented in existing tickets or PROGRESS.md.

| # | Failure | Why it stays failing | Reference |
|---|---|---|---|
| 1 | `tests/unit/test_wakeup.py::test_run_wakeup_loop_survives_a_per_iteration_exception` | pytest's `caplog` fixture leaks across files when invoked via `pytest -q`; passes via the explicit Phase 5 file list — as of the 2026-10-16 pass; fixed since (see the note above) | SRA-509 |
| 1 | `tests/unit/test_digest_scheduler.py::test_users_due_for_wakeup_skips_and_logs_bad_timezone_user` | Same SRA-509 caplog-isolation quirk; passes standalone — as of the 2026-10-16 pass; fixed since (see the note above) | SRA-509 |
| 7 | `tests/unit/test_quiz_dashboard.py::test_*` | Phase 4 dashboard unit harness hard-codes `PATH = "/usr/bin:/usr/local/bin"` and an async-DOM timing assumption the SPA module cannot satisfy here | SRA-405 (documented) |
| 1 | `tests/unit/test_interview_planner.py::test_generate_interview_outline_raises_quiz_generation_error_on_llm_failure` | Phase 8 planner / repository integration (SRA-804) not yet implemented | SRA-804 planned |
| 1 | `tests/integration/test_init_db.py::test_reset_db_repairs_an_inconsistent_database` | Phase 8 migration `e5f6a7b8c9d0` exists on disk but the Phase 3 migration in the working-tree test only goes up to `8cb1b420d9d2`; the user is iterating | Phase 8 in-progress |
| 1 | `tests/integration/test_phase3_migration.py::test_upgrade_preserves_phase2_data_and_supports_explicit_legacy_link` | Same Phase 8 migration chain gap as above | Phase 8 in-progress |
| 1 | `tests/e2e/test_phase4_web_quiz_e2e.py::test_phase4_web_quiz_e2e_returns_401_without_authentication` | Phase 4 dashboard intermediate issue documented in SRA-406 | SRA-406 |
| 3 | `tests/integration/test_phase4_end_to_end.py::test_phase4_end_to_end_*` | Phase 4 e2e seed-bypasses documented in SRA-406 | SRA-406 |
| 1 | `tests/e2e/test_quiz_web_e2e.py::test_quiz_web_loop_through_real_router` | Test seeds an entry without `repository.create_entry`, so no reviews are scheduled and pregen finds nothing | SRA-404 |

## Lint / type noise that remains

`ruff check .` reports **8 errors** in `tests/playwright/conftest.py` (unused imports `generate_and_persist_questions`, `MCQDrafts`, `InterviewPlanCreate`, `InterviewTurnRole`) and `tests/unit/test_quiz_pregeneration.py` (unused local `fake` and `from sra.agents import interview_planner`). All eight belong to active SRA-701 (dashboard) and SRA-802 (interview planner) work and were intentionally left untouched per `guides/self_heal.md` ("preserve unrelated user changes"); the relevant module-level tests are skipped at the `playwright/` collection level and the affected unit-test sites are still WIP. `pyright` reports **4 errors** in `tests/playwright/{conftest.py, test_user_flows.py}` — same user-WIP directory, same treatment; they resolve as SRA-701's Playwright suite matures.

## How to reproduce the verification run

```bash
source .venv/bin/activate

# Focused re-runs of every ticket closed in this pass
pytest tests/integration/test_route_policy_audit.py -v
pytest tests/unit/test_digest_scheduler.py -v
pytest tests/unit/test_dev_pregenerate_route.py -v
pytest tests/unit/test_auth_client.py -v

# Optional Phase 5 file-list invocation suppressing the SRA-509 caplog isolation
# quirk (the two caplog tests pass here); then the final default regression:
# (the file list was needed as of the 2026-10-16 pass; the quirk is fixed now)
pytest tests/unit/test_digest_scheduler.py tests/unit/test_wakeup.py \
      tests/integration/test_phase5_end_to_end.py tests/integration/test_phase5_rate_limit_inheritance.py -v
pytest -q
```

Expected: `17 failed, 970 passed, 26 skipped` on the day the run finishes; the 17 documented limitations remain stable across future runs and do not regress.

## What this report does not claim

Per `guides/self_heal.md` §9:

- The 17 remaining failures are not "skipped silently". Each is surfaced and classified; no `xfail` markers were added, no assertions relaxed, no test deleted.
- The application is not "bug-free". Manual completion checks remain — physical microphone acceptance (SRA-204/206), global macOS hotkey delivery (SRA-205), Google OAuth in a real browser (SRA-302), native macOS Keychain (SRA-303), and two-account isolation/rate limit (SRA-305) — none exercisable in this environment.
- The Phase 8 interview stack is intentionally incomplete (SRA-802 through SRA-809 planned). Self-heal did not extend its scope.