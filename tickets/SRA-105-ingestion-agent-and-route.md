# SRA-105 — Ingestion LangGraph agent + ingest route

**Phase:** 1 — Core loop · **Status:** verified

## Summary

Two parts, done back to back so you get one payoff at the end of each. First,
build the LangGraph agent in `src/sra/agents/ingestion_graph.py` that turns a raw
transcript into one or more structured, persisted study entries — the core
"voice note → spaced-repetition entry" logic, and the piece most worth learning
LangGraph for. Then wire it into the already-stubbed `POST /api/v1/entries/ingest`
route, so you can `curl` a real sentence and watch it become stored, scheduled
entries.

## Unlocks

- The `POST /entries/ingest` route becomes real: paste a transcript, get back
  structured entries, find them immediately through `/search`, and see their
  reviews through `/digest` when due. The first review is scheduled one day after
  studying.
- SRA-107 can verify the whole Phase 1 loop over HTTP.
- This is the first ticket where a new *kind* of system enters: instead of
  deterministic Python (scheduler math, SQL-ish queries) you ask an LLM to read
  free text and produce structured output it might get wrong or fail to produce
  at all.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/agents/ingestion_graph.py`](../src/sra/agents/ingestion_graph.py) | edit | `_get_chat_model()`, `extract_entries`, `validate_entries`, `persist_entries`, `request_clarification`, and `build_ingestion_graph()` — the graph shape is spelled out in the module docstring. |
| [`src/sra/api/routers/entries.py`](../src/sra/api/routers/entries.py) | edit | `ingest_transcript(...)` invokes the graph with `{"transcript": ..., "user_id": user.id, "session": session, "drafts": [], "entries": [], "error": None}`, maps `result["entries"]` to `EntryOut`, and raises `HTTPException(422, detail=result["error"])` when `result["error"]` is set. |
| [`src/sra/agents/schemas.py`](../src/sra/agents/schemas.py) | read | `StudyEntryDrafts`, the schema bound via `.with_structured_output(...)`. `IngestionState` at the top of the graph file carries `session`, not just `transcript`/`user_id`. |

## Acceptance criteria

- [x] A transcript naming two distinct topics produces two separate
  `StudyEntryDraft`s, not one merged draft
- [x] An LLM call failure sets `state["error"]` rather than raising, and the graph
  routes to `request_clarification`
- [x] A draft with an empty topic or summary is dropped by `validate_entries`
- [x] `persist_entries` creates exactly one `StudyEntry` (+ its 6 reviews, via
  SRA-103/102) per surviving draft
- [x] The compiled graph, run end to end on a fixture transcript against a temp
  DB, produces the expected rows
- [x] `POST /api/v1/entries/ingest` with a valid transcript returns `201` and the
  created entries
- [x] An ingestion error (e.g. empty/unparseable transcript) returns `422` with a
  useful message, not a `500`

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_ingestion_graph.py tests/unit/test_ingest_route.py -v
pytest tests/e2e/test_capture_to_digest_e2e.py -v -k ingest_then_digest
```

Recorded: 32 passed (24 graph + 8 route), and 1 passed for the ingest-to-digest
case. Most tests monkeypatch `sra.agents.ingestion_graph._get_chat_model` with a
fake model supporting `.with_structured_output(...)`; the structured-output tests
replace model generation instead, to exercise parsing. No live OpenAI call or API
key needed.

## Manual verification

- [ ] Invoking the graph directly on a two-topic transcript prints one line per
      topic:

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

- [ ] Over HTTP, with `OPENAI_API_KEY` in `.env`: `curl -X POST
      http://localhost:8000/api/v1/entries/ingest -H "Content-Type:
      application/json" -d '{"transcript": "Today I learned about
      photosynthesis."}'` returns 201; the entry appears in
      `/api/v1/search?q=photosynthesis` immediately, and its `1d` checkpoint
      appears in `/api/v1/digest` one day after creation, not immediately.

### Verification record

- **2026-10-07:** 33 graph/route/ingestion-to-digest tests passed; full suite 52
  passed / 6 failed (all in the unimplemented SRA-106 chat functions); `ruff check
  .` and `pyright` pass.
- Live multi-topic transcript after replenishing OpenAI credits: HTTP 201 with
  Photosynthesis and Mitosis entries. Both original transcripts persisted, each
  entry received all six pending checkpoints, search returned exactly the new
  Mitosis entry, and both first checkpoints appeared through the digest route
  with tomorrow's date simulated.
- Automated verification uses scripted model responses, including a mocked
  generation call through the real structured-output parser. Live extraction
  quality needs a configured OpenAI key.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).