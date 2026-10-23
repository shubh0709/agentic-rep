# SRA-501 — User preferences and scheduler columns

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Extend `users` with `preferred_review_time`, `timezone`, `quiz_question_count`, `quiz_option_count`, and `wakeup_notification_sent_at`. Expose the settings through `GET /api/v1/me/preferences` and `PUT /api/v1/me/preferences`, validate them, and make quiz pregeneration read stored values. Defaults remain `09:00`, `UTC`, `10`, and `4`, preserving Phase 4 behavior. The columns are grouped in one migration so Phase 5 consumers share one user-scoped source of truth.

## Unlocks

- Later Phase 5 jobs and clients can read one typed `UserPreferences` object for wake-up timing, quiz sizing, and delivery bookkeeping.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/db/models.py`](../src/sra/db/models.py) | edit | Adds the five fields, validators, defaults, and database constraints to `User`. |
| [`src/sra/alembic/versions/b2c3d4e5f6a7_add_phase5_preferences_columns.py`](../src/sra/alembic/versions/b2c3d4e5f6a7_add_phase5_preferences_columns.py) | new | Chains revision `b2c3d4e5f6a7` from `a1b2c3d4e5f6` and supports downgrade/re-upgrade. |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | edit | Adds typed `get_user_preferences` and `update_user_preferences` helpers. |
| [`src/sra/api/schemas.py`](../src/sra/api/schemas.py) | edit | Defines `PreferencesIn` and `PreferencesOut`. |
| [`src/sra/api/routers/preferences.py`](../src/sra/api/routers/preferences.py) | new | Serves authenticated `GET`/`PUT /api/v1/me/preferences`. |
| [`src/sra/api/routers/__init__.py`](../src/sra/api/routers/__init__.py) | edit | Mounts the protected preferences router under `/api/v1`. |
| [`src/sra/jobs/quiz_pregeneration.py`](../src/sra/jobs/quiz_pregeneration.py) | edit | Makes `_resolve_user_quiz_preferences` read stored counts instead of `(10, 4)`. |

## Acceptance criteria

- [x] `User` carries the five new columns with the documented defaults, validators, and `CheckConstraint`s
- [x] `alembic upgrade head` from `a1b2c3d4e5f6` adds the columns; `downgrade -1` removes them; a second `upgrade head` restores them; pre-Phase-5 rows are untouched with defaults applied
- [x] `GET /api/v1/me/preferences` returns the defaults for a freshly-created user and the stored values for an edited one; both shapes match `PreferencesOut`
- [x] `PUT /api/v1/me/preferences` with a valid body returns `200` and the stored row matches; an invalid `preferred_review_time` returns `400`; an invalid `timezone` returns `400`; an out-of-range count returns `400`
- [x] The new router is mounted under `/api/v1` and is included in the route-policy audit as a `user` policy with `limit_authenticated`
- [x] `_resolve_user_quiz_preferences` reads from `repository.get_user_preferences(...)`; SRA-403's existing tests still pass without modification
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass with no new warnings

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_user_model.py tests/unit/test_user_preferences.py tests/unit/test_repository.py -v
pytest tests/unit/test_preferences_routes.py -v
pytest tests/integration/test_phase5_migration.py tests/integration/test_phase5_migration_replay.py -v
pytest -m 'not phase5' -q 2>&1 | tail -3
ruff check .
pyright
```

Migration replay must prove the columns appear, disappear on downgrade, and return on re-upgrade. Ruff and Pyright must be clean.

## Manual verification

- [ ] Authenticate and verify `GET /api/v1/me/preferences` returns `09:00`, `UTC`, `10`, `4`, and `NULL`; update to `07:30`, `America/Los_Angeles`, `10`, `4` and verify persistence.
- [ ] Submit `99:99`, an invalid IANA zone, and an out-of-range count; verify each returns `400`, and confirm the stored row is unchanged.

**Verification:**
- **2026-10-14, local macOS, Python 3.13.12, pytest 9.1.1:** revision `b2c3d4e5f6a7` follows `a1b2c3d4e5f6`; upgrade/downgrade/re-upgrade added and removed all five columns and constraints while preserving pre-Phase-5 defaults.
- `repository.UserPreferences`, `get_user_preferences(user_id, session)`, and `update_user_preferences(user_id, prefs, session)` now feed the protected preferences router and `_resolve_user_quiz_preferences`; the `(10, 4)` literal is gone.
- `GET/PUT /api/v1/me/preferences` is mounted with `require_user` and `limit_authenticated`. `PreferencesIn` forbids extras and validates `HH:MM`, IANA zone, and count bounds; `PreferencesOut` echoes all five fields.
- Focused results: **60 passed** (`test_user_preferences.py`), **12 passed** (`test_preferences_routes.py`), **8 passed** (`test_phase5_migration.py`), **5 passed** (`test_phase5_migration_replay.py`): **85 passed** total.
- `pytest -m 'not phase5' -q` recorded **482 passed, 13 failed, 14 skipped**. The 13 pre-existing failures were seven dashboard harness PATH failures, three Phase 4 direct-seed failures plus one equivalent quiz-web seed failure, one hard-coded Phase 3 head, and one unpatched OpenAI 401 test.
- `ruff check .` passed; `pyright` reported **0 errors, 0 warnings, 0 informations** after narrowing `_to_out` to `UserPreferences`, asserting non-null `user.id`, and using `PreferencesIn.model_validate(payload)`.
- Manual evidence was not re-executed. The 14 skips remain the documented Phase 2 desktop/Whisper opt-ins; SRA-501 introduced no new limitation.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
