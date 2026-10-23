# SRA-104 — API skeleton: app factory + digest + search

**Phase:** 1 — Core loop · **Status:** implemented

## Summary

Config (SRA-101), the scheduler (SRA-102), and the repository (SRA-103) all work,
but every one of them is only reachable from inside a test or a one-off Python
script. There is no server, nothing to `curl`, nothing to look at in a browser.
This ticket wires a real HTTP server whose two endpoints do nothing but call the
repository functions you already implemented and tested. No LangGraph, no OpenAI
calls — FastAPI, dependency injection, and routing turn out to be a thin shell
around the layer you already have.

## Unlocks

- You can run `uvicorn`, open `/docs` in a browser, and `curl` two real endpoints
  returning real data from the real file-based database.
- SRA-105 and SRA-106 plug their agent-backed routes into an app factory that
  already exists. For now you seed data with a script instead of a real ingest
  call.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/api/app.py`](../src/sra/api/app.py) | edit | `create_app()` calls the already-imported `init_db()`, builds `FastAPI(title="Spaced Repetition Agent")`, and returns it. |
| [`src/sra/api/routers/digest.py`](../src/sra/api/routers/digest.py) | edit | `get_digest(...)` calls `repository.get_due_reviews(user.id, date.today(), session)`, mapping each `ReviewSchedule` (with its parent `StudyEntry`) to a `ReviewOut`. |
| [`src/sra/api/routers/search.py`](../src/sra/api/routers/search.py) | edit | `search(...)` calls `repository.search_entries(user.id, q, session, date_from, date_to)`, parsing `date_from`/`date_to` to `date` if present, mapping to `EntryOut`. |

## Acceptance criteria

- [ ] `create_app()` returns a working FastAPI app with all four routers mounted
  (entries/chat will 500 with `NotImplementedError` if called — that's fine until
  SRA-105/106)
- [ ] `GET /api/v1/digest` returns only the signed-in (Phase 1: the single local)
  user's reviews due today or earlier, still pending
- [ ] `GET /api/v1/search?q=...` matches case-insensitively; an irrelevant query
  returns `[]`, not an error
- [ ] `GET /api/v1/search` honors `date_from`/`date_to` when given

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_api_routes.py -v -k "digest or search"
```

Expected once implemented: 3 passed. The two `test_chat_*` tests in the same
file keep failing until SRA-106 — expected, not a regression you caused.

## Manual verification

- [ ] Seed the real file-based DB (there is no ingest endpoint yet), then run
      `uvicorn sra.api.app:create_app --factory --reload --port 8000` and, from a
      second terminal:

      ```bash
      python3 -c "
      from sqlmodel import Session
      from sra.db.session import get_engine, init_db
      from sra.db.models import User, UserCreate, StudyEntryCreate
      from sra.db import repository

      init_db()
      with Session(get_engine()) as session:
          user = User.model_validate(UserCreate(email='local@localhost', display_name='Local User'))
          session.add(user); session.commit(); session.refresh(user)
          repository.create_entry(
              entry_create=StudyEntryCreate(topic='Photosynthesis', summary='light + dark reactions',
                                            raw_transcript='seeded for SRA-104 manual check'),
              user_id=user.id, session=session)
          print('seeded user', user.id)
      "
      curl -s http://localhost:8000/api/v1/digest | python3 -m json.tool
      curl -s "http://localhost:8000/api/v1/search?q=photo" | python3 -m json.tool
      ```

- [ ] Digest shows one entry with a `1d` checkpoint scheduled for tomorrow;
      search finds it by `photo`. http://localhost:8000/docs works too.

> **Note:** `get_settings()` still validates `OPENAI_API_KEY` eagerly even though
> nothing here calls OpenAI — the DB engine reads `Settings.database_url` off the
> same object. You still need a key (a dummy string is fine locally) in `.env`
> for the server to start. SRA-105/106 are what actually spend it.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).