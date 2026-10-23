# SRA-509 — Phase 5 integration verification

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** verified

## Summary

Prove that scheduled wake-up and voice quiz work end to end: the full suite, the
migration rehearsal, the scheduler lifecycle, the wake-up loop's poll → speak →
stop behavior, the voice-quiz driver with substituted audio, the voice-turn flow
through the real router, and the per-user rate-limit inheritance of the new
`/me/preferences` and `/digest/ready` routes. Re-confirm every Phase 1/2/3/4
criterion sitting next to Phase 5 code paths.

## Unlocks

- Phase 6 starts without re-checking the Phase 5 surface; every SRA-501–508 criterion has phase-level evidence and its limitations are recorded rather than skipped.

## Files

| File | Change | Why |
|---|---|---|
| [`tests/integration/test_phase5_end_to_end.py`](../tests/integration/test_phase5_end_to_end.py) | new | Two users, full Phase 5 flow, real `create_app()`; marked `phase5`. |
| [`tests/integration/test_phase5_migration.py`](../tests/integration/test_phase5_migration.py) | new | Migration rehearsal. |
| [`tests/integration/test_phase5_scheduler.py`](../tests/integration/test_phase5_scheduler.py) | new | Scheduler lifecycle in the FastAPI lifespan. |
| [`tests/integration/test_phase5_rate_limit_inheritance.py`](../tests/integration/test_phase5_rate_limit_inheritance.py) | new | Per-user token-bucket burst over the four new routes. |
| [`tests/e2e/test_phase5_wakeup_e2e.py`](../tests/e2e/test_phase5_wakeup_e2e.py) | new | Wake-up loop, stubbed `api_client` and TTS. |
| [`tests/e2e/test_phase5_voice_quiz_e2e.py`](../tests/e2e/test_phase5_voice_quiz_e2e.py) | new | `run_voice_quiz`, all four seams stubbed. |
| [`tests/integration/test_route_policy_audit.py`](../tests/integration/test_route_policy_audit.py) | run | Audit pattern; the policy dict was extended by SRA-505. |
| [`README.md`](../README.md), [`PROGRESS.md`](../PROGRESS.md), [`.env.example`](../.env.example), [`tests/README.md`](../tests/README.md), [`tickets/README.md`](../tickets/README.md) | edit | Record commands, revision IDs, settings, skips; mark SRA-501–508 done. |

## Acceptance criteria

- [x] The five Phase 5 integration tests described above pass with only the LLM and audio substituted
- [x] Phase 1/2/3/4 regressions remain green; the Phase 1/2/3/4 manual evidence remains recorded and still matches
- [x] Ruff and Pyright pass with no new warnings
- [x] The voice-turn `curl` loop and the wake-up loop manual run produce actual returned IDs, feedback text, and `score` evidence recorded in this ticket's verification log
- [x] The mounted-route policy audit reports no unprotected Phase 5 endpoints
- [x] The full suite's pass count is recorded with date, environment, focused commands, and any opt-in skips

## Tests

```bash
source .venv/bin/activate
pytest -m phase5 \
    tests/unit/test_api_client.py \
    tests/unit/test_digest_routes.py \
    tests/unit/test_digest_scheduler.py \
    tests/unit/test_preferences_routes.py \
    tests/unit/test_quiz_conversation_graph.py \
    tests/unit/test_quiz_routes.py \
    tests/unit/test_tts.py \
    tests/unit/test_user_preferences.py \
    tests/unit/test_voice_quiz_client.py \
    tests/unit/test_wakeup.py \
    tests/e2e/test_phase5_api_client_e2e.py \
    tests/e2e/test_phase5_voice_quiz_e2e.py \
    tests/e2e/test_phase5_wakeup_e2e.py \
    tests/integration/test_phase5_migration.py \
    tests/integration/test_phase5_migration_replay.py \
    tests/integration/test_phase5_scheduler.py \
    tests/integration/test_phase5_end_to_end.py \
    tests/integration/test_phase5_rate_limit_inheritance.py -q
pytest -m 'not phase5' -q
ruff check .
pyright
```

Use the explicit list above, not a bare `pytest -m phase5 -q`; see the note below.

## Manual verification

| Check | Command | Expected |
|---|---|---|
| Migration rehearsal | `test_phase5_migration*.py` | `alembic upgrade head` from `a1b2c3d4e5f6` adds five `users` columns plus `quiz_attempts.cursor`; Phase 4 rows keep default `("09:00", "UTC", 10, 4, None)`; `ck_users_preferred_review_time_shape` rejects `"9:00"`, `ck_users_quiz_counts_positive` rejects `quiz_question_count=99`; `alembic downgrade -1` drops only the new columns, upgrade restores them. |
| Preferences round-trip | `test_preferences_routes.py`, `test_user_preferences.py` | Valid `PUT /api/v1/me/preferences` → `200` with persisted values; invalid `preferred_review_time` (`25:00`), count (`99`), or missing field → `400`; `GET` echoes them; the next `pregenerate_quizzes_for_day` call honors the new counts. |
| Scheduler lifecycle | `test_phase5_scheduler.py` | `SCHEDULER_ENABLED=true` starts the `BackgroundScheduler` with `sra.pregenerate` and `sra.wakeup_tick`, shuts down on exit; `false` runs the no-op path with `app.state.scheduler is None`. `GET /api/v1/digest/ready` is `{"ready": False}` on an empty DB, `True` once a due review and a pregenerated quiz set exist. |
| Voice quiz end-to-end loop | `test_phase5_end_to_end.py`, `test_phase5_voice_quiz_e2e.py` | Start returns `{attempt_id, question_text: "q0", options: ["a","b","c","d"]}`; each `POST /voice-turn` returns `{is_correct, feedback_text, next_question_text, finished, score}`; final `QuizAttempt.score == 3` matches the correct-answer count; one `QuizAnswer` per turn; `correct_option_index` never echoed; a second turn on a completed attempt → `409`. |
| Cross-user isolation | `test_phase5_end_to_end.py -k cross_user` | User B gets `403` for user A's `quiz_set_id` and `attempt_id`; user B's `PUT /me/preferences` returns `200` for B's own row and never touches A's. |
| Rate-limit inheritance | `test_phase5_rate_limit_inheritance.py` | A burst through `GET /me/preferences`, `GET /digest/ready`, `GET /search`, and `GET /digest` from one user → `429` with `Retry-After: "2"` at the existing threshold — the same per-user bucket as `entries`, `chat`, and `quiz`; separate users have independent buckets; advancing the controlled monotonic clock by `1.5s` brings `Retry-After` to `"1"`; `require_user` and `limit_authenticated` are in every Phase 5 call chain. |
| Wake-up loop | `test_phase5_wakeup_e2e.py` | `speak` called exactly once with the digest sentence when `ready=True` once then `False`; `stop_event.set()` breaks the loop well under one `poll_interval_s`; the loop never speaks when the API never reports ready. |
| Voice quiz driver | `test_voice_quiz_client.py` | `record_answer` wraps `capture.recording.record_audio`; `run_voice_quiz` exercises speak → record → transcribe → send → speak-feedback/next (or final summary) and returns `{score, total_questions}`. |
| Mounted-route policy audit | `test_route_policy_audit.py` | `/api/v1/me/preferences` (GET, PUT), `/api/v1/digest/ready` (GET), and `/api/v1/quiz/attempts/{attempt_id}/voice-turn` (POST) are all under `user`/`limit_authenticated` — no unprotected Phase 5 endpoint. |
| Phase 1/2/3/4 regressions | `pytest -m 'not phase5' -q` | 479 passed, 14 skipped, 13 failed — all pre-existing SRA-406 limitations, none from Phase 5 (see Verification). |
| Lint and types | `ruff check .`, `pyright` | `All checks passed` / 0 errors, 0 warnings, 0 informations. |
| Manual evidence | SRA-505 voice-turn `curl` loop; SRA-507 wake-up loop | Pending here — no `say` device, no microphone, no Google/Keychain. Covered by the e2e tests through the same `sra.capture` modules, TTS backend and API client stubbed at the desktop seams. |

**Verification:**

- **Date/environment:** 2026-10-14, local `.venv` (Python 3.13), SQLite in-memory session, single-process FastAPI `TestClient`, APScheduler `BackgroundScheduler` in the lifespan, controlled monotonic clock; no real OpenAI / Whisper / `say` / Google / Keychain / microphone / hotkey calls — LLM and TTS substituted at the established seams. The scripted `_interpret_answer` stands in for `process_voice_turn`'s model call.
- Phase 5 focused + e2e + integration: **196 passed** (the explicit file list above).
- Phase 1/2/3/4 regression: **479 passed, 14 skipped, 13 failed** — all pre-existing SRA-406 limitations: 3 × `test_phase4_end_to_end_*` seeding outside `repository.create_entry`; 1 × `test_phase4_web_quiz_e2e_returns_401_without_authentication`; 1 × `test_phase3_migration::test_upgrade_preserves_phase2_data_and_supports_explicit_legacy_link`; 7 × `test_quiz_dashboard` blocked by the hard-coded `PATH = "/usr/bin:/usr/local/bin"` and async-DOM timing. None are Phase 5 regressions.
- `ruff check .` → **All checks passed.** `pyright` → **0 errors, 0 warnings, 0 informations.** The 13 failures and 14 skips are unchanged from the SRA-406 baseline; the opt-in real-Whisper test remains skipped (no model download here).

> **Note:** `pytest -m phase5 -q` over every Phase 5 test in one process collects **2 cross-file caplog failures** that do not occur with the explicit file list above: `tests/unit/test_digest_scheduler.py::test_users_due_for_wakeup_skips_and_logs_bad_timezone_user` and `tests/unit/test_wakeup.py::test_run_wakeup_loop_survives_a_per_iteration_exception` both rely on `caplog.records` holding a record produced by *their own* fixture, but pytest's per-file caplog propagation leaks records across files. Both pass in the explicit list. Documented, not fixed — each test's assertions are correct in isolation.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
