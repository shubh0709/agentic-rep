# SRA-301 — OAuth identity schema and safe legacy migration

**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting · **Status:** verified

## Summary

Extend the existing `User` table with the identity and authorization fields Google
login needs, then ship an Alembic migration that preserves current users, study
entries, and review schedules. This ticket establishes the database contract only;
it does not authenticate requests. Before it, every request is assigned to the first
database user and a fresh install silently creates `local@localhost`. After it, the
database can distinguish Google identities by stable `google_sub`, represent the
`user` and `admin` roles, and retain pre-Phase-3 data.

`google_sub` is Google's stable subject identifier and the login key; email is
profile data and may change. Application code must validate the role as `user` or
`admin`, and the database must default existing rows to `user` so a migration never
grants privilege.

## Unlocks

- SRA-302 can perform Google sign-in and resolve a request to exactly one user.
- A migration rehearsal can start from the Phase 2 schema, seed the local user and
  owned rows, upgrade, and prove every primary and foreign key still identifies the
  same data. `get_current_user` is deliberately **not** replaced here — until
  SRA-302 the running app still has single-local-user behavior.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/db/models.py`](../src/sra/db/models.py) | edit | Unique Google subject field, a role constrained to `user`/`admin` defaulting to `user`, and nullable `last_login_at` (set by SRA-302 only after verified login). Preserve server-owned ID/timestamp conventions. |
| [`src/sra/alembic/versions/`](../src/sra/alembic/versions/) | edit | One reviewed revision after the initial schema; upgrade preserves ownership, downgrade is explicit about information loss. |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | read | Existing collaborator; its user-scoped calls need no redesign, but regression tests must prove behavior is preserved. |
| [`tests/integration/test_phase3_migration.py`](../tests/integration/test_phase3_migration.py) | run | Rehearsal against a **file** database at the previous revision. In-memory `create_all()` is not evidence an existing database upgrades correctly. |

## Acceptance criteria

- [x] A fresh database can store two OAuth-backed users with distinct subjects, and
  duplicate subjects are rejected
- [x] Only `user` and `admin` are accepted roles; existing and newly created
  ordinary users default to `user`
- [x] Upgrading a Phase 2 database preserves user IDs, study-entry ownership, review
  schedules, and timestamps
- [x] Legacy local data has a documented, operator-controlled migration path and
  cannot be claimed by the first web request
- [x] Downgrade behavior and any irreversible data loss are documented and tested at
  the appropriate level
- [x] Existing repository, agent, API, and Phase 2 tests remain green without live
  Google access

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_user_model.py tests/integration/test_phase3_migration.py -v
pytest tests/unit/test_repository.py tests/unit/test_query_tools.py -v
alembic upgrade head && ruff check . && pyright
```

The migration test must use a temporary file database, stamp it to the pre-Phase-3
revision, insert representative Phase 2 rows, upgrade, and inspect the result. It
must not touch the developer's normal `sra.db`. Fixture users need distinct
deterministic subjects — not one shared fake subject — plus a fixture representing a
legacy row.

## Manual verification

- [ ] Back up a disposable copy of a Phase 2 database, point `DATABASE_URL` at the
      copy, run `alembic upgrade head`. Inspect users, entries, and schedules before
      and after, recording IDs and counts.
- [ ] Exercise the documented legacy-data procedure with a test identity, then verify
      the same entries still belong to the same numeric user ID. Rehearse recovery
      from the backup.

> **Note:** never use a production or only copy of study data for the first
> migration run. Quiz-count, option-count, timezone, and review-time fields stay out
> of this phase; their owning phases migrate them when their behavior exists.

### Verification record

- **2026-10-14**, temporary SQLite file databases under pytest; revision
  `067a64508521` → `8cb1b420d9d2`.
- Pre-upgrade: one each of user/entry/review, IDs **41 / 51 / 61**, entry 51 owned
  by user 41. Post-upgrade: legacy subject NULL, role `user`, last login NULL; IDs,
  timestamps, entry ownership, review link, and schedule date unchanged.
- Explicit `link_legacy_user` attached a verified subject without changing user ID
  41; a second link and a duplicate subject were rejected. Unverifiable data
  remains unlinked for export/archive.
- Operator procedure requires a stopped-app file backup; recovery copies that file
  back and re-verifies its revision, IDs, and ownership. No Google/network access
  was used.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).