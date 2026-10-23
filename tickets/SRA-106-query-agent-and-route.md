# SRA-106 — Query LangGraph agent + chat route

**Phase:** 1 — Core loop · **Status:** implemented

## Summary

Two parts, same pattern as SRA-105. Build the tool-calling agent in
`src/sra/agents/query_graph.py` that answers natural-language questions about a
user's own study history, then wire it into the already-stubbed
`POST /api/v1/chat` route. This is the piece that makes the app feel like an
assistant rather than a CRUD app.

The new thing to learn here is control flow, not vocabulary. SRA-105 was a short
linear pipeline (extract → validate → persist). This is a **loop**: the model
decides which tool to call (if any), the tool runs, its result goes back to the
model, and this repeats until the model answers instead of calling another tool.
Same `StateGraph`, nodes, and edges — genuinely different shape.

## Unlocks

- Every Phase 1 read/write path is live over HTTP: ingest, digest, search, and now
  chat.
- SRA-107 is no longer adding capability; it verifies that all four work together
  as one coherent flow.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/agents/query_graph.py`](../src/sra/agents/query_graph.py) | edit | `make_tools(user_id, session)`, the three `@tool` functions, `_get_chat_model()`, and `build_query_graph(user_id, session)`. |
| [`src/sra/api/routers/chat.py`](../src/sra/api/routers/chat.py) | edit | `chat(...)` calls `build_query_graph(user.id, session).invoke({"messages": [("user", body.question)]})` and returns `ChatResponse(answer=<final AI message content>)`. |

## Acceptance criteria

- [ ] A question naming a topic in the DB drives the graph to call
  `search_entries_tool`, and the final answer reflects that entry
- [ ] A "what's due today" question drives a call to `get_due_reviews_tool`
- [ ] The graph never surfaces another user's entries, even when asked broadly
  ("what have I studied?")
- [ ] A question about something never studied gets an honest "not found" answer,
  not a fabricated one
- [ ] `POST /api/v1/chat` with a well-formed body returns `200` and a non-empty
  answer
- [ ] `POST /api/v1/chat` with a malformed body (missing `question`) returns
  `422`, not `500`

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_query_graph.py -v
pytest tests/unit/test_api_routes.py -v -k chat
```

Expected once implemented: 3 passed then 2 passed. The graph tests use
`FakeToolCallingModel` (top of the test file) to script a deterministic tool-call
sequence — no real OpenAI call needed. Together with SRA-104's digest/search
tests, all 5 tests in `test_api_routes.py` should pass.

## Manual verification

- [ ] With `OPENAI_API_KEY` in `.env`, seed an entry via SRA-105's curl command,
      then ask about it:

      ```bash
      curl -s -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
        -d '{"question": "what did I study today?"}' | python3 -m json.tool
      ```

- [ ] The answer is grounded in the seeded entry. The same graph also runs
      standalone against an in-memory SQLite DB, invoking
      `build_query_graph(user.id, session)` with
      `{"messages": [("user", "what did I study about photosynthesis?")]}`.

> **Note:** the one thing in this ticket that matters for correctness, not just
> style, is the closure-over-`user_id` pattern in `make_tools`'s docstring: never
> let the LLM supply `user_id` as a tool argument. The model picks *what* to
> search, never *whose* data.

> **Note:** the code is implemented and Phase 1 was functionally verified with
> live OpenAI on 2026-10-07 ([`PROGRESS.md`](../PROGRESS.md)), but these
> checkboxes are still unchecked. Re-run the commands above before treating them
> as met.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).