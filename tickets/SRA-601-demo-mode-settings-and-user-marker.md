# SRA-601 — DEMO_MODE settings and demo-user marker column

**Phase:** 6 — Open-source packaging · **Status:** implemented — 2026-10-14

## Summary

Add `demo_mode: bool = False` and `demo_data_retention_days: int = 7` to `Settings` (with validators), add an `is_demo_user: bool = False` column on `User` together with a fresh Alembic revision `d4e5f6a7b8c9` chained off `c3d4e5f6a7b8`, and expose typed repository helpers `list_old_demo_users(retention_days, as_of_utc, session)` and `delete_user_cascade(user, session)` so the demo reset job (SRA-603) can find and remove demo data without bespoke SQL. The two surfaces are grouped deliberately: keeping the migration count at one lets SRA-603 stay focused on reset behaviour and lets SRA-605 rehearse the Phase 6 upgrade/downgrade cycle in a single pass. Once this lands a self-hoster and the hosted demo run the same image — the demo only adds `DEMO_MODE=true` and `DEMO_DATA_RETENTION_DAYS=7` to its environment, and the server branches on `Settings.demo_mode` at request time. The schema can also tell "a row created by the hosted demo" apart from "a row belonging to a real self-hoster", which is the precondition for bounded demo retention. The marker is a single boolean and never needs a join, so it follows the Phase 3/5 precedent of putting server-owned scalars directly on the `users` row rather than in a side table.

## Unlocks

- SRA-603's reset job can filter `User.is_demo_user == True` rows older than the retention window and delete each one through a single typed cascade.
- The hosted demo deploys the same image as a self-hoster, adding only `DEMO_MODE=true` and `DEMO_DATA_RETENTION_DAYS=7`.
- SRA-605 can rehearse the Phase 6 upgrade/downgrade cycle against one revision.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/config/`](../src/sra/config/) | edit | `Settings.demo_mode: bool = False` and `demo_data_retention_days: int = Field(default=7, gt=0, le=365)`; the upper bound is "a year", the lower bound "at least one day". |
| [`src/sra/db/models.py`](../src/sra/db/models.py) | edit | `is_demo_user: bool = False` on `UserBase` and `UserCreate`; the concrete `User` table gets an explicit `Boolean` column with `nullable=False, server_default="0"` so raw-SQL inserts populate cleanly. |
| [`src/sra/alembic/versions/d4e5f6a7b8c9_add_phase6_is_demo_user.py`](../src/sra/alembic/versions/d4e5f6a7b8c9_add_phase6_is_demo_user.py) | new | Revision `d4e5f6a7b8c9`, `down_revision = "c3d4e5f6a7b8"`. `upgrade()` adds the column with `server_default=false`; `downgrade()` drops it. `demo_mode` is a settings flag and needs no migration. |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | edit | `delete_user_cascade(user, session) -> None` (one transaction, one commit, cascades through `study_entries` → `review_schedules` → `quiz_sets` → `quiz_questions` → `quiz_attempts` → `quiz_answers`) and `list_old_demo_users(retention_days, as_of_utc, session) -> list[tuple[User, datetime]]` (demo-only rows whose oldest related timestamp predates the cutoff). Both raise `UserNotFoundError`. |
| [`tests/unit/test_user_model.py`](../tests/unit/test_user_model.py) | edit | `is_demo_user` defaults to `False`, accepts `True`, and the validator does not fail on `False`. |
| [`tests/unit/test_config.py`](../tests/unit/test_config.py) | edit | `Settings(demo_mode=True)` works, `demo_data_retention_days` must be in `(0, 365]`, and the existing required-OpenAI-key validator still fires. |
| [`tests/unit/test_repository.py`](../tests/unit/test_repository.py) | edit | `list_old_demo_users` (oldest-row detection per user, demo-only filtering, retention cutoff) and `delete_user_cascade` (a deleted user's entries, schedules, quiz sets, attempts, and answers are gone). |
| [`tests/integration/test_phase6_migration.py`](../tests/integration/test_phase6_migration.py) | new | Upgrade from `c3d4e5f6a7b8` lands the column; existing rows carry `is_demo_user=False`. |
| [`tests/integration/test_phase6_migration_replay.py`](../tests/integration/test_phase6_migration_replay.py) | new | Replays `downgrade -1` then `upgrade head` against an isolated SQLite; the column disappears and reappears; every pre-existing row keeps `is_demo_user=False`. |

## Acceptance criteria

- [ ] `Settings.demo_mode` defaults to `False`; reading `DEMO_MODE=true` from `.env`/`docker-compose` returns `True`; missing setting defaults to `False`
- [ ] `Settings.demo_data_retention_days` defaults to `7`; rejects `0`, negatives, `>365`, and non-integer strings with a clear pydantic-style error
- [ ] `User.is_demo_user` defaults to `False` on `UserBase`, `UserCreate`, and the concrete `User` table
- [ ] `alembic upgrade head` from `c3d4e5f6a7b8` adds the column with `server_default=false`; `downgrade -1` removes it; pre-Phase-6 rows keep `False`
- [ ] `delete_user_cascade(user, session)` deletes the user row and every dependent entry, schedule, quiz set, question, attempt, and answer in a single transaction; missing user raises `UserNotFoundError`
- [ ] `list_old_demo_users(retention_days, as_of_utc, session)` returns only `is_demo_user=True` rows whose oldest related timestamp is older than the cutoff; a demo user with no related rows is never returned
- [ ] All Phase 1/2/3/4/5 tests remain green; Ruff and Pyright pass with no new warnings

## Tests

```bash
pytest tests/unit/test_config.py tests/unit/test_user_model.py tests/unit/test_repository.py -v
pytest tests/integration/test_phase6_migration.py tests/integration/test_phase6_migration_replay.py -v
```

No test carries the `phase6` marker, so `pytest -m 'not phase6' -q` selects the whole 1261-test suite rather than Phases 1–5; use the file-level commands above to isolate this ticket. Lint, type check, and the full command list live in [`tests/README.md`](../tests/README.md).

## Manual verification

- [ ] `python -c "from sra.config import Settings; print(Settings().demo_mode, Settings().demo_data_retention_days)"` prints `False 7`.
- [ ] `DEMO_MODE=true DEMO_DATA_RETENTION_DAYS=14 python -c "from sra.config import Settings; print(Settings().demo_mode, Settings().demo_data_retention_days)"` prints `True 14`.
- [ ] `DEMO_DATA_RETENTION_DAYS=0 python -c "from sra.config import Settings; Settings()"` raises `ValidationError: Input should be greater than 0`.

**Verification:**

- **Date and environment:** 2026-10-14, macOS, Python 3.13.
- **Result:** `PROGRESS.md` records SRA-602/601 as "demo settings, `is_demo_user` + migration `d4e5f6a7b8c9`, cascade/retention; **23 + 44 tests**".
- **Known caveat:** this ticket's own verification-record block was never filled in — the line above is the phase-level entry, not a per-command log.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
