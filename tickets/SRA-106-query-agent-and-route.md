# SRA-106 — Query LangGraph agent + chat route

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** Medium
**Blocked by:** SRA-101 (needs `get_settings()`), SRA-103 (tools call `repository.get_due_reviews`/`search_entries`/etc.), SRA-104 (app + routing already exist to plug this into)
**Blocks:** SRA-107

## Summary

Two parts, same pattern as SRA-105:

1. Build the tool-calling agent in `src/sra/agents/query_graph.py` that answers natural-language questions about a user's own study history.
2. Wire it into the already-stubbed `POST /api/v1/chat` route.

## Where things stand right now

Everything else in Phase 1 is working: you can post a transcript and have it become scheduled entries (SRA-105), see what's due (`/digest`), and find things by keyword (`/search`). What's missing is the piece that makes this feel like an assistant rather than a CRUD app — asking a free-form question and getting an answer grounded in your own data.

This ticket introduces a different LangGraph shape than SRA-105's: instead of a short linear pipeline (extract → validate → persist), it's a loop — the model decides *which* tool to call (if any), the tool runs, its result goes back to the model, and this repeats until the model answers instead of calling another tool. It's the same LangGraph vocabulary you already used in SRA-105 (`StateGraph`, nodes, edges), applied to a genuinely different control-flow shape, which is the main new thing to learn here.

## What this ticket unlocks

Once both parts are done, every Phase 1 read/write path is live over HTTP: ingest, digest, search, and now chat. SRA-107 is just verifying that all four work together as one coherent flow — this is the last new capability, not just more wiring.

## Files

- `src/sra/agents/query_graph.py`
- `src/sra/api/routers/chat.py`

## Part 1 — the graph

- `make_tools(user_id, session)` — build the three `@tool`-decorated functions below as a list, each closed over `user_id`/`session` so the LLM can pick *what* to search but never *whose* data.
- `search_entries_tool`, `get_due_reviews_tool`, `get_entry_detail_tool` — the tool bodies, each calling the matching `repository` function and formatting the result as a string the LLM can read.
- `_get_chat_model()` — same seam as SRA-105's, for test mocking.
- `build_query_graph(user_id, session)` — wire a `[agent] <-> [tools]` loop: an agent node calling `_get_chat_model().bind_tools(make_tools(user_id, session))`, looping to a tool-execution node and back until the model responds with no further tool calls. Return the compiled graph.

### Where to look (Part 1)

- `docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md`, KTD5, for the loop shape.
- `langgraph.prebuilt.create_react_agent` is worth reading even if you wire the loop by hand — same shape.
- The closure-over-`user_id` pattern in `make_tools`'s docstring — this is the one thing in this ticket that matters for correctness, not just style: never let the LLM supply `user_id` as a tool argument.

## Part 2 — the route

- `chat(...)` in `routers/chat.py` — call `build_query_graph(user.id, session).invoke({"messages": [("user", body.question)]})`, return `ChatResponse(answer=<final AI message content>)`.

### Where to look (Part 2)

- The docstring in `routers/chat.py` lists the same steps.
- `src/sra/api/schemas.py` for `ChatRequest`/`ChatResponse`'s fields.

## Acceptance criteria

- [ ] A question naming a topic in the DB drives the graph to call `search_entries_tool`, and the final answer reflects that entry
- [ ] A "what's due today" question drives a call to `get_due_reviews_tool`
- [ ] The graph never surfaces another user's entries, even when asked broadly ("what have I studied?")
- [ ] A question about something never studied gets an honest "not found" answer, not a fabricated one
- [ ] `POST /api/v1/chat` with a well-formed body returns `200` and a non-empty answer
- [ ] `POST /api/v1/chat` with a malformed body (missing `question`) returns `422`, not `500`

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_query_graph.py -v
```

Expected once implemented: 3 passed. Tests use `FakeToolCallingModel` (top of the test file) to script a deterministic tool-call sequence — no real OpenAI call needed.

```bash
pytest tests/unit/test_api_routes.py -v -k chat
```

Expected once implemented: 2 passed (the digest/search tests in the same file were already covered by SRA-104).

## Manual verification (uses a real OpenAI call — needs `OPENAI_API_KEY` in `.env`)

Graph in isolation:

```bash
python3 -c "
from sqlmodel import Session, SQLModel, create_engine
from sra.db.models import User, UserCreate, StudyEntryCreate
from sra.db import repository
from sra.agents.query_graph import build_query_graph

engine = create_engine('sqlite://')
SQLModel.metadata.create_all(engine)
with Session(engine) as session:
    user = User.model_validate(UserCreate(email='me@local', display_name='Me'))
    session.add(user); session.commit(); session.refresh(user)
    entry_create = StudyEntryCreate(topic='Photosynthesis', summary='light + dark reactions', raw_transcript='today I studied photosynthesis')
    repository.create_entry(entry_create=entry_create, user_id=user.id, session=session)

    graph = build_query_graph(user.id, session)
    result = graph.invoke({'messages': [('user', 'what did I study about photosynthesis?')]})
    print(result['messages'][-1].content)
"
```

Now the whole Phase 1 loop, over HTTP:

```bash
uvicorn sra.api.app:create_app --factory --reload --port 8000
# in another terminal, after ingesting something via SRA-105's curl command:
curl -s -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
  -d '{"question": "what did I study today?"}' | python3 -m json.tool
```

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `pytest tests/unit/test_query_graph.py -v` — 3 passed
- [ ] `pytest tests/unit/test_api_routes.py -v` — all 5 passed (digest/search from SRA-104, chat from this ticket)
- [ ] Manual verification gives a sensible answer grounded in the seeded entry, both directly and via the running server
- [ ] Full suite: `pytest -q`
