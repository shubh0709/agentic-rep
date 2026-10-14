# Contributing

This repo is a teaching scaffold worked on by many contributors, including
people implementing their first real Python backend. The guardrails below
exist so that a mistake gets caught on your own machine, in seconds, instead
of surviving into `main`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

`pre-commit install` registers a git hook that runs lint (`ruff`) and the
type checker (`pyright`) on every `git commit`. If either fails, the commit
is blocked until you fix it — this is what would have caught the
`created_at=date` class of bug (a server-owned field passed the wrong type)
before it ever reached a PR.

## Running checks manually

```bash
ruff check .              # lint
pyright                   # type check
pytest                    # tests
pre-commit run --all-files   # everything pre-commit would run, across the whole repo
```

CI (`.github/workflows/ci.yml`) runs the same three checks on every push and
PR, so `pre-commit` catches things locally and CI is the backstop if a hook
was skipped or bypassed.

SRA-201 through SRA-205 are implemented. Run the full `pytest` suite to protect
both phases; `pytest -m 'not phase2' -q` isolates Phase 1 regressions.
Ticket-specific commands are in [tests/README.md](tests/README.md). Real Whisper
inference is opt-in, and physical microphone/global-hotkey acceptance remains
part of SRA-206 verification. Do not hide unfinished behavior with `xfail` or
assertions expecting `NotImplementedError`.

## Reproducible installs

`pyproject.toml` declares dependencies as ranges (`fastapi>=0.115`, etc.), so
two contributors installing at different times can end up with different
transitive versions. `requirements-lock.txt` is a pinned snapshot of a known-
working environment:

```bash
pip install -r requirements-lock.txt   # exact versions, for reproducing an environment
```

Regenerate it after adding or upgrading a dependency in `pyproject.toml`:

```bash
pip install -e ".[dev,desktop]"
pip freeze --exclude-editable > requirements-lock.txt
```

This snapshot covers the server/dev environment and SRA-201's `capture` extra
(HTTPX, already pinned as a dev dependency). To reproduce and verify text capture:

```bash
pip install -r requirements-lock.txt
pip install -e '.[dev,capture]' -c requirements-lock.txt
pip check
pytest tests/unit/test_client_config.py tests/unit/test_api_client.py tests/e2e/test_client_ingestion_e2e.py -q
```

The snapshot includes `faster-whisper` and the macOS `desktop` extra. Desktop-only
pins carry `sys_platform == "darwin"`; preserve these markers when regenerating
on macOS so Linux server installs do not attempt to install PyObjC. Default
tests fake those boundaries.
Real Whisper checks explicitly opt in with `SRA_RUN_WHISPER_TESTS=1`; see the
test guide for installation and fixture details. To verify desktop dependencies in a clean macOS environment, install the lockfile,
then `pip install -e '.[dev,desktop]' -c requirements-lock.txt` and run `pip check`.
Installing into a shared development environment may expose unrelated package
conflicts; use a fresh virtual environment to verify this project’s dependency set.

## Schema migrations (Alembic)

`db/session.py`'s `init_db()` (`SQLModel.metadata.create_all()`) is fine for
tests and local dev — it's fast and idempotent. It's not enough once more
than one contributor is changing the schema on separate branches: `create_all()`
only ever adds missing tables, it can't apply an `ALTER TABLE` for a column
someone else already shipped. Alembic is the source of truth for schema
changes from here on:

```bash
alembic upgrade head                              # apply migrations to your local DB
# after changing a model in src/sra/db/models.py:
alembic revision --autogenerate -m "add X to Y"   # generate a migration
# review the generated file in src/sra/alembic/versions/ before committing --
# autogenerate is a starting point, not guaranteed correct (it won't detect
# a column rename, for example -- it'll see a drop + an add).
alembic upgrade head                              # apply it locally to confirm it works
```

Commit the generated migration file alongside your model change in the same
PR. `env.py` reads the DB URL from `Settings.database_url` (env vars / `.env`),
not from `alembic.ini` directly, so it stays in sync with however the app
itself is configured.

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

Server-owned fields (`id`, `created_at`, foreign keys assigned by the
repository layer) live **only** on the `table=True` class, never on
`<Name>Create`. Repository functions build the row with:

```python
StudyEntry.model_validate(entry_create, update={"user_id": user_id})
```

not by re-listing fields as constructor kwargs. This is deliberate: a
server-owned field can't be passed by a caller because `<Name>Create`'s type
never carries it — the guarantee comes from the type, not from whoever wrote
the call site remembering to leave a field out. `model_config = {"extra":
"forbid"}` means a typo'd or unexpected field raises immediately instead of
being silently dropped.

Follow this shape for every new table. See `src/sra/db/models.py`'s module
docstring and `docs/design/technical-design.md` §3.2 for the full rationale,
and `repository.create_entry` for the reference call site.

This pattern is the same one used by the official FastAPI/SQLModel
full-stack template (`fastapi/full-stack-fastapi-template`), not something
invented for this repo.
