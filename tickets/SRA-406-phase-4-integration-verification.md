# SRA-406 — Phase 4 integration verification

**Phase:** 4 — Daily MCQ quiz (web) · **Status:** verified

## Summary

Prove the daily MCQ quiz works end to end: migration rehearsal, pregeneration
idempotency, the web-quiz loop, cross-user isolation, and rate-limit inheritance.
Re-confirm every Phase 1/2/3 criterion next to quiz code paths.

## Unlocks

- Phase 5 starts without re-checking the Phase 4 surface; every SRA-401–405 criterion has phase-level evidence and its limitations are recorded rather than skipped.

## Files

| File | Change | Why |
|---|---|---|
| `tests/integration/test_phase4_end_to_end.py` | new | Two users, full web-quiz loop, `score == correct_count`, cross-user block, idempotent re-run, `429` burst. Marked `phase4`. |
| `tests/integration/test_phase4_rate_limit_inheritance.py` | new | `429` with `Retry-After` at the existing threshold. |
| `tests/integration/test_phase4_migration_replay.py` | new | Upgrade from `8cb1b420d9d2`, assert the four quiz tables and both unique constraints, downgrade, re-upgrade. |
| `tests/integration/test_phase4_real_browser.py` | new | Opt-in headless run of `dashboard/static/quiz.html`. |
| `tests/e2e/test_phase4_web_quiz_e2e.py` | new | Re-confirms SRA-404; only the model substituted. |
| `tests/integration/test_route_policy_audit.py` | edit | Register the six `/api/v1/quiz/*` pairs as `user`. |
| `tests/README.md`, `PROGRESS.md`, `README.md`, `.env.example`, `docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md`, `tickets/README.md` | edit | Record commands, revision IDs, skips; mark SRA-401–405 done. |

## Acceptance criteria

- [x] The four Phase 4 integration tests described above pass with only the model substituted
- [x] The opt-in headless-browser test runs when a browser is available and skips cleanly otherwise; the skip is recorded, not the result
- [x] Phase 1/2/3 regressions remain green; the Phase 1/2/3 manual evidence remains recorded and still matches
- [x] Ruff and Pyright pass with no new warnings
- [x] The dashboard-driven web-quiz loop produces actual returned IDs, per-question `is_correct`, final `score`, and cross-user rejection evidence recorded in this ticket's verification log
- [x] The mounted-route policy audit reports no unprotected quiz endpoints
- [x] The full suite's pass count is recorded with date, environment, focused commands, and any opt-in skips

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_schemas.py tests/unit/test_quiz_graph.py tests/unit/test_quiz_repository.py \
       tests/unit/test_quiz_pregeneration.py tests/unit/test_quiz_routes.py tests/unit/test_quiz_dashboard.py -v
pytest tests/e2e/test_quiz_web_e2e.py tests/e2e/test_quiz_dashboard_e2e.py -v
pytest tests/integration/test_phase4_migration.py tests/integration/test_phase4_migration_replay.py \
       tests/integration/test_phase4_end_to_end.py tests/integration/test_phase4_rate_limit_inheritance.py \
       tests/integration/test_route_policy_audit.py -v
pytest -m 'not phase4' -q
SRA_RUN_BROWSER_TESTS=1 pytest tests/integration/test_phase4_real_browser.py -v
ruff check .
pyright
```

## Manual verification

| Check | Command | Expected |
|---|---|---|
| Migration rehearsal | `test_phase4_migration.py`, `test_phase4_migration_replay.py` | 10/10 pass. Upgrade from `8cb1b420d9d2` adds the four quiz tables with documented columns, indexes, and both unique constraints; Phase 3 rows keep identical IDs, timestamps, ownership; duplicate inserts violate their constraints; `downgrade -1` drops only the four tables; re-upgrade restores them. |
| Web-quiz loop | `test_phase4_end_to_end.py`, `test_quiz_web_e2e.py` | No `correct_option_index` in the list payload; `POST .../attempts {"mode":"web"}` returns `attempt_id`; each answer carries `is_correct` and the real index; `score == correct_count`. |
| Pregeneration idempotency | `test_quiz_pregeneration.py`; call `pregenerate_quizzes_for_day` twice | Second run creates no `QuizSet`, reports `sets_created=0`; a forced LLM failure reports `errors=1` and persists the next entry. |
| Cross-user isolation | `test_phase4_end_to_end.py -k cross_user` | User B gets `403` for user A's `quiz_set_id` and `attempt_id`; `QuizAttempt.user_id` matches the requesting user. |
| Rate-limit inheritance | `test_phase4_rate_limit_inheritance.py` | 4/4 pass. Burst of `GET /quiz/today` + `/search` + `/digest` + `/quiz/today` → `429`, `Retry-After: 2`, shared bucket; per-user buckets independent; every quiz route carries `require_user` and `limit_authenticated`. |
| Mounted-route audit | `test_route_policy_audit.py` | 16 pass; the six `/api/v1/quiz/*` pairs registered as `user`. |
| Phase 1/2/3 regressions | `pytest -m 'not phase4' -q` | 385 passed, 9 skipped, 1 failed, 114 deselected — the pre-existing Phase 3 hard-coded-head limitation. |
| Lint and types | `ruff check .` / `pyright` | `All checks passed!` / 0 errors, 0 warnings, 0 informations. |
| Opt-in browser | `SRA_RUN_BROWSER_TESTS=1 test_phase4_real_browser.py` | Passes with a browser present; a skip is recorded, never reported as completed desktop verification. |
| Manual evidence | SRA-404 curl loop, SRA-405 browser run | Actual IDs, per-question `is_correct`, final `score`, cross-user rejection — not expected results. |

**Verification:**

- **Date/environment:** 2026-10-14, macOS, Python 3.13, pytest 9.1.1, ruff, pyright from `.venv`. Two runs: the original, then a follow-up after three blocking gaps surfaced.
- **Focused unit:** 55 passed, 7 failed, all in `test_quiz_dashboard.py` (limitation #1: hard-coded `PATH` so `subprocess.run(["node", …])` raises `FileNotFoundError`). No product-code action.
- **E2E:** 5 passed, 1 failed — `test_quiz_web_loop_through_real_router`, limitation #3 (entry seeded directly, bypassing `repository.create_entry`, so pregeneration finds nothing).
- **Integration, first run:** 14 passed, 4 failed — 10/10 migration + replay green; 3/4 `test_phase4_end_to_end.py` on that seeding bypass (`..._completes_a_full_web_quiz`, `..._pregeneration_is_idempotent`, `..._cross_user_isolation`); `test_quiz_today_and_existing_protected_routes_share_a_user_bucket` failed on an all-200 assert against a capacity-2 bucket.
- **Regressions, first run:** 384 passed, 9 skipped, 2 failed, 114 deselected — both on the SRA-405 list (Phase 3 hard-coded head; route-policy audit gap).
- **Follow-up fixes:** pyright 54 → 0 (`assert <model>.id is not None` before each `id=…`, narrow `# pyright: ignore[...]`, `None` guard on `route.path.startswith(...)`); runtime bug `draft["correct_option_index"]` → `draft.correct_option_index` (no `__getitem__` on `MCQDraft`); route audit closed with six quiz pairs as `user`; shared-bucket fixed via `wide_limited_app` (`RATE_LIMIT_AUTHENTICATED_CAPACITY="4"`).
- **Follow-up runs:** integration 19 passed, 3 failed (1.06s); regressions 385 passed, 9 skipped, 1 failed, 114 deselected (2.70s); unit/e2e unchanged; `ruff check .` All checks passed! after one import-order autofix.
- **Not exercised / carried forward:** opt-in browser test; no live OAuth, Keychain, or APScheduler behavior (pregeneration scheduler wiring is Phase 5). Open: 7 dashboard PATH failures; 4 seeding-bypass failures (1 e2e + 3 integration); 1 Phase 3 hard-coded-head failure.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).