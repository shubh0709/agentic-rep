# SRA-801 — Interview schema and safe migration

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds the durable storage the interview feature needs: the `interview_plans` and `interview_turns` tables, a `QuizAttempt.cursor_on_plan` column for outline position, and the two `users` preference columns (`interview_question_count`, `interview_target_minutes`) that size the planner and the per-attempt turn cap. An Alembic revision performs the change without disturbing any Phase 1–7 row. This ticket is schema only: no interview question is generated, no route exposes the mode, and no user can start an interview yet — the agent, repository, router, and UI work land in later tickets. Once it lands, the database can hold one outline per `QuizSet`, a chronological log of `ai` / `user` / `system_summary` turns per attempt, and a per-user interview size and duration budget.

## Unlocks

- SRA-802 can persist the first `InterviewPlan`; SRA-803 writes `InterviewTurn` rows on every HTTP turn; SRA-805's routes resolve interviews only for the authenticated user.
- A future interview started today keeps the outline length it was created with, regardless of later preference changes.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/db/models.py` | edit | Add `InterviewPlan` / `InterviewTurn` (`<Name>Base`/`<Name>Create`/`<Name>`, `extra="forbid"` on `Create`, SQLModel `JSON` for `outline` as with `StudyEntry.tags`, `_utcnow()` defaults), `QuizAttempt.cursor_on_plan`, and the two `UserBase` preference columns with validators plus a `CheckConstraint` mirroring SRA-501's `ck_users_quiz_counts_positive`. |
| `src/sra/alembic/versions/<rev>_add_phase8_interview_tables.py` | new | One revision chaining off `d4e5f6a7b8c9`, hand-written because autogenerate on SQLite elides `CheckConstraint` expressions and JSON nuances. `upgrade()` creates both tables (unique `quiz_set_id`, indexed `attempt_id`, `ck_interview_turns_role`), adds `cursor_on_plan INTEGER NOT NULL DEFAULT 0` via `batch_alter_table`, and adds the two `users` columns. `downgrade()` reverses in FK-safe order, touching nothing pre-existing. |
| `src/sra/api/schemas.py` | edit | Extend `PreferencesIn` / `PreferencesOut` with the two fields, same `field_validator` style as `_check_question_count` / `_check_option_count`. |
| `src/sra/api/routers/preferences.py` | edit | Pass the two fields through the existing `PUT /api/v1/me/preferences` — no new endpoint. |
| `tests/unit/test_quiz_models.py` | edit | Defaults, FK targets, JSON round-trip, duplicate `quiz_set_id` rejected, `role` check constraint, `extra="forbid"` on each `Create`, out-of-range validators. |
| `tests/integration/test_phase8_migration.py` | new | Rehearse against a temporary file DB at the previous revision, seeded with a Phase 5 user, entry, `QuizSet`, and in-progress `QuizAttempt`. Cleans up in `finally`; skips where SQLite cannot create a file DB. |
| `tests/conftest.py` | read only | Per-test in-memory SQLite via `create_all`, so new tables are observable immediately — hence the separate file-DB rehearsal. |
| `PROGRESS.md`, `README.md` | edit | Record the new revision ID only after the rehearsal passes. |

## Acceptance criteria

- [ ] `interview_plans` and `interview_turns` exist with the columns, indexes, and unique constraints described above
- [ ] A fresh `alembic upgrade head` succeeds and creates the two new tables with their foreign keys pointing at the existing tables
- [ ] A Phase 5/6 database with one user (carrying the four Phase 5 preference columns), one entry, one `QuizSet`, and one `QuizAttempt` upgrades without losing IDs, foreign keys, or schedules; the same pre-existing rows survive a downgrade and a subsequent upgrade
- [ ] `users.interview_question_count` and `users.interview_target_minutes` columns appear with documented defaults; the `CheckConstraint` rejects out-of-range values
- [ ] `quiz_attempts.cursor_on_plan` appears with `server_default=0`; existing Phase 4/5 attempts read the new column as `0`
- [ ] Inserting a second `InterviewPlan` with the same `quiz_set_id` violates the unique constraint
- [ ] `InterviewTurn.role` rejects values other than `ai`, `user`, `system_summary` via the check constraint
- [ ] `interview_turns.created_at` default is timezone-aware, matching the project's `_utcnow()` convention
- [ ] `interview_plans.target_questions` snapshots the user's preference at generation time; `outline` JSON round-trips a list of `{subtopic, difficulty, focus_prompt}` dicts
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_models.py tests/integration/test_phase8_migration.py tests/integration/test_phase8_migration_replay.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

The `phase8` marker groups the suite during early Phase 8 work. The two `--ignore` flags skip pre-existing unrelated failures; `test_init_db.py` is excluded while the Phase 8 migration chain is still WIP.

## Manual verification

- [ ] Back up a disposable copy of a Phase 5/6 database, point `DATABASE_URL` at it, and run `alembic upgrade head`. Record row IDs and counts in `users`, `study_entries`, `review_schedules`, `quiz_sets`, `quiz_questions`, `quiz_attempts`, `quiz_answers` before and after. Never use a production or the only copy of study data for the first run.
- [ ] Confirm the two new tables and three new columns appear, that a hand-inserted duplicate `quiz_set_id` row fails on `uq_interview_plans_quiz_set_id`, and that an `InterviewTurn` with `role='bogus'` fails on `ck_interview_turns_role`.
- [ ] Run `alembic downgrade -1` (new objects disappear, existing rows still readable), then `alembic upgrade head` again and confirm everything returns.

**Verification (2026-10-15):**

- Revision `d4e5f6a7b8c9` → `e5f6a7b8c9d0`, local macOS dev (`darwin`, Python 3.13.12, SQLite).
- `pytest tests/unit/test_quiz_models.py tests/integration/test_phase8_migration.py tests/integration/test_phase8_migration_replay.py -v` → **58 passed** (46 unit + 7 migration + 5 migration replay).
- Regression `pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py` → **785 passed, 24 failed, 14 skipped, 153 deselected** — the 24 failures are the pre-existing Phase 1–7 limitations unchanged from the SRA-605 baseline; no new regressions.
- `ruff check .` clean. `pyright` errors drop from 20 to 8 (remaining are pre-existing untracked Phase 8 TDD scaffold and auth-client test issues, outside SRA-801 scope).
- Downgrade removed, in order: `ck_users_interview_preferences_in_range`, `users.interview_target_minutes`, `users.interview_question_count`, `quiz_attempts.cursor_on_plan`, `ix_interview_turns_attempt_id`, `interview_turns`, `interview_plans`; the second upgrade recreated them identically.
- Seeded rows survived with identical IDs and timestamps: `users.id=1001` (`phase6-replay@example.com`, `google_sub=phase6-replay-sub`, `is_demo_user=0`), `study_entries.id=1101` (topic `Mitosis`, `created_at=2026-09-01 13:00:00`), `review_schedules.id=1201` (`1d`, `scheduled_date=2026-09-02`), `quiz_sets.id=1301` (`generated_on=2026-10-14`, `question_count=2`, `option_count=4`), `quiz_attempts.id=1401` (`mode=web`, `total_questions=2`, `started_at=2026-10-14 09:00:00`, `cursor=0`). After re-upgrade the user read `interview_question_count=6`, `interview_target_minutes=5`, the attempt `cursor_on_plan=0`.
- > **Note:** the status line still reads `Planned`. A hand-driven rehearsal against a copy of a real Phase 5/6 database must still be recorded before this ticket closes.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).