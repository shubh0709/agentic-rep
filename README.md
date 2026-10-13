# Spaced Repetition Agent

An open-source, self-hostable study agent: capture what you studied, get it resurfaced on a spaced-repetition schedule, and quiz yourself on it — built with LangChain, LangGraph, and FastAPI.

This repo is a **teaching project**. Phase 1 is implemented and verified. Phase 2 now has tests and typed teaching stubs: you implement the functions, guided by their docstrings and failing assertions. Supporting plumbing (error types, protocols, configuration fields, ORM models, and DB sessions) is provided. See the [Phase 2 TDD guide](tests/README.md) for the implementation order and test commands.

See [`docs/plans/`](docs/plans/) for the full implementation plan and [`docs/design/technical-design.md`](docs/design/technical-design.md) for the HLD/LLD/database/API design.

## Build phases

The project is being built incrementally, smallest working slice first:

- [x] **Phase 1 — Core loop.** Single local user, no auth yet. An ingestion agent turns a pasted transcript into structured study entries, a fixed-interval scheduler (1d/3d/7d/1mo/3mo/6mo) schedules reviews, and a minimal API exposes ingest / digest / search / chat.
- [ ] **Phase 2 — Voice capture.** Local Whisper transcription + a macOS menu-bar hotkey client, wired to Phase 1's ingest endpoint.
- [ ] **Phase 3 — Multi-tenancy.** Google OAuth login, real per-user data isolation, RBAC (user/admin), API rate limiting.
- [ ] **Phase 4 — Daily quiz.** MCQ generation agent, nightly pre-generation job, web quiz flow.
- [ ] **Phase 5 — Wake-up & voice quiz.** Per-user scheduled time, spoken digest read-out, voice quiz conversation agent.
- [ ] **Phase 6 — Open-source packaging.** Docker, docker-compose, demo mode, self-host docs.

## Phase 1 — what's here

```
src/sra/
  config.py              Settings + get_settings() [1 stub]
  db/
    models.py             User, StudyEntry, ReviewSchedule (concrete)
    session.py             DB engine/session setup (concrete)
    scheduler.py           compute_review_dates, schedule_reviews_for_entry [stubs]
    repository.py          create_entry, get_due_reviews, search_entries, mark_review_done [stubs]
  auth/
    dependencies.py         get_current_user — Phase 1 placeholder (concrete; real OAuth lands in Phase 3)
  agents/
    schemas.py               StudyEntryDraft (concrete)
    ingestion_graph.py        LangGraph: transcript -> structured entries [stubs]
    query_graph.py            LangGraph: tool-calling Q&A over your history [stubs]
  api/
    schemas.py                 Request/response DTOs (concrete)
    deps.py                     Shared dependencies re-exported for routers (concrete)
    app.py                      create_app() [stub]
    routers/
      entries.py                 POST /entries/ingest [stub]
      digest.py                  GET /digest [stub]
      search.py                  GET /search [stub]
      chat.py                    POST /chat [stub]
  alembic/                     Schema migrations (env.py wired to Settings.database_url)
tests/
  unit/          one test module per stubbed module above
  e2e/           ingest -> digest, end to end
```

Every router in `api/routers/` is mounted under `/api/v1` by `create_app()` (via `api/routers/__init__.py`'s aggregate `api_router`), so the routes above are reachable at `/api/v1/entries/ingest`, `/api/v1/digest`, etc.

Phase 1 intentionally skips voice capture — you paste/type a transcript into `/api/v1/entries/ingest` to exercise the ingestion agent and scheduler before any audio pipeline exists.

## Getting started

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install     # runs ruff + pyright on every commit — see CONTRIBUTING.md
cp .env.example .env   # first setup only; fill in OPENAI_API_KEY
alembic upgrade head   # create the DB schema (see CONTRIBUTING.md for the migration workflow)

pytest -m 'not phase2' -q  # existing Phase 1 regression tests
pytest -q                 # includes intentionally red Phase 2 teaching contracts
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow (lint/type-check/tests, reproducible installs, migrations) and the DB model conventions every new table should follow.

The implemented Phase 1 API can already run:

```bash
uvicorn sra.api.app:create_app --factory --reload
```

Then `POST /api/v1/entries/ingest {"transcript": "..."}`, `GET /api/v1/digest`, `GET /api/v1/search?q=...`, `POST /api/v1/chat {"question": "..."}`.

## Phase 2 — implement against the tests

Start with [SRA-201](tickets/SRA-201-text-capture-api-client.md), then work through
SRA-202–205. The HTTP client, transcription, pipeline, recorder, and menu-bar
controller functions deliberately raise `NotImplementedError`. All their tests
are supplied, including end-to-end flows through the real API/database/scheduler.
The [test guide](tests/README.md) distinguishes unit, end-to-end, real Whisper,
and manual desktop checks and explains the expected red/green workflow.

Default tests only need `'.[dev]'`. Once implementing real audio, use
`pip install -e '.[dev,capture]'`, or `'.[dev,desktop]'` on macOS for microphone
and menu-bar dependencies. Client settings are `API_BASE_URL` (default
`http://127.0.0.1:8000`, without `/api/v1`), `WHISPER_MODEL_SIZE` (`base`), and
`HOTKEY_COMBO` (`<ctrl>+<alt>+r`). The client needs no server credentials.
The complete desktop launch, permissions, and failure/recovery acceptance steps
are in the tickets and remain unverified until those exercises are implemented.

Both agents use OpenAI through `langchain-openai`. Set `OPENAI_API_KEY` in
your existing `.env` or shell environment. `OPENAI_MODEL` defaults to
`gpt-4.1-nano`, an inexpensive model supporting structured output and tool
calling. Change that setting and restart the server to select another compatible
OpenAI model. Anthropic credentials are no longer used.

Each model response is capped at 2,048 completion tokens, with a 30-second
request timeout and at most one retry. Ingestion uses function calling with
Pydantic validation; chat uses database-backed tools. API calls are billed to
your OpenAI account. See the [official model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-nano).

The digest includes pending reviews due today or earlier. A newly captured
entry's first checkpoint is scheduled for tomorrow and appears once it is due.

## LangSmith tracing

Tracing is optional and disabled by default. To enable it, add these settings
to `.env` (or export them in your shell), then restart the API:

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your-langsmith-api-key
LANGSMITH_PROJECT=spaced-repetition-agent
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Use the endpoint for your LangSmith region (for example,
`https://eu.api.smith.langchain.com` for EU). If your API key is linked to
multiple workspaces, also set `LANGSMITH_WORKSPACE_ID`.

Call `/api/v1/entries/ingest` or `/api/v1/chat`, then open your project in
[LangSmith](https://smith.langchain.com). Look for `sra-ingestion` and
`sra-query`: each trace includes graph nodes and nested LangChain model/tool
calls, including inputs, outputs, timing, and errors. Traces include transcript,
chat, and retrieved study content. Set `LANGSMITH_TRACING=false` and restart
to disable uploads.

The routes use `sra.agents.tracing.trace_agent()` to pass Pydantic's `.env`
settings to LangSmith without changing process environment variables. If you
invoke a graph from a standalone Python script, wrap its invocation too:

```python
from sra.agents.tracing import trace_agent

with trace_agent():
    result = graph.invoke(initial_state)  # your compiled graph and input
```

See the [LangGraph tracing guide](https://docs.langchain.com/langsmith/trace-with-langgraph)
for details. No extra decorators are needed on the existing LangChain tools or models.

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev workflow and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community standards. Work is tracked as tickets in [`tickets/`](tickets/README.md).

## License

[MIT](LICENSE)
