# Build Progress

Working log for the phased build. Update this file whenever a phase's scope changes or a task finishes — it's the source of truth for "what's done, what's next" across sessions, not just the phase list in README.

Build mode: **implementation in progress**. The phase checklists below record the original scaffold; implementation updates track completed functionality and current verification results.

Implementation work is tracked as tickets in [`tickets/`](tickets/README.md) (`SRA-1xx` = Phase 1, `SRA-2xx` = Phase 2, ...) — this file tracks phase/scaffold status, `tickets/` tracks the step-by-step work within a phase. At the user's request, all Phase 2 tests and teaching stubs are prepared up front; the user implements the learning functions and verifies each ticket's complete manual flow.

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

- **Real-audio automation — 2026-10-14:** added `make test-data` (280 passed,
  1 live case deselected) and `make test-live` (1 passed, 7 fixture cases
  deselected). Both replay the spoken fixture through the recorder/controller,
  real Whisper, loopback HTTP, isolated SQLite, scheduling, search/chat/digest,
  and application/engine restart. The live run verified actual OpenAI extraction
  and chat. Added quiet/noisy audio, permission/overflow recovery, corrupt input,
  and native-shortcut failure/recovery coverage. Real silence exposed a Whisper
  hallucination; enabled voice-activity filtering and verified zero submissions
  for the silent fixture. Physical desktop acceptance remains outstanding.

**Status: SRA-201 through SRA-205 are implemented; physical desktop acceptance remains pending.** [SRA-201 through SRA-206](tickets/README.md#board--phase-2-voice-capture) cover the plan's U4/U5 and the technical design's client/server split. The assistant supplies tests and teaching docstrings; implementations proceed one ticket at a time. Each implementation ticket must deliver its manual flow, verify newly created IDs through search, and record a failure/recovery check. Focused completed-ticket checks and Phase 1 regressions must stay green; future exercises intentionally keep the full suite red until implemented. See [tests/README.md](tests/README.md).

- Added unit contracts for isolated configuration, HTTP validation/errors/no retries, lazy local inference, pipeline composition, recorder WAV/lifecycle/cleanup, and queued menu-bar controller behavior.
- Added end-to-end tests through real client/transcription wrappers, FastAPI, graph, repository, SQLite, scheduling, search, and due-date digest. Only external inference and HTTP location are replaced; timeout-after-save, silence, failures, and recovery are included.
- Added an original spoken WAV fixture and opt-in real Whisper integration test. Real model/device/hotkey checks remain pending and do not count as implemented functionality.
- The base server/dev dependency snapshot remains unchanged; optional capture/desktop extras are declared but not installed or locked here.
- **Scaffold verification — 2026-10-13:** added 150 Phase 2 cases (136 unit, 13 end-to-end, 1 opt-in Whisper integration). Full suite: **94 passed, 145 failed, 2 skipped**; every failure is the intended `NotImplementedError`, with no collection/setup errors. The skips are real Whisper opt-in and missing macOS desktop dependencies. Phase 1 independently remains **91 passed** using `pytest -m 'not phase2' -q`. Ruff, Pyright, and whitespace checks pass. No real Whisper/model download, microphone, or hotkey verification was performed.

- **Client configuration update — 2026-10-13:** implemented independent cached desktop settings and HTTP(S) base URL validation. All **24 client configuration tests pass**; SRA-201 remains open for the HTTP wrapper and manual flow. Current full suite: **118 passed, 121 failed, 2 skipped**; all failures are remaining `NotImplementedError` exercises, with no collection/setup errors. Phase 1 remains **91 passed**; Ruff, Pyright, and whitespace checks pass.

- **SRA-201 completion — 2026-10-13:** implemented the single-POST HTTP client, strict response validation, error details and causes, resource cleanup, and a 90-second timeout with explicit uncertainty after interrupted submissions. Added seven regression cases for interrupted connections and invalid timestamps. The HTTP-only `capture` extra installs without audio dependencies; a clean Python 3.13 installation matches `requirements-lock.txt` exactly and passes `pip check`, so existing pins remain unchanged. Documentation now includes the text submission/search flow and cached-setting recovery.
- **SRA-201 verification:** **77 focused tests passed**, **91 Phase 1 regressions passed**, Ruff/Pyright/whitespace checks passed. The clean pinned environment also passes focused and Phase 1 checks. Full suite: **171 passed, 75 failed, 2 skipped**, with every failure confined to the unchanged SRA-202–205 `NotImplementedError` exercises. With the user's approval, real HTTP/OpenAI submissions at `http://127.0.0.1:8765` created **mitosis ID 1** and **Photosynthesis ID 2** in an isolated temporary DB; search found the exact IDs. Both retained the transcript and local-user ownership with six pending reviews. Unused port **53425** produced a clear connection-refused error; restoring the URL permitted ID 2's submission. The temporary server was stopped. Full commands and evidence are recorded in SRA-201.

- **SRA-202 verification — 2026-10-13:** **40 focused configuration/transcription tests**, the opt-in real-Whisper fixture test, and **91 Phase 1 regressions passed**; Ruff and Pyright passed. The cached `base` model transcribed the fixture with `HF_HUB_OFFLINE=1`. A clean Python 3.13 environment installed the pinned capture dependencies and passed `pip check`. PyAV is constrained below 19 because the current `faster-whisper` 1.2.1 release still passes an argument removed in PyAV 19. Full suite: **185 passed, 61 failed, 2 skipped**; failures are confined to SRA-203–205 teaching stubs. The live transcript-to-server flow remains unrecorded because it requires a running model-backed API.

- **SRA-203 implementation — 2026-10-13:** implemented the one-call recording pipeline with configured model selection, exact transcript forwarding, silence short-circuiting, and unchanged typed failures/file ownership. Added saved-WAV usage documentation. **15 focused tests**, **108 completed-ticket tests**, and **91 Phase 1 regressions passed**; Ruff and Pyright passed. The e2e flow verified transcript/ownership persistence, six schedules per entry, search, capture-day exclusion, next-day `1d` digest visibility, input preservation, failures, and recovery. Full suite: **200 passed, 46 failed, 2 skipped**; failures are confined to SRA-204–205 stubs. A real OpenAI-backed submission remains pending explicit approval to transmit the fixture-derived transcript externally.


- **SRA-204 implementation — 2026-10-14:** implemented lazy microphone access, unique owned temporary WAVs, copied PCM buffers, strict one-capture lifecycle, device/file cleanup on failure or cancellation, and Enter-to-start/stop terminal ingestion. CLI reports actual IDs/topics, silence, typed errors, and unchanged timeout uncertainty. Added setup, permissions, search-ID comparison, and cleanup inspection instructions. **38 focused tests**, **146 completed-ticket tests**, and **91 Phase 1 regressions pass**; Ruff/Pyright pass. Full suite: **239 passed, 15 failed, 1 skipped**, with all failures in unchanged SRA-205 stubs and the opt-in Whisper check skipped. Desktop dependencies installed; the command runner reports no audio devices, so real spoken-note/search and physical failure/recovery acceptance remain pending.

- **SRA-205 implementation — 2026-10-14:** implemented the capture controller and lazy macOS adapter, exact global-shortcut/menu dispatch through the UI loop, background processing, saved counts/IDs, error recovery, overlap prevention, and orderly hotkey/worker/WAV cleanup. Replaced the general `pynput` event listener with macOS `RegisterEventHotKey`, so only the configured chord is registered and Accessibility/Input Monitoring access is unnecessary. Added chord parsing, registration/release, shortcut-conflict fallback, adapter wiring, and failure-cleanup tests plus README setup/recovery instructions. **33 menu-bar tests passed; all 271 tests passed**, including real Whisper with cached weights (`SRA_RUN_WHISPER_TESTS=1 HF_HUB_OFFLINE=1`). Ruff and Pyright passed. Real desktop libraries import successfully, but the command runner exposes **zero audio devices**; physical microphone/hotkey/UI acceptance and live search-ID evidence remain pending.

- **SRA-206 automated verification — 2026-10-14:** the default suite passed
  **259 tests** with only the opt-in Whisper case skipped; all **16 e2e tests**
  passed. Enabling real Whisper with cached weights and offline mode produced a
  fully green **260-test** run. Ruff and Pyright passed. The e2e coverage verifies
  exact returned IDs, transcript/ownership persistence, six pending schedules,
  search, next-day digest visibility, failure recovery, single submission, and
  recorder cleanup. Phase 2 remains open because this runner exposes zero audio
  devices, so the required physical microphone, global-hotkey/native-UI,
  live-server, restart, and per-ticket manual evidence could not be recorded.

- [x] [SRA-201](tickets/SRA-201-text-capture-api-client.md) — client config + `src/sra/client/api_client.py`: typed note → API → stored entries → search
- [x] [SRA-202](tickets/SRA-202-local-whisper-transcription.md) — `src/sra/capture/transcription.py`: real WAV → local Whisper transcription boundary (manual server-flow evidence remains environment-dependent)
- [x] [SRA-203](tickets/SRA-203-recording-to-ingestion-pipeline.md) — `src/sra/capture/pipeline.py`: one-call audio ingestion, no-speech/error handling, storage and due-date digest checks (live external submission pending approval)
- [ ] [SRA-204](tickets/SRA-204-microphone-capture.md) — `src/sra/capture/recording.py`: implemented and automated checks pass; real microphone/search acceptance pending
- [ ] [SRA-205](tickets/SRA-205-menubar-hotkey-capture.md) — `src/sra/capture/menubar_app.py`: implemented hotkey/UI around the recorder and pipeline; physical desktop/search acceptance pending
- [ ] [SRA-206](tickets/SRA-206-phase-2-integration-verification.md) — automated regression checks plus real desktop-to-digest verification

Phase 2 retains Phase 1's single local user and server-side OpenAI agents. Only transcript text is submitted; raw audio stays local. Authentication remains Phase 3, and wake-up/TTS/voice quiz remain Phase 5. The scaffold and tests now exist; runtime behavior and manual verification remain incomplete.

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
