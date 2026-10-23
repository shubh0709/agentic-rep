# Build Progress

The single source of truth for **phase status — what is done and what is next**. Per-ticket scope and verification live in each ticket file; see [`tickets/README.md`](tickets/README.md) for the board, [`tests/README.md`](tests/README.md) for commands, [`tickets/self_heal/SH-REPORT.md`](tickets/self_heal/SH-REPORT.md) for self-heal runs.

## Status at a glance

| Phase | Scope | Status | Key notes |
|---|---|---|---|
| 1 — Core loop | Ingestion, scheduler, API | Implemented, verified 2026-10-07 | 91 tests; live OpenAI flows succeeded |
| 2 — Voice capture | Local Whisper + menu-bar hotkey | SRA-201–205 done; **SRA-206 open** | 260-test green run incl. real Whisper; zero audio devices |
| 3 — Auth, RBAC, rate limiting | Google OAuth, user isolation, limits | SRA-301/302/304/306 done; **303, 305 open** | Real-Google + Keychain acceptance pending |
| 4 — Daily MCQ quiz (web) | Quiz tables, MCQ agent, pregen, dashboard | Closed 2026-10-14 | Left 5 SRA-404/405/406 limits |
| 5 — Wake-up & voice quiz | Preferences, APScheduler, TTS, voice quiz | Closed 2026-10-14 | `-m phase5` green: 212 passed, 1049 deselected |
| 6 — Open-source packaging | Docker/compose, demo mode, self-host docs | Closed 2026-10-14 | `POST /admin/demo/reset` gated on `DEMO_MODE` |
| 7 — Study dashboard | Entries, search, upcoming reviews | SRA-701 done 2026-10-15 | `dev_mode` + `POST /me/dev/pregenerate-now` |
| 8 — AI interview mode | Schema, planner, graph, routes, clients | SRA-801–808 done 2026-10-15; **809 not done** | SRA-802 circular import blocks one e2e |
| 9 — Topic authoring + quality | `reference_materials`, `POST /topics`, rubric | SRA-901, 906–909 done; **902–905 open** | Green after SRA-906: 1232 passed, 29 skipped |

## Changelog

- **2026-10-17** — [SRA-906](tickets/SRA-906-dashboard-ui-recovery.md) — dashboard UI recovery; suite green at **1232 passed, 29 skipped**, Ruff/Pyright clean.
- **2026-10-16** — Self-heal ([report](tickets/self_heal/SH-REPORT.md)) — four defects + route-audit gap closed (SH-002…005; `d6f080a`, `6938f95`, `2d33a4e`); 962→**970 passed**, 25→**17 failed**, Pyright 16→4.
- **2026-10-16** — [SRA-901](tickets/SRA-901-reference-materials-and-ingestion-route.md) — reference-materials schema, fetcher, extractor, ingestion route; **84/84** tests pass.
- **2026-10-15** — [SRA-808](tickets/SRA-808-interview-voice-client-and-tts.md)/[807](tickets/SRA-807-interview-dashboard-ui.md) — interview voice client + menu item, browser dashboard with `?attempt_id=` resume; 82 + 16 unit, 1 e2e, 7 unit + 2 e2e.
- **2026-10-15** — [SRA-805](tickets/SRA-805-interview-router-endpoints.md)/[806](tickets/SRA-806-interview-api-schemas.md)/[804](tickets/SRA-804-interview-repository-helpers.md) — interview routes (commit `d6f080a`), DTOs widening `StartAttemptRequest.mode` to `interview`, repo helpers with cascade extended to interview tables; **33 + 96 + 34 passed**.
- **2026-10-15** — [SRA-803](tickets/SRA-803-interview-conversation-graph.md) — interview graph, `max_turns=20`, one LLM call per `answer`; **13 passed**.
- **2026-10-15** — [SRA-802](tickets/SRA-802-interview-planner-and-pregen.md)/[801](tickets/SRA-801-interview-schema-and-migration.md) — planner/pre-gen, interview models, `cursor_on_plan`, migration `e5f6a7b8c9d0`; **58 passed**. Left the circular import blocking one Phase 4 e2e.
- **2026-10-15** — [SRA-306](tickets/SRA-306-automatic-application-credential-refresh.md) — refresh credential, `jti` rotation, reuse revocation, `POST /auth/refresh`; **27 new tests**; baseline 797 passed/14 skipped/23 failed.
- **2026-10-15** — [SRA-701](tickets/SRA-701-study-dashboard.md) — `dev_mode` + `/me/dev/pregenerate-now`; live run made two quiz sets (10q × 4 options) for entries 2 and 3.
- **2026-10-14** — [SRA-305](tickets/SRA-305-phase-3-integration-verification.md) — Phase 3 verification: **386 passed, 9 skips**, 112 Phase 3 tests; `make test-data` **394 passed, 1 deselected**.
- **2026-10-14** — [SRA-304](tickets/SRA-304-api-rate-limiting.md) — token buckets, pre-work `429` + `Retry-After`, health exemption; **34 focused + 363 full-suite**; loopback `201, 201, 429` then `200`.
- **2026-10-14** — [SRA-303](tickets/SRA-303-desktop-oauth-authenticated-client.md)/[302](tickets/SRA-302-google-oauth-rbac-isolation.md)/[301](tickets/SRA-301-oauth-user-schema-migration.md) — Google login, credentials, one-use completion codes, `/auth/me`, per-origin Keychain, bearer capture, two-user isolation, RBAC, role constraints, rehearsed upgrade/downgrade; real-Google + Keychain evidence pending.
- **2026-10-14** — [SRA-605](tickets/SRA-605-phase-6-integration-verification.md) — Phase 6 close: **106 passed**; `demo_user` fixture, explicit `QuizAnswer` cascade, `is_demo_user` server default.
- **2026-10-14** — [SRA-604](tickets/SRA-604-self-host-documentation.md)/[603](tickets/SRA-603-demo-reset-job-and-admin-endpoint.md) — demo callout, self-hosting guide, env-var table, `.env.example`; demo-reset job, `POST /admin/demo/reset`; 17 doc + **24** tests.
- **2026-10-14** — [SRA-602](tickets/SRA-602-dockerfile-and-docker-compose.md)/[601](tickets/SRA-601-demo-mode-settings-and-user-marker.md) — Dockerfile/entrypoint/compose/`.dockerignore`; demo settings, `is_demo_user` + migration `d4e5f6a7b8c9`, cascade/retention; **23 + 44 tests**.
- **2026-10-14** — [SRA-509](tickets/SRA-509-phase-5-integration-verification.md) — Phase 5 close: **196 passed, 73 deselected**; `not phase5` **479 passed / 14 skipped / 13 failed** (pre-existing SRA-406).
- **2026-10-14** — [SRA-507](tickets/SRA-507-wakeup-delivery-loop.md)/[508](tickets/SRA-508-voice-quiz-client-driver.md) — wake-up loop, interruptible `stop_event.wait`; `run_voice_quiz` + Enter-to-start/stop recording; 11 tests each.
- **2026-10-14** — [SRA-505](tickets/SRA-505-voice-quiz-router-endpoint.md)/[506](tickets/SRA-506-client-api-extensions.md) — voice-turn route, typed 422/409/400, client extensions; 33 + 75 focused.
- **2026-10-14** — [SRA-502](tickets/SRA-502-digest-ready-and-scheduler-wiring.md)/[503](tickets/SRA-503-local-tts-abstraction.md)/[504](tickets/SRA-504-voice-quiz-conversation-graph.md) — APScheduler digest job + `/digest/ready`, `say` TTS seam, voice-quiz graph + migration `c3d4e5f6a7b8`; 24 + 11 + 13 focused.
- **2026-10-14** — [SRA-406](tickets/SRA-406-phase-4-integration-verification.md)/[405](tickets/SRA-405-dashboard-quiz-ui.md) — Phase 4 close: Pyright **54 → 0 errors**, route audit widened to all six quiz routes; `quiz.js`/`quiz.html` as plain ES modules, 4/4 e2e.
- **2026-10-14** — [SRA-401](tickets/SRA-401-quiz-schema-and-migration.md)/[404](tickets/SRA-404-quiz-api-endpoints.md)/[402](tickets/SRA-402-mcq-generation-agent-and-repository.md)/[403](tickets/SRA-403-quiz-pregeneration-job.md) — four quiz tables + migration `a1b2c3d4e5f6` off `8cb1b420d9d2`, six endpoints (voice-mode typed `400`), MCQ generation graph, `pregenerate_quizzes_for_day`/`_for_user`; 19/20 e2e, 25 + 12 focused.
- **2026-10-14** — [SRA-205](tickets/SRA-205-menubar-hotkey-capture.md)/[206](tickets/SRA-206-phase-2-integration-verification.md) — capture controller + macOS `RegisterEventHotKey` adapter replacing the `pynput` listener; all **271 tests** pass, then **259**/**260 green** with real Whisper. Phase 2 open: **zero audio devices**.
- **2026-10-14** — [SRA-204](tickets/SRA-204-microphone-capture.md)/[203](tickets/SRA-203-recording-to-ingestion-pipeline.md) — lazy mic access, owned temp WAVs, Enter-to-start/stop CLI, one-call pipeline with silence short-circuit; **38 + 15 focused**.
- **2026-10-14** — [SRA-201](tickets/SRA-201-text-capture-api-client.md)/[202](tickets/SRA-202-local-whisper-transcription.md) — single-POST HTTP client, 90s timeout with explicit uncertainty; live submissions created **mitosis ID 1** and **Photosynthesis ID 2**; **77 focused + 91 Phase 1**; Whisper boundary **40 focused** (PyAV held <19 for `faster-whisper` 1.2.1).
- **2026-10-14** — Phase 2 real-audio automation — `make test-data` **280 passed, 1 deselected**, `make test-live` **1 passed, 7 deselected**; voice-activity filtering stops the silent-fixture hallucination.
- **2026-10-13** — Phase 2 scaffold — 150 cases; **94 passed / 145 failed / 2 skipped**, all intended `NotImplementedError`; Phase 1 **91 passed**; client config **24** green.
- **2026-10-07** — Phase 1 OpenAI migration — `OPENAI_MODEL` default `gpt-4.1-nano`, 2,048-token cap, 30s timeout, 1 retry; **91 tests passed**; five live requests OK (ingest `201` → Photosynthesis ID 2, Mitosis ID 3).
- **2026-10-07** — [SRA-105](tickets/SRA-105-ingestion-agent-and-route.md) — ingestion graph + `POST /entries/ingest`; **33 passed**, suite **52/6** (SRA-106 stubs).
- **Phase 1 scaffold** — `pytest` collected **34 tests**: 33 clean `NotImplementedError` + 1 trivial pass. Two real bugs fixed: naive `datetime.utcnow()` defaults in `db/models.py` (SQLModel needs tz-aware writes; `_utcnow()` added) and `api/app.py` omitting the `init_db`/router imports at module scope, leaving `create_app()` nothing to monkeypatch.

## Known limitations

- **Physical desktop acceptance unrecorded** — microphone, global hotkey, menu-bar UI; zero audio devices on the runner ([SRA-204](tickets/SRA-204-microphone-capture.md), [SRA-205](tickets/SRA-205-menubar-hotkey-capture.md), [SRA-206](tickets/SRA-206-phase-2-integration-verification.md)).
- **Live Google OAuth + native Keychain acceptance pending** — two real accounts, desktop sign-in ([SRA-302](tickets/SRA-302-google-oauth-rbac-isolation.md), [SRA-303](tickets/SRA-303-desktop-oauth-authenticated-client.md), [SRA-305](tickets/SRA-305-phase-3-integration-verification.md)).
- **Live `say`/OpenAI desktop acceptance still pending** — the manual voice-quiz run and live loopback evidence belong to [SRA-508](tickets/SRA-508-voice-quiz-client-driver.md) (`implemented`, manual verification still unchecked); [SRA-509](tickets/SRA-509-phase-5-integration-verification.md) is `verified` with Phase 5 closed 2026-10-14 and covers the substituted-audio path only.
- **Phase 8 incomplete** — SRA-809 has not run ([SRA-809](tickets/SRA-809-phase-8-integration-verification.md)).
- **Phase 9 in progress** — SRA-902/903/904/905 open ([Phase 9 plan](docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md)).
- **Pre-existing SRA-802 circular import** — `repository.py → interview_planner.py → quiz_graph.py → repository.py` blocks `tests/e2e/test_phase4_web_quiz_e2e.py` ([SRA-803](tickets/SRA-803-interview-conversation-graph.md)).
- **Dashboard JS tests need test-only hooks** — Node driver needed a `setImmediate` DOM dump, `auto_advance`, and an `appendChild` shim ([SRA-405](tickets/SRA-405-dashboard-quiz-ui.md), [SRA-807](tickets/SRA-807-interview-dashboard-ui.md)).
- **Default-suite state** — green as of 2026-10-17; the 17 failures classified on 2026-10-16 were repaired by Phase 9.

## Reference

- [`tickets/README.md`](tickets/README.md) — phase→ticket board
- [`tests/README.md`](tests/README.md) — test commands, scaffold failures, evidence rules
- [`docs/design/technical-design.md`](docs/design/technical-design.md) — architecture, schema, API contract
- [`docs/plans/`](docs/plans/) — historical feature plans
- [`tickets/self_heal/SH-REPORT.md`](tickets/self_heal/SH-REPORT.md) — self-heal run results
- [`README.md`](README.md) — install, quickstart, env vars, demo
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — dev checks, Alembic
- [`guides/`](guides/) — commits, ticket creation, TDD, self-heal