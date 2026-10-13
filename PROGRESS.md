# Build Progress

Working log for the phased build. Update this file whenever a phase's scope changes or a task finishes — it's the source of truth for "what's done, what's next" across sessions, not just the phase list in README.

Build mode: **implementation in progress**. The phase checklists below record the original scaffold; implementation updates track completed functionality and current verification results.

Per-module implementation work is tracked as tickets in [`tickets/`](tickets/README.md) (`SRA-1xx` = Phase 1, `SRA-2xx` = Phase 2, ...) — this file tracks phase/scaffold status, `tickets/` tracks the step-by-step implementation work within a phase once its scaffold exists.

## Implementation update — 2026-10-07

- Ingestion and query agents now use OpenAI (`langchain-openai`) with configurable `OPENAI_MODEL`, defaulting to `gpt-4.1-nano`. `OPENAI_API_KEY` replaces the Anthropic credential and is stored as a masked secret in Settings. Each response has a 2,048-token limit, a 30-second request timeout, and one retry.
- OpenAI migration verification: **91 tests passed**, Ruff and Pyright passed. After replenishing API credits, all five live HTTP requests succeeded with `gpt-4.1-nano`: ingestion returned 201 and created Photosynthesis (ID 2) and Mitosis (ID 3); digest, search, and both chat requests returned 200. Search returned exactly Mitosis, chat recalled cell division, and the unknown-topic question correctly reported no records.
- **Phase 1 is functionally complete.** Both new entries retain the original transcript and have six pending checkpoints each. Their first reviews are scheduled for 2026-10-08; a separate request through the real digest route with its date simulated as 2026-10-08 returned both new `1d` reviews. The database dates were not changed. All **3 end-to-end tests** also passed independently.
- Digest behavior is unchanged: tomorrow's `1d` checkpoints are stored at ingestion but only appear in the digest when due. An immediate digest request does not verify new future checkpoints.

### Earlier ingestion implementation checkpoint

- SRA-105 ingestion graph and `POST /api/v1/entries/ingest` are implemented. Valid drafts are persisted with six reviews each; invalid input and model failures return useful HTTP 422 messages.
- Verified graph execution, response fields, ownership, original transcript storage, search visibility, and digest visibility when the first review becomes due. Corrected the ingestion-to-digest test to advance the date by one day, matching the scheduler.
- SRA-105 tests: **33 passed**. Full suite: **52 passed, 6 failed**, all remaining failures in unimplemented SRA-106 chat/query functions. Ruff and Pyright pass.
- Live Claude verification remains pending: no usable Anthropic key is configured. Automated checks use mocked model responses and an isolated database.

---

## Phase 1 — Core loop (single local user, no auth)

Ingestion agent + scheduler + minimal API, exercised via pasted transcript text (no voice capture yet).

- [x] `pyproject.toml`, `.env.example`, `README.md` (phase roadmap)
- [x] `src/sra/config.py` — `Settings` + `get_settings()` [stub]
- [x] `src/sra/db/models.py` — `User` (minimal), `StudyEntry`, `ReviewSchedule` (concrete)
- [x] `src/sra/db/session.py` — engine/session setup (concrete)
- [x] `src/sra/db/scheduler.py` — `compute_review_dates`, `schedule_reviews_for_entry` [stubs]
- [x] `src/sra/db/repository.py` — `create_entry`, `get_due_reviews`, `search_entries`, `mark_review_done` [stubs]
- [x] `src/sra/auth/dependencies.py` — `get_current_user` Phase 1 placeholder (concrete)
- [x] `src/sra/agents/schemas.py` — `StudyEntryDraft` (concrete)
- [x] `src/sra/agents/ingestion_graph.py` — ingestion LangGraph implemented (SRA-105), plus a `_get_chat_model()` seam so tests can fake the LLM
- [x] `src/sra/agents/query_graph.py` — query LangGraph [stubs], same `_get_chat_model()` seam
- [x] `src/sra/api/schemas.py` — request/response DTOs (concrete)
- [x] `src/sra/api/deps.py` — shared dependencies re-exported for routers (concrete)
- [x] `src/sra/api/app.py` — `create_app()`, mounts `api_router` under `/api/v1` [stub]
- [x] `src/sra/api/routers/entries.py` — `POST /entries/ingest` implemented (SRA-105)
- [x] `src/sra/api/routers/digest.py` — `GET /digest` [stub]
- [x] `src/sra/api/routers/search.py` — `GET /search` [stub]
- [x] `src/sra/api/routers/chat.py` — `POST /chat` [stub]
- [x] `alembic/` — schema migrations, wired to `Settings.database_url`; `init_db()` remains for tests/local dev only
- [x] `tests/conftest.py` (session, seeded_user, other_user, client fixtures) + one unit test module per stubbed module above
- [x] `tests/e2e/test_capture_to_digest_e2e.py`
- [x] Install deps in a venv, run `pytest`, fix any wiring errors so every failure is a clean `NotImplementedError`

**Phase 1 status: implemented and functionally verified on 2026-10-07**, including live OpenAI ingestion and Q&A, storage, scheduling, search, and digest visibility when due. See the implementation update above.

**Historical scaffold verification:** `pytest` originally collected 34 tests: 33 failed/errored with a clean `NotImplementedError` (proving every import, fixture, dependency-injection wire, and route registration was correct) and 1 passed trivially (see caveat below). Two real bugs were found and fixed while verifying, not left for the user to trip over:
  - `db/models.py` used naive `datetime.utcnow()` defaults; the installed SQLModel version requires timezone-aware datetimes on write. Fixed with a shared `_utcnow()` helper.
  - `api/app.py` didn't import `init_db`/the routers at module scope, so `create_app()` had nothing importable for tests to reference/monkeypatch. Fixed by importing them at the top of the file.

**Known caveat to revisit once you implement `get_settings()`:** `test_settings_missing_required_key_raises` currently "passes" for the wrong reason — it asserts `pytest.raises(Exception)`, and `NotImplementedError` satisfies that just as well as the real pydantic `ValidationError` would. Once `get_settings()` is implemented, rerun this test specifically and confirm it's still failing for the right reason (a missing required field), not passing by coincidence.

**Not in Phase 1 (by design):** voice capture, Whisper, menu-bar app, real auth, Docker.

---

## Phase 2 — Voice capture

- [ ] Not started. `src/sra/capture/transcription.py` (local Whisper), `src/sra/capture/pipeline.py`, `src/sra/capture/menubar_app.py`, `src/sra/client/api_client.py`.

## Phase 3 — Multi-tenancy, auth, RBAC, rate limiting

- [ ] Not started. Extend `User` model (role, OAuth fields), `src/sra/auth/google_oauth.py`, replace Phase 1's placeholder `get_current_user` with real `require_user`/`require_admin`, `src/sra/api/rate_limit.py`, `src/sra/client/auth_client.py`.

## Phase 4 — Daily MCQ quiz (web)

- [ ] Not started. `QuizSet`/`QuizQuestion`/`QuizAttempt`/`QuizAnswer` models, `src/sra/agents/quiz_graph.py`, `src/sra/jobs/quiz_pregeneration.py`, `src/sra/api/routers/quiz.py`, `dashboard/static/quiz.js`.

## Phase 5 — Scheduled wake-up & voice quiz

- [ ] Not started. `preferred_review_time`/`timezone` on `User`, `src/sra/jobs/digest_scheduler.py`, `src/sra/capture/wakeup.py`, `src/sra/capture/tts.py`, `src/sra/agents/quiz_conversation_graph.py`, `src/sra/capture/voice_quiz_client.py`.

## Phase 6 — Open-source packaging

- [ ] Not started. `Dockerfile`, `docker-compose.yml`, `DEMO_MODE` + `src/sra/jobs/demo_reset.py`, final README self-host instructions.

---

## Reference docs

- Implementation plan: `docs/plans/2026-09-25-1616-feat-study-agent-scaffold-plan.md`
- HLD / LLD / DB schema / API contract: `docs/design/technical-design.md`
