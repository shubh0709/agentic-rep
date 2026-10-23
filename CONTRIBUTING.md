# Contributing

*Owns developer setup and the daily checks: virtualenv, pre-commit, lint, type-check, test invocation, CI, lockfile regeneration, migrations, and the DB model convention. Commands, markers, opt-in suites, and the manual-evidence rule live in [`tests/README.md`](tests/README.md).*

A teaching scaffold worked on by many contributors, including people implementing their first real Python backend. These guardrails exist so a mistake is caught on your own machine, in seconds, not in `main`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

The hook runs lint (`ruff`) and the type checker (`pyright`) on every `git commit`; a failure blocks the commit. That is what would have caught the `created_at=date` class of bug before it reached a PR. The optional `capture` and `desktop` extras are never needed by the default suite.

## Running checks manually

```bash
ruff check .                  # lint
pyright                       # type check
pytest                        # default suite
pre-commit run --all-files    # everything pre-commit would run, repo-wide
```

CI (`.github/workflows/ci.yml`) runs the same three checks on every push and PR, plus a `requirements-lock.txt` staleness check.

## Which tests to run

*Which* scope only — [`tests/README.md`](tests/README.md) owns the commands, markers, opt-in suites, and the manual-evidence rule.

| Scope | When |
|---|---|
| Default `pytest -q` | Always. Protects every implemented phase at once. |
| `-m 'not phase<N>'` / `-m phase<N>` | One phase at a time: prove earlier phases did not regress, or isolate one. |
| Ticket-focused paths | Indexed per ticket in `tickets/README.md`. |
| `make test-data` / `make test-live` | Opt-in real-audio verification. |
| Physical desktop acceptance | Manual; never automated. |

Never hide unfinished behavior with `xfail`, blanket skips, or assertions expecting `NotImplementedError`. A green offline suite is never evidence of real Google, Keychain, rate limiting, microphone, or hotkey behavior.

## Writing rate-limit tests

- Use `create_app(rate_limit_clock=fake_clock)` and fresh app state; never sleep for tokens.
- After changing environment variables in a test, call `get_settings.cache_clear()` **before** constructing the app — an existing app keeps its original limiter policy.
- `app.state.rate_limits.reset()` explicitly resets both stores; lifespan shutdown clears them. Cleanup on requests/summaries expires idle keys, so the TTL must cover the full refill time.
- Run `tests/unit/test_rate_limit.py` and `tests/integration/test_rate_limited_routes.py` together for math, concurrency, configuration, HTTP ordering, RBAC, and redaction coverage.

See [API rate limits](README.md#api-rate-limits) for units, tuning, proxy trust, restart limits, and the proposed Redis seam.

## Reproducible installs

`pyproject.toml` declares dependency ranges, so two contributors installing at different times can end up with different transitive versions. `requirements-lock.txt` is a pinned snapshot of a known-working environment (`pip install -r requirements-lock.txt`). Regenerate after changing a dependency — CI fails if stale:

```bash
pip install -e ".[dev,desktop]"
pip freeze --exclude-editable > requirements-lock.txt
```

It covers the server/dev environment plus the `capture` and macOS `desktop` extras. Desktop-only pins carry `sys_platform == "darwin"`; preserve those markers when regenerating on macOS so Linux installs do not try PyObjC. Verify a clean environment with the lockfile, then `pip install -e '.[dev,desktop]' -c requirements-lock.txt`, then `pip check`.

## Schema migrations (Alembic)

`db/session.py`'s `init_db()` (`SQLModel.metadata.create_all()`) is fine for tests and local dev: fast and idempotent. It is not enough once more than one contributor changes the schema on separate branches — `create_all()` only ever adds missing tables, so it cannot apply an `ALTER TABLE` for a column someone else shipped. Alembic is the source of truth from here on:

```bash
alembic upgrade head                              # apply migrations to your local DB
# after changing a model in src/sra/db/models.py:
alembic revision --autogenerate -m "add X to Y"   # generate a migration
alembic upgrade head                              # apply it locally to confirm it works
```

Review the generated file in `src/sra/alembic/versions/` before committing. Autogenerate is a starting point, not guaranteed correct — it sees a column rename as a drop plus an add. Commit the migration alongside your model change in the same PR. `env.py` reads the DB URL from `Settings.database_url`, not `alembic.ini`, so it stays in sync with the app.

## The Base / Create / Table convention for DB models

Every table in `src/sra/db/models.py` is split into three classes:

```python
class StudyEntryBase(SQLModel):       # fields shared by input and storage
    ...

class StudyEntryCreate(StudyEntryBase):  # ONLY what a caller may supply
    model_config = {"extra": "forbid"}

class StudyEntry(StudyEntryBase, table=True):  # adds server-owned fields
    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    created_at: datetime = Field(default_factory=_utcnow)
```

Server-owned fields (`id`, `created_at`, foreign keys assigned by the repository layer) live **only** on the `table=True` class, never on `<Name>Create`. Repository functions build the row with `StudyEntry.model_validate(entry_create, update={"user_id": user_id})` rather than re-listing fields as constructor kwargs. This is deliberate: a server-owned field cannot be passed by a caller because `<Name>Create`'s type never carries it — the guarantee comes from the type, not from remembering to leave a field out. `model_config = {"extra": "forbid"}` makes a typo'd field raise instead of being silently dropped. See `src/sra/db/models.py`'s module docstring and `docs/design/technical-design.md` §3.2 for the rationale, and `repository.create_entry` for the reference call site.