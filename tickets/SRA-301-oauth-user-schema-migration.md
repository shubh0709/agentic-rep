# SRA-301 — OAuth identity schema and safe legacy migration

**Type:** Task
**Phase:** 3 — Multi-tenancy, authentication, RBAC, and rate limiting
**Priority:** Highest — every authenticated request needs a durable identity before login can replace the local-user placeholder
**Status:** Done — 2026-10-14
**Blocked by:** SRA-206 (Phase 2 completion)
**Blocks:** SRA-302

## Summary

Extend the existing `User` table with the identity and authorization fields that
Google login needs, then ship an Alembic migration that preserves current users,
study entries, and review schedules. This ticket establishes the database
contract only; it does not authenticate requests yet.

Before this ticket, every request is assigned to the first database user and a
fresh installation silently creates `local@localhost`. After it, the database
can distinguish Google identities by stable `google_sub`, represent the `user`
and `admin` roles, and retain pre-Phase-3 data without exposing it to whichever
person happens to sign in first.

## Where things stand right now

[`src/sra/db/models.py`](../src/sra/db/models.py) already defines a minimal
`User` with `id`, `email`, `display_name`, and `created_at`. `StudyEntry.user_id`
already points at that table, and repository/query functions already require a
trusted `user_id`. The initial Alembic revision creates these tables. What is
missing is an external identity key, a role, login timestamps, and a migration
path for databases that already contain the Phase 1/2 local user.

The older plan describes `User` as new and includes quiz and wake-up preferences.
The current code makes the table pre-existing, while quiz preferences belong to
Phase 4 and wake-up time/timezone belong to Phase 5. This ticket adds only the
Phase 3 fields: `google_sub`, `role`, and `last_login_at`, while retaining the
existing email, name, ID, and creation time.

## What this ticket unlocks

A migration rehearsal can start from the Phase 2 schema, seed the local user and
owned study rows, upgrade to the Phase 3 schema, and prove that every primary and
foreign key still identifies the same data. A separate fresh-database check can
create two OAuth-backed users with different Google subject IDs and reject a
duplicate subject.

SRA-302 will use that durable identity to perform Google sign-in and resolve a
request to exactly one user. This ticket deliberately does not replace
`get_current_user`; until SRA-302 is complete the running app still has its
single-local-user behavior.

## How this fits into the whole app

```mermaid
flowchart LR
    OldDB[(Phase 2 database)] -->|Alembic upgrade| Migration[New Phase 3 revision]
    Migration --> Users[(users with OAuth and role fields)]
    Migration --> Entries[(existing study_entries)]
    Entries -->|same user_id foreign key| Users
    Google[Future verified Google identity] -. SRA-302 upsert by google_sub .-> Users
    Users -. trusted user.id .-> Routes[Authenticated routes]
    Routes -. existing user-scoped calls .-> Repo[repository.py and query tools]
```

The solid arrows are this ticket's migration flow. The dotted arrows show why
the fields exist, but are implemented in SRA-302. `google_sub` is Google's stable
subject identifier and is the login key; email is profile data and may change.
Application code must validate the role as `user` or `admin`, and the database
must default existing rows to `user` so a migration never grants privilege.

### Existing local data

An upgraded database may contain a user with no verified Google identity. Keep
that row and all its foreign keys intact, but do not let the first unauthenticated
visitor claim it automatically. The implementation must provide and document an
explicit operator-controlled link or export/reset procedure before SRA-302 turns
off local-user fallback. Record the chosen procedure and test it. If a staged
nullable `google_sub` is used for legacy rows, new OAuth-created users must still
require a non-empty subject and the schema limitation must be documented; do not
misrepresent that intermediate state as the design's final non-null invariant.

## Files and why they matter

| File | Role and required change |
|---|---|
| [`src/sra/db/models.py`](../src/sra/db/models.py) | Extend the existing table model with the Phase 3 identity/role fields and validation. Preserve the server-owned ID/timestamp conventions. |
| [`src/sra/alembic/versions/`](../src/sra/alembic/versions/) | Add one reviewed revision after the initial schema. Its upgrade must preserve existing ownership; its downgrade must be explicit about any information loss. |
| [`tests/unit/test_user_model.py`](../tests/unit/) | New focused contract tests for defaults, allowed roles, stable-subject uniqueness, and profile-field behavior. |
| [`tests/integration/test_phase3_migration.py`](../tests/) | New migration rehearsal against a file database created at the previous revision, including owned entries and reviews. In-memory `create_all()` is not evidence that an existing database upgrades correctly. |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | Existing collaborator to inspect. Its user-scoped calls should not need redesign; regression tests must show their behavior is preserved. |
| [`tests/conftest.py`](../tests/conftest.py) | Update user fixtures to create valid Phase 3 users without making every old test depend on Google network calls. |
| [`.env.example`](../.env.example), [`README.md`](../README.md), [`PROGRESS.md`](../PROGRESS.md) | Document the legacy-data decision and migration command. OAuth secrets themselves arrive in SRA-302. |

## TDD starting point

No Phase 3 test or learning stub is present today. Prepare the typed model
changes, migration fixture/support, and failing behavior tests before implementing
the migration. Model declarations and Alembic operations are schema decisions,
not `NotImplementedError` exercises; the learning work is reasoning about
identity invariants, safe upgrades, and verification.

Do not weaken existing fixtures by assigning every user the same fake subject.
Use distinct deterministic values and retain a fixture representing a legacy
row when testing migration behavior.

## What to implement

- Add a unique Google subject field used as the external identity key. Treat
  email and display name as profile attributes that can change, never as login
  keys.
- Add a role that defaults to `user`; accept only the `user` and `admin` values.
  Existing rows must migrate to `user`, never `admin`.
- Add nullable `last_login_at`; SRA-302 updates it only after verified login.
- Generate and review an Alembic revision. Test upgrade from the actual previous
  revision, not only table creation from current metadata.
- Preserve existing user IDs, entry ownership, review rows, timestamps, and
  unique constraints through upgrade.
- Define an explicit operator-controlled path for legacy local data. It must not
  be claimable merely by being the first person to reach a public server.
- Keep future quiz-count, option-count, timezone, and review-time fields out of
  this phase; their owning phases can migrate them when their behavior exists.

## Where to look

- Technical design §2.2 and §3.2 for the intended user fields.
- Plan KTD8 and U11 for the stable-subject and per-user invariants.
- [`CONTRIBUTING.md`](../CONTRIBUTING.md#schema-migrations-alembic) for the
  repository's Alembic workflow.
- [`src/sra/alembic/versions/067a64508521_initial_schema.py`](../src/sra/alembic/versions/067a64508521_initial_schema.py)
  for the schema that an existing installation actually has.

## Acceptance criteria

- [x] A fresh database can store two OAuth-backed users with distinct subjects, and duplicate subjects are rejected
- [x] Only `user` and `admin` are accepted roles; existing and newly created ordinary users default to `user`
- [x] Upgrading a Phase 2 database preserves user IDs, study-entry ownership, review schedules, and timestamps
- [x] Legacy local data has a documented, operator-controlled migration path and cannot be claimed by the first web request
- [x] Downgrade behavior and any irreversible data loss are documented and tested at the appropriate level
- [x] Existing repository, agent, API, and Phase 2 tests remain green without live Google access

## How to test this ticket alone

The implementation should make these focused commands available:

```bash
source .venv/bin/activate
pytest tests/unit/test_user_model.py -v
pytest tests/integration/test_phase3_migration.py -v
pytest tests/unit/test_repository.py tests/unit/test_query_tools.py -v
alembic upgrade head
ruff check .
pyright
```

The migration integration test must use a temporary file database, downgrade or
stamp it to the pre-Phase-3 revision, insert representative Phase 2 rows, run the
upgrade, and inspect the upgraded data. It must not touch the developer's normal
`sra.db`.

## Manual verification

Back up a disposable copy of a Phase 2 database, point `DATABASE_URL` at the
copy, and run `alembic upgrade head`. Inspect the users, entries, and schedules
before and after, recording IDs and counts. Exercise the documented legacy-data
procedure with a test identity, then verify the same entries still belong to the
same numeric user ID. Also rehearse the recovery path from the backup.

Do not use a production or only copy of study data for the first migration run.

## Verification record

- Date, environment, and old/new revision IDs: 2026-10-14, temporary SQLite file databases under pytest; `067a64508521` → `8cb1b420d9d2`.
- Backup and recovery location/procedure: automated rehearsal uses disposable temporary copies; operator procedure requires a stopped-app file backup and restores that file for recovery.
- Pre-upgrade user, entry, and review IDs/counts: one each, IDs 41/51/61, with entry 51 owned by user 41.
- Post-upgrade identity fields and preserved ownership: legacy subject NULL, role `user`, last login NULL; IDs, timestamps, entry ownership, review link, and schedule date unchanged.
- Legacy linking/export/reset result: explicit `link_legacy_user` attached a verified subject without changing user ID 41; a second link and duplicate subject were rejected. Unverifiable data remains unlinked for export/archive.
- Automated results, skips, and limitations: focused model and migration tests, completed Phase 1/2 regressions, repository/query regressions, Ruff, and Pyright passed. No Google/network access was used; real OAuth belongs to SRA-302.

## Definition of Done

- [x] Acceptance criteria are met and actual migration evidence is recorded
- [x] Focused, regression, lint, and type checks pass
- [x] Migration and recovery instructions match the verified behavior
- [x] Ticket board and progress documentation reflect the actual state
