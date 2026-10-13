# SRA-105 — Ingestion LangGraph agent + ingest route

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** High
**Status:** Pending implementation
**Blocked by:** SRA-101 (needs `get_settings()` for the OpenAI key), SRA-103 (`persist_entries` calls `repository.create_entry`), SRA-104 (app + routing already exist to plug this into)
**Blocks:** SRA-107

## Summary

Two parts, done back to back so you get one payoff at the end of each:

1. Build the LangGraph agent in `src/sra/agents/ingestion_graph.py` that turns a raw transcript into one or more structured, persisted study entries — the core "voice note → spaced-repetition entry" logic, and the piece most worth learning LangGraph for.
2. Wire it into the already-stubbed `POST /api/v1/entries/ingest` route, so you can `curl` a real sentence and watch it become stored, scheduled entries.

## Where things stand right now

After SRA-104 you have a real server, but the only way data gets into it is a hand-written seed script — there's no ingestion story at all, which is the actual point of this project. Meanwhile `GET /digest` and `GET /search` are sitting there fully working, just waiting for real data to show up through the front door.

This is also the first ticket where a new *kind* of system enters the picture: instead of deterministic Python (scheduler math, SQL-ish repository queries), you're now asking an LLM to read free text and produce structured output it might get wrong or fail to produce at all. That's worth taking on its own, without FastAPI/routing also being new at the same time — which is why SRA-104 came first.

## What this ticket unlocks

Once both parts are done, the `POST /entries/ingest` route becomes real: paste a transcript, get back structured entries, find them immediately through `/search`, and see their reviews through `/digest` when due. The first review is scheduled one day after studying. That's the first time the app does something no plain CRUD app does — turn unstructured speech into a scheduled study plan.

## Files

- `src/sra/agents/ingestion_graph.py`
- `src/sra/api/routers/entries.py`
- `src/sra/agents/schemas.py`
- `tests/unit/test_ingestion_graph.py`
- `tests/unit/test_ingest_route.py`
- `tests/e2e/test_capture_to_digest_e2e.py`

## Part 1 — the graph

- `_get_chat_model()` — construct and return the `ChatOpenAI` model. Kept separate so tests can monkeypatch it with a fake model instead of making a real API call.
- `extract_entries(state)` — get the model from `_get_chat_model()`, bind `StudyEntryDrafts` with `.with_structured_output(...)`, invoke it with a prompt built from `state["transcript"]`, then read `result.drafts` into the state. A transcript may cover multiple topics — return one draft per topic. On an LLM failure, set `state["error"]` instead of raising.
- `validate_entries(state)` — drop drafts with an empty topic/summary; set `state["error"]` if none survive.
- `persist_entries(state)` — for each surviving draft, build a `StudyEntryCreate` (topic/subtopic/tags/summary from the draft, `raw_transcript` from `state["transcript"]`) and call `repository.create_entry(entry_create=..., user_id=state["user_id"], session=state["session"])`, collecting results into `state["entries"]`.
- `request_clarification(state)` — terminal node for the invalid/empty branch.
- `build_ingestion_graph()` — wire the four nodes above with LangGraph's `StateGraph`, `add_node`, `add_edge`, and `add_conditional_edges` per the shape in the module docstring. Return the compiled graph.

### Where to look (Part 1)

- Graph shape diagram: `docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md`, KTD4.
- Structured-output pattern: LangChain docs for `.with_structured_output(...)` on a chat model.
- `IngestionState` TypedDict at the top of the file — note it carries `session` (the DB session), not just `transcript`/`user_id`.

## Part 2 — the route

- `ingest_transcript(...)` in `routers/entries.py` — build `build_ingestion_graph()`, invoke it with `{"transcript": ..., "user_id": user.id, "session": session, "drafts": [], "entries": [], "error": None}`, map `result["entries"]` to `EntryOut`, return them. If `result["error"]` is set instead, raise `HTTPException(422, detail=result["error"])`.

### Where to look (Part 2)

- The docstring in `routers/entries.py` lists the same steps.
- `src/sra/api/schemas.py` for `EntryOut`'s exact fields.

## Acceptance criteria

- [ ] A transcript naming two distinct topics produces two separate `StudyEntryDraft`s, not one merged draft
- [ ] An LLM call failure sets `state["error"]` rather than raising, and the graph routes to `request_clarification`
- [ ] A draft with an empty topic or summary is dropped by `validate_entries`
- [ ] `persist_entries` creates exactly one `StudyEntry` (+ its 6 reviews, via SRA-103/102) per surviving draft
- [ ] The compiled graph, run end to end on a fixture transcript against a temp DB, produces the expected rows
- [ ] `POST /api/v1/entries/ingest` with a valid transcript returns `201` and the created entries
- [ ] An ingestion error (e.g. empty/unparseable transcript) returns `422` with a useful message, not a `500`

Automated verification uses scripted model responses, including a mocked generation call through the real structured-output parser. Live extraction quality remains to be verified with a configured OpenAI key.

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_ingestion_graph.py tests/unit/test_ingest_route.py -v
```

Expected: 32 passed (24 graph tests and 8 route tests). Most tests monkeypatch `sra.agents.ingestion_graph._get_chat_model` with a fake model that supports `.with_structured_output(...)`. Structured-output tests instead replace model generation to exercise parsing. No live OpenAI call or API key is needed.

The route tests cover success, invalid drafts, empty transcripts, model failures, response fields, ownership, review creation, and search visibility. To also exercise the ingestion-to-digest flow before SRA-106 exists:

```bash
pytest tests/e2e/test_capture_to_digest_e2e.py -v -k ingest_then_digest
```

Expected: 1 passed. This test checks that no review is due on the capture date, then advances the digest date by one day and checks the first checkpoint. The chat flows remain SRA-106's responsibility.

## Manual verification (uses a real OpenAI call — needs `OPENAI_API_KEY` in `.env`)

Graph in isolation:

```bash
python3 -c "
from sqlmodel import Session, SQLModel, create_engine
from sra.db.models import User
from sra.agents.ingestion_graph import build_ingestion_graph

engine = create_engine('sqlite://')
SQLModel.metadata.create_all(engine)
with Session(engine) as session:
    user = User(email='me@local', display_name='Me')
    session.add(user); session.commit(); session.refresh(user)

    graph = build_ingestion_graph()
    result = graph.invoke({
        'transcript': 'Today I studied photosynthesis and mitosis.',
        'user_id': user.id, 'session': session,
        'drafts': [], 'entries': [], 'error': None,
    })
    for e in result['entries']:
        print(e.topic, '-', e.summary)
"
```

Should print two lines, one per topic.

Now the full loop, over HTTP, chained with the endpoints SRA-104 already built:

```bash
uvicorn sra.api.app:create_app --factory --reload --port 8000
# in another terminal:
curl -s -X POST http://localhost:8000/api/v1/entries/ingest \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Today I learned about photosynthesis."}' | python3 -m json.tool

curl -s http://localhost:8000/api/v1/digest | python3 -m json.tool
curl -s "http://localhost:8000/api/v1/search?q=photosynthesis" | python3 -m json.tool
```

The entry should appear in search immediately. Its `1d` checkpoint appears in the digest one day after the entry's creation date, not immediately after ingestion.

## Definition of Done

- [ ] Acceptance criteria verified automatically with mocked model responses
- [ ] Graph, route, and ingestion-to-digest tests — 33 passed
- [ ] Manual verification produces sensible entries from a real transcript, both directly and via the running server
- [ ] Full suite: 52 passed, 6 failed in the unimplemented SRA-106 chat/query functions
- [ ] `ruff check .` and `pyright` pass

Verified on 2026-10-07. After replenishing OpenAI credits, the live multi-topic transcript returned HTTP 201 with Photosynthesis and Mitosis entries. Both original transcripts were persisted and each entry received all six pending review checkpoints. Search returned exactly the new Mitosis entry, and both new first checkpoints appeared through the digest route with tomorrow's date simulated.
