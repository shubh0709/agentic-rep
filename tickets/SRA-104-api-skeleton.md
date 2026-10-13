# SRA-104 — API skeleton: app factory + digest + search

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** High — first ticket that produces a running server
**Blocked by:** SRA-103 (digest/search call the repository directly)
**Blocks:** SRA-105, SRA-106, SRA-107

## Summary

Implement `create_app()` and the two read-only routes that don't need an LLM: `GET /api/v1/digest` and `GET /api/v1/search`. No LangGraph, no OpenAI calls — just FastAPI wiring on top of the repository layer you already built in SRA-103.

## Where things stand right now

Config (101), the scheduler (102), and the repository (103) all work — you've proven it with `pytest`. But every one of those is only reachable from inside a test or a one-off Python script. There is no server, nothing you can `curl`, nothing you can look at in a browser. It's easy for FastAPI, dependency injection, and routing to feel like "yet another new system" bolted on top of the ones you just learned.

They aren't a new system yet — they're a thin shell around the one you already have. This ticket proves that by wiring up a real HTTP server whose two endpoints do nothing but call the repository functions you already implemented and tested in SRA-103.

## What this ticket unlocks

After this ticket, you can run `uvicorn`, open `/docs` in a browser, and `curl` two real endpoints that return real data from a real (file-based, not in-memory-test) database. That's the first time the app is "running" in a way you can point at. It deliberately excludes the ingest and chat routes — those need an agent, which is the next kind of complexity, tackled one ticket at a time in SRA-105 and SRA-106. For now, you seed data with a small script instead of a real ingest call.

## Files

- `src/sra/api/app.py`
- `src/sra/api/routers/digest.py`
- `src/sra/api/routers/search.py`

## What to implement

- `create_app()` in `app.py` — call `init_db()` (already imported at the top of the file), construct `FastAPI(title="Spaced Repetition Agent")`, `include_router(api_router, prefix=API_V1_PREFIX)`, return the app. It's a factory (not a module-level `app = FastAPI()`) specifically so tests can call it repeatedly against an overridden DB session — don't change that shape. Note `api_router` (from `src/sra/api/routers/__init__.py`) already aggregates all four resource routers, including `entries` and `chat` — those two will keep raising `NotImplementedError` when called until SRA-105/SRA-106, and that's expected; registering them now doesn't require them to work yet.
- `get_digest(...)` in `routers/digest.py` — call `repository.get_due_reviews(user.id, date.today(), session)`, map each `ReviewSchedule` (with its parent `StudyEntry`) to a `ReviewOut`.
- `search(...)` in `routers/search.py` — call `repository.search_entries(user.id, q, session, date_from, date_to)` (parsing the `date_from`/`date_to` query strings to `date` if present), map results to `EntryOut`.

## Where to look

- Each file's docstring lists the exact steps.
- `src/sra/api/schemas.py` for `ReviewOut`/`EntryOut`'s exact fields.
- `src/sra/auth/dependencies.py`'s `get_current_user` — already implemented (Phase 1 placeholder: single local user, auto-created on first call), just `Depends()` it like the stubs already do.
- One wrinkle worth knowing up front: `get_settings()` (SRA-101) still validates `OPENAI_API_KEY` eagerly, even though nothing in this ticket calls OpenAI — the DB engine reads `Settings.database_url` off the same object. So you still need a key (a dummy string is fine locally) in `.env` for the server to even start. SRA-105/106 are what actually spend it.

## Acceptance criteria

- [ ] `create_app()` returns a working FastAPI app with all four routers mounted (entries/chat will 500 with `NotImplementedError` if called — that's fine until SRA-105/106)
- [ ] `GET /api/v1/digest` returns only the signed-in (Phase 1: the single local) user's reviews due today or earlier, still pending
- [ ] `GET /api/v1/search?q=...` matches case-insensitively; an irrelevant query returns `[]`, not an error
- [ ] `GET /api/v1/search` honors `date_from`/`date_to` when given

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_api_routes.py -v -k "digest or search"
```

Expected once implemented: 3 passed (the two `test_chat_*` tests in the same file are unaffected by this ticket and will keep failing until SRA-106 — that's expected, not a regression you caused).

## Manual verification

There's no ingest endpoint yet, so seed the real (file-based) DB directly with a script, then hit the running server from a second terminal:

```bash
source .venv/bin/activate
python3 -c "
from sqlmodel import Session
from sra.db.session import get_engine, init_db
from sra.db.models import User, UserCreate, StudyEntryCreate
from sra.db import repository

init_db()
with Session(get_engine()) as session:
    user = User.model_validate(UserCreate(email='local@localhost', display_name='Local User'))
    session.add(user); session.commit(); session.refresh(user)
    entry_create = StudyEntryCreate(
        topic='Photosynthesis', summary='light + dark reactions',
        raw_transcript='seeded for SRA-104 manual check',
    )
    repository.create_entry(entry_create=entry_create, user_id=user.id, session=session)
    print('seeded user', user.id)
"
```

```bash
uvicorn sra.api.app:create_app --factory --reload --port 8000
# in another terminal:
curl -s http://localhost:8000/api/v1/digest | python3 -m json.tool
curl -s "http://localhost:8000/api/v1/search?q=photo" | python3 -m json.tool
```

Or just open http://localhost:8000/docs and try both from Swagger UI. The digest response should show one entry with a `1d` checkpoint scheduled for tomorrow; the search response should find it by `photo`.

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `pytest tests/unit/test_api_routes.py -v -k "digest or search"` — 3 passed
- [ ] Manual curl/Swagger check against the running server returns the seeded entry from both endpoints
- [ ] Full suite: `pytest -q` (entries/chat-related failures are expected until SRA-105/106)
