# Tickets

Work items for building the scaffold, one small capability at a time. Each
`SRA-NNN-*.md` file is one ticket: what to build, which files matter, how to
test it in isolation, and its Definition of Done. Numbering follows the phase,
so `SRA-1xx` is Phase 1, `SRA-2xx` is Phase 2, and so on. Per-ticket detail
lives in the ticket file itself; this page is only the index. See
[`guides/ticket_creation.md`](../guides/ticket_creation.md) to author or split a
ticket.

## Phase status

| Phase | Scope | Tickets |
|---|---|---|
| 1 — Core loop | [Ingestion agent + scheduler + minimal API, pasted transcripts, no auth](../README.md#build-phases) | SRA-101 – SRA-107 |
| 2 — Voice capture | [Local Whisper + macOS menu-bar hotkey client](../README.md#build-phases) | SRA-201 – SRA-206 |
| 3 — Multi-tenancy, auth, RBAC, rate limiting | [Google OAuth, two-user isolation, token buckets](../README.md#build-phases) | SRA-301 – SRA-306 |
| 4 — Daily MCQ quiz (web) | [Quiz tables, generation agent, pregen job, six endpoints, dashboard UI](../README.md#build-phases) | SRA-401 – SRA-406 |
| 5 — Scheduled wake-up & voice quiz | [Preferences, APScheduler, `say` TTS, voice-quiz conversation](../README.md#build-phases) | SRA-501 – SRA-509 |
| 6 — Open-source packaging | [Dockerfile/compose profiles, demo mode + reset job, self-host docs](../README.md#build-phases) | SRA-601 – SRA-605 |
| 7 — Study dashboard | [Entries, search, upcoming-reviews timeline on `/dashboard`](../README.md#build-phases) | SRA-701 |
| 8 — Interactive AI interview | [Schema, planner, conversation graph, repo helpers, routes, DTOs, browser + voice clients](../README.md#build-phases) | SRA-801 – SRA-809 |
| 9 — Topic authoring + question quality | [`reference_materials`, `POST /topics`, six-section rubric + verifier](../README.md#build-phases) | SRA-901 – SRA-909 |

Phase status is owned by [`PROGRESS.md`](../PROGRESS.md), the source of truth for what is done
and what is next; each ticket file's own `Status:` field holds the finer detail.

## Phase 1 — Core loop

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-101](SRA-101-configuration.md) | Application configuration | `get_settings()` that every other module reads through |
| [SRA-102](SRA-102-spaced-repetition-scheduler.md) | Spaced-repetition scheduler | The six 1d/3d/7d/1mo/3mo/6mo checkpoint dates |
| [SRA-103](SRA-103-data-repository-layer.md) | Data repository layer | User-scoped entry and review access, the only direct DB layer |
| [SRA-104](SRA-104-api-skeleton.md) | API skeleton: app factory + digest + search | `create_app()` plus the two LLM-free read routes |
| [SRA-105](SRA-105-ingestion-agent-and-route.md) | Ingestion LangGraph agent + ingest route | Transcript becomes persisted, scheduled study entries |
| [SRA-106](SRA-106-query-agent-and-route.md) | Query LangGraph agent + chat route | Natural-language answers grounded in study history |
| [SRA-107](SRA-107-phase-1-integration-verification.md) | Phase 1 integration verification | Proves 101–106 work as one continuous flow |

## Phase 2 — Voice capture

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-201](SRA-201-text-capture-api-client.md) | Text capture through the HTTP client | Submit a typed note, retrieve those exact new entries |
| [SRA-202](SRA-202-local-whisper-transcription.md) | Local Whisper transcription | An audio file becomes spoken text on the local machine |
| [SRA-203](SRA-203-recording-to-ingestion-pipeline.md) | Recording-to-ingestion pipeline | One call that transcribes, submits, handles no-speech |
| [SRA-204](SRA-204-microphone-capture.md) | Microphone capture | Live microphone to finalized WAV, with a terminal entry point |
| [SRA-205](SRA-205-menubar-hotkey-capture.md) | macOS menu-bar hotkey capture | Global hotkey toggles recording and reports the result |
| [SRA-206](SRA-206-phase-2-integration-verification.md) | Phase 2 integration verification | Hotkey to due-date digest as one verified desktop flow |

## Phase 3 — Multi-tenancy, auth, RBAC, rate limiting

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-301](SRA-301-oauth-user-schema-migration.md) | OAuth identity schema and safe legacy migration | Identity and role columns, existing rows preserved |
| [SRA-302](SRA-302-google-oauth-rbac-isolation.md) | Google OAuth, authenticated isolation, and RBAC | Browser login, issued credentials, `require_user`/`require_admin` |
| [SRA-303](SRA-303-desktop-oauth-authenticated-client.md) | Desktop OAuth and authenticated capture client | Loopback code exchange, Keychain storage, bearer capture |
| [SRA-304](SRA-304-api-rate-limiting.md) | Per-user and per-IP API rate limiting | Token buckets returning `429` with `Retry-After` |
| [SRA-305](SRA-305-phase-3-integration-verification.md) | Phase 3 integration verification | Two real accounts prove isolation, RBAC, and limits |
| [SRA-306](SRA-306-automatic-application-credential-refresh.md) | Automatic application credential refresh | Rotating refresh token so an active session never logs out |

## Phase 4 — Daily MCQ quiz (web)

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-401](SRA-401-quiz-schema-and-migration.md) | Quiz schema and safe migration | Four quiz tables: sets, questions, attempts, answers |
| [SRA-402](SRA-402-mcq-generation-agent-and-repository.md) | MCQ generation agent and quiz repository | Persisted MCQ questions per entry per generated day |
| [SRA-403](SRA-403-quiz-pregeneration-job.md) | Quiz pregeneration job | Idempotent daily batch over every user's due entries |
| [SRA-404](SRA-404-quiz-api-endpoints.md) | Quiz API endpoints (web) | Today's list plus the whole attempt lifecycle |
| [SRA-405](SRA-405-dashboard-quiz-ui.md) | Dashboard quiz UI | One-question-at-a-time web quiz with final score |
| [SRA-406](SRA-406-phase-4-integration-verification.md) | Phase 4 integration verification | Migration replay, idempotency, isolation, web loop |

## Phase 5 — Scheduled wake-up & voice quiz

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-501](SRA-501-user-preferences-and-scheduler-columns.md) | User preferences and scheduler columns | Five preference columns read through `GET/PUT /me/preferences` |
| [SRA-502](SRA-502-digest-ready-and-scheduler-wiring.md) | Digest ready endpoint and APScheduler wiring | `GET /digest/ready` plus lifespan-owned cron and interval jobs |
| [SRA-503](SRA-503-local-tts-abstraction.md) | Local TTS abstraction | `speak(text)` over macOS `say`, with a test seam |
| [SRA-504](SRA-504-voice-quiz-conversation-graph.md) | Voice quiz conversation graph | Spoken answer mapped to an option, answer row persisted |
| [SRA-505](SRA-505-voice-quiz-router-endpoint.md) | Voice quiz router endpoint | Replaces the Phase 4 voice-mode `400` with real routes |
| [SRA-506](SRA-506-client-api-extensions.md) | Client API extensions for wake-up and voice quiz | Four desktop client methods for digest and voice quiz |
| [SRA-507](SRA-507-wakeup-delivery-loop.md) | Wake-up delivery loop | Poll, speak, sleep, exit cleanly on the stop event |
| [SRA-508](SRA-508-voice-quiz-client-driver.md) | Voice quiz client driver | Recorder + transcriber + client + TTS in one function |
| [SRA-509](SRA-509-phase-5-integration-verification.md) | Phase 5 integration verification | Scheduler, wake-up, and voice-quiz loops proven together |

## Phase 6 — Open-source packaging

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-601](SRA-601-demo-mode-settings-and-user-marker.md) | Demo-mode settings and per-user marker column | `demo_mode`, retention days, and `User.is_demo_user` |
| [SRA-602](SRA-602-dockerfile-and-docker-compose.md) | Dockerfile and docker-compose.yml | Default, `postgres`, and `demo` compose profiles |
| [SRA-603](SRA-603-demo-reset-job-and-admin-endpoint.md) | Demo reset job and admin endpoint | Hourly demo wipe plus an admin-triggered reset |
| [SRA-604](SRA-604-self-host-documentation.md) | Self-host documentation | README quickstart, demo mode, OAuth setup, env-var table |
| [SRA-605](SRA-605-phase-6-integration-verification.md) | Phase 6 integration verification | Migration round-trip, demo lifecycle, route audit, clean lint |

## Phase 7 — Study dashboard

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-701](SRA-701-study-dashboard.md) | Study dashboard: entries, search, upcoming reviews | One page listing entries, filtering, and the review timeline |

## Phase 8 — Interactive AI interview mode

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-801](SRA-801-interview-schema-and-migration.md) | Interview schema and safe migration | `interview_plans`, `interview_turns`, and the plan cursor |
| [SRA-802](SRA-802-interview-planner-and-pregen.md) | Interview planner agent and pregeneration | One structured outline per quiz set, idempotent per day |
| [SRA-803](SRA-803-interview-conversation-graph.md) | Interview conversation graph (per-turn agent) | Per-turn grading, next action, and persisted transcript rows |
| [SRA-804](SRA-804-interview-repository-helpers.md) | Interview repository helpers | Typed plan and turn reads/writes for the new tables |
| [SRA-805](SRA-805-interview-router-endpoints.md) | Interview router endpoints | Start, interview-turn, and full-transcript resume routes |
| [SRA-806](SRA-806-interview-api-schemas.md) | Interview API schemas (DTOs) | Wire types plus the `interview` attempt mode |
| [SRA-807](SRA-807-interview-dashboard-ui.md) | Interview dashboard UI | Chat thread with score chips, actions, and summary panel |
| [SRA-808](SRA-808-interview-voice-client-and-tts.md) | Interview voice client and TTS integration | Spoken interview driven through the same three routes |
| [SRA-809](SRA-809-phase-8-integration-verification.md) | Phase 8 integration verification | Closes the phase from a green automated baseline |

## Phase 9 — Topic authoring with reference materials + question-quality uplift

| Ticket | Title | What it adds |
|---|---|---|
| [SRA-901](SRA-901-reference-materials-and-ingestion-route.md) | Reference materials schema, URL fetcher, doc extractor, ingestion route | `POST /api/v1/topics` with stored references per entry |
| [SRA-902](SRA-902-dashboard-create-topic-ui.md) | Dashboard "Create topic with references" UI | Modal, reference rows, generate-now, inline quiz/interview cards |
| [SRA-903](SRA-903-quiz-graph-quality-uplift.md) | MCQ prompt uplift | Bloom levels, distractor rubric, source-grounded verify pass |
| [SRA-904](SRA-904-interview-quality-uplift.md) | Interview planner + per-turn grader prompt uplift | Source quotes and rubrics anchor the planner and the grader |
| [SRA-905](SRA-905-phase9-integration-verification.md) | Phase 9 integration verification + README | Closes the topic → quiz → interview lifecycle |
| [SRA-906](SRA-906-dashboard-ui-recovery.md) | Dashboard UI recovery and topic-generation completion | Repairs the broken `/dashboard` module evaluation |
| [SRA-907](SRA-907-startup-quiz-pregeneration-catch-up.md) | Catch up quiz pre-generation when the API starts | Runs the pregen job once at scheduler startup |
| [SRA-908](SRA-908-transcript-grounded-quiz-verification.md) | Verify quizzes generated from captured transcripts | Transcript backs verification when no references exist |
| [SRA-909](SRA-909-collapse-repeated-transcription-spans.md) | Collapse repeated spans in local transcription | Drops Whisper's repeated adjacent spans from the transcript |

## Self-heal tickets

Produced by the [`guides/self_heal.md`](../guides/self_heal.md) pass against
`master` on 2026-10-16; the run's overall results live in
[SH-REPORT](self_heal/SH-REPORT.md).

| Ticket | Title | What it adds |
|---|---|---|
| [SH-001](self_heal/SH-001-coverage-matrix.md) | Coverage matrix and pass-1 failure classification | Every test mapped to a ticket or a known limitation |
| [SH-002](self_heal/SH-002-route-audit-refresh.md) | `/api/v1/auth/refresh` mounted without an audit policy entry | Registers the refresh route in the route-policy audit |
| [SH-003](self_heal/SH-003-scheduler-test-time-bomb.md) | Digest-scheduler tests coupled to the wall clock | Tests control their own `scheduled_date` fixture value |
| [SH-004](self_heal/SH-004-dev-py-pregensummary-fields.md) | Dev pregen route discards `interview_plans_*` counts | The summary matches its declared shape again |
| [SH-005](self_heal/SH-005-memory-store-save-pair.md) | `MemoryCredentialStore` test fake lacks `save_pair` | The fake satisfies the credential-store protocol |
| [SH-006](self_heal/SH-006-dashboard-empty-upcoming-count.md) | Empty dashboard leaves upcoming count unresolved | Resolves the summary tile to zero after an empty response |
| [SH-007](self_heal/SH-007-dashboard-upcoming-window-label.md) | Upcoming summary label ignores selected window | Keeps the summary label aligned with the review filter |
| [SH-008](self_heal/SH-008-mobile-dashboard-horizontal-overflow.md) | Mobile dashboard overflows horizontally | Wraps authenticated navigation and controls at phone width |
| [SH-009](self_heal/SH-009-create-topic-modal-focus-escape.md) | Create-topic modal lets keyboard focus escape | Traps Tab navigation inside the open dialog |
| [SH-010](self_heal/SH-010-voice-capture-stale-listening-status.md) | Voice capture stays “Listening” after stop | Synchronizes recorder label, pressed state, and live status |
| [SH-011](self_heal/SH-011-interview-buttons-not-wired.md) | Interview action buttons do nothing | Connects Send, Next, and End to their API actions |
| [SH-012](self_heal/SH-012-topic-create-leaves-dashboard-stats-stale.md) | Topic creation leaves dashboard summaries stale | Refreshes entry and review summaries after creation |
| [SH-013](self_heal/SH-013-interview-summary-text-hidden.md) | Completed interview hides its summary text | Renders both turn-string and detail-object summaries |
| [SH-014](self_heal/SH-014-playwright-harness-not-installable.md) | Playwright harness is not installable from dev dependencies | Declares, locks, and documents the browser harness |
| [SH-015](self_heal/SH-015-missing-favicon-console-error.md) | Every first page load logs a favicon 404 | Serves an explicit icon and keeps the landing console clean |
| [SH-016](self_heal/SH-016-static-assets-stale-after-deploy.md) | Existing browsers reuse stale assets after deploy | Versions shared assets and page modules across releases |
| [SH-REPORT](self_heal/SH-REPORT.md) | Application self-heal completion report | Defects closed and failures still outstanding |

## Conventions

- **Ticket IDs** follow the phase: `SRA-1xx` = Phase 1, `SRA-2xx` = Phase 2,
  and so on. Self-heal findings use `SH-0NN` under `self_heal/`.
- **What a ticket contains** is fixed by the
  [reusable ticket outline](../guides/ticket_creation.md#reusable-ticket-outline).
- **Phase status** — what is done and what is next — belongs to
  [`PROGRESS.md`](../PROGRESS.md). Update it when a ticket closes.
- **Test commands, intentional scaffold failures, and the manual-evidence rule**
  belong to [`tests/README.md`](../tests/README.md); each ticket's *How to test
  this ticket alone* section holds its own focused command.
- **Historical plan documents** are archived under
  [`docs/plans/`](../docs/plans/) and are read-only history.

## Reference

- [`../PROGRESS.md`](../PROGRESS.md) — phase status, what's done, what's next
- [`../docs/design/technical-design.md`](../docs/design/technical-design.md) —
  architecture, DB schema, API contract, module map, flow diagrams
- [`../tests/README.md`](../tests/README.md) — test commands, scaffold
  failures, manual-evidence rules
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — dev setup, lint/type/test
  commands, Alembic workflow
- [`../guides/`](../guides/) — ticket authoring, TDD workflow, commits, self-heal
- [`../README.md`](../README.md) — install, quickstart, env vars, demo,
  self-hosting
- [`../docs/plans/`](../docs/plans/) — archived scaffold and Phase 9 plans
- [`self_heal/SH-REPORT.md`](self_heal/SH-REPORT.md) — self-heal run results
