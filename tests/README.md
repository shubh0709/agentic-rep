# Test-first learning guide

*Owns every test command, the pytest markers, the opt-in and real-hardware suites, the manual-evidence rule, and what each layer proves. Lint, type checking, and the migration workflow live in [`CONTRIBUTING.md`](../CONTRIBUTING.md).*

A ticket's contract becomes a failing test before it becomes an implementation. Default tests are offline and hermetic — no Google, no browser, no Keychain, no microphone, no real clock, no model weights.

## The default suite

```bash
source .venv/bin/activate
pytest -q                       # everything that must pass before a PR merges
pytest -m 'not phase2' -q       # Phase 1 regressions while working Phase 2
```

`pip install -e ".[dev]"` is enough: no weights are downloaded and no device is opened. CI runs `pytest` on every push and PR; see [`CONTRIBUTING.md`](../CONTRIBUTING.md) for lint, type checking, and CI.

## Pytest markers

Markers are declared in `pyproject.toml`. Run `pytest --collect-only -q -m <name>` to see what a marker currently selects.

| Marker | Selects |
|---|---|
| `phase2` | Voice-capture TDD contracts (client, transcription, pipeline, recorder, menu-bar). |
| `phase3` | Authentication, tenant isolation, desktop credentials, rate limiting. |
| `phase4` | Daily MCQ quiz: schema, agent, pregeneration job, API, dashboard, verification. |
| `phase5` | Scheduled wake-up and voice quiz: preferences, TTS, voice graph, router, client, wakeup loop. |
| `phase6` | Open-source packaging: settings, Docker, compose, demo reset, self-host docs. Declared but applied to no test — `-m phase6` selects nothing; those tests run under `pytest -q`. |
| `phase7` | Study dashboard: entries, search, upcoming reviews. |
| `phase8` | Interactive AI interview mode: schema, planner, graph, repository, routes, dashboard, voice client. |
| `phase9` | Topic authoring with reference materials and question-quality uplift. |
| `whisper` | Opt-in real local Whisper inference. |
| `desktop` | Requires macOS and installed desktop dependencies. |
| `live_openai` | Explicit opt-in calls to the real OpenAI service. |

The idiom is `pytest -m 'not phase<N>' -q` while a new contract is red, then `pytest -m phase<N> -q` to check that phase alone.

## Ticket commands

Phase 2 voice capture:

| Ticket | Focused checks |
|---|---|
| SRA-201 | `pytest tests/unit/test_client_config.py tests/unit/test_api_client.py tests/e2e/test_client_ingestion_e2e.py -v` |
| SRA-202 | `pytest tests/unit/test_transcription.py -v` |
| SRA-203 | `pytest tests/unit/test_capture_pipeline.py tests/e2e/test_voice_capture_to_digest_e2e.py -v` |
| SRA-204 | `pytest tests/unit/test_recording.py tests/e2e/test_terminal_capture_e2e.py -v` |
| SRA-205 | `pytest tests/unit/test_menubar_app.py -v` |
| SRA-206 | `pytest -q`, `ruff check .`, `pyright`, plus real Whisper and desktop acceptance |

Phase 3 authentication and rate limiting:

```bash
pytest tests/unit/test_user_model.py -v
pytest tests/unit/test_auth.py -v
pytest tests/integration/test_authenticated_routes.py -v
pytest tests/unit/test_auth_client.py tests/unit/test_api_client.py -m phase3 -v
pytest tests/unit/test_rate_limit.py -v
pytest tests/integration/test_rate_limited_routes.py -v
pytest tests/e2e/test_authenticated_capture_e2e.py -v
pytest tests/integration/test_route_policy_audit.py -v
```

That phase's red-to-green order was: user model plus a rehearsed migration against the real Phase 2 revision (SRA-301); provider verification, identity upsert, application credentials, completion codes, protected route/RBAC integration (SRA-302); desktop loopback and secure storage (SRA-303); deterministic synchronized token buckets wired ahead of route side effects (SRA-304). The rate-limit test rules — fake clock, fresh app state, `get_settings.cache_clear()` before constructing the app — are in [`CONTRIBUTING.md`](../CONTRIBUTING.md#writing-rate-limit-tests).

Later phases keep focused commands in their own tickets; see [`tickets/README.md`](../tickets/README.md) for the per-ticket index.

## Red → green → refactor

1. Read the failing assertion and the relevant function's docstring.
2. Implement enough behavior to satisfy that contract, then rerun the focused test.
3. Run all unit tests for that ticket, then its end-to-end tests.
4. Refactor with those checks green, and rerun the phase regression suite.
5. Complete the ticket's real-component manual checks and record the evidence.

## Intentional scaffold failures

A teaching scaffold may ship tests that fail on purpose. There are currently **no** expected-failure markers and no tests that pass merely because a stub raises `NotImplementedError`. Keeping that true:

- A scaffold test must **collect** successfully and fail at the named learning stub or behavior assertion — never because an import, dependency, browser, device, or network call was unavailable.
- Do not hide unfinished behavior with a new skip, an `xfail`, or an assertion expecting `NotImplementedError`.
- The only skips in the default suite are the opt-in/environment ones: `tests/integration/test_transcription_whisper.py` (`SRA_RUN_WHISPER_TESTS=1`), `tests/integration/test_audio_flow.py` (`make test-data` / `make test-live`), and `tests/integration/test_dockerfile.py` (`docker` on `PATH`).
- When a stub is implemented, its tests must go green and stay green. A platform skip never counts as a verified feature.

## Opt-in real-component checks

These need an extra, a cached model, credentials, or real hardware. They are skipped by default and never implied by a green default suite.

| Goal | Setup | Command |
|---|---|---|
| Local Whisper transcription | `pip install -e '.[dev,capture]'`, cached weights | `SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v` |
| Real fixture audio, no OpenAI | `capture` extra, cached `base` model | `make test-data` |
| Real OpenAI extraction and chat | `capture` extra, `OPENAI_API_KEY` | `make test-live` |
| Desktop recording and menu-bar | macOS, `pip install -e '.[dev,desktop]'` | manual acceptance (below) |

Populate the Whisper cache once, with network access: `SRA_RUN_WHISPER_TESTS=1 WHISPER_MODEL_SIZE=base .venv/bin/python -m pytest tests/integration/test_transcription_whisper.py -v`. `WHISPER_MODEL_SIZE` selects the model (default `base`). Missing dependencies or weights fail an explicitly requested run rather than skipping silently. Repeat with cached weights offline before recording a ticket's completion. The fixture's text, provenance, and expected keywords are in [`fixtures/README.md`](fixtures/README.md).

## Two complete verification commands

From the repository root, after installing `.[dev,capture]`:

```bash
make test-data   # Full suite + real fixture audio; no OpenAI calls
make test-live   # Real fixture audio + real OpenAI extraction and chat
```

Both use `.venv/bin/python` by default; override with `make PYTHON=python test-data`. Both require the cached Whisper `base` model and set `HF_HUB_OFFLINE=1` to prevent implicit downloads. `test-live` reads `OPENAI_API_KEY` and `OPENAI_MODEL` from the shell or the repository `.env`, sends only the synthetic fixture transcript and a study-history question, incurs normal API charges, and runs with tracing disabled. Neither command touches the normal study database or needs a running backend. Do not provide production credentials to untrusted CI jobs.

`tests/integration/test_audio_flow.py` starts a real Uvicorn server on an ephemeral loopback port with a temporary SQLite file. It replays the fixture's PCM through the production recorder/controller, runs real local Whisper, sends one real HTTP request, and checks storage, ownership, all six review dates, search, chat, due-date digest, application/engine restart, and file cleanup. Only extraction/query models are replaced in `test-data`; `test-live` uses the configured OpenAI models. "Restart" means a recreated application/server/DB engine inside the same test process, not a desktop process or OS restart.

The fixture command also covers quieter audio (20% amplitude), deterministic low-level noise, denied-device permission then recovery, overflow then recovery, real digital silence, and a corrupt WAV. Reused callback buffers are overwritten after delivery, which verifies the recorder copies them correctly. Whisper voice-activity filtering prevents the observed silent-input hallucination from being submitted. These variants establish behavior for those signals, not a general guarantee of recognition in every room or with every microphone.

## What each layer proves

| Layer | Real components | Replaced boundaries |
|---|---|---|
| Unit | One function, class, or lifecycle controller | Its collaborators: HTTP responses, Whisper model, audio stream, pipeline, worker/UI queues |
| Automated end to end | Text client → API/storage, or terminal → recorder/WAV → transcription → pipeline/client → FastAPI → graph → repository → SQLite → scheduler → search/digest | In-process HTTP transport, deterministic inference, controlled PCM/input/clock |
| Whisper integration | Real spoken WAV, transcription wrapper, local Whisper model | None; opt-in because packages/weights are needed |
| Desktop acceptance | Actual hotkey, microphone, macOS UI, Whisper, running API, model provider | None; manual steps in SRA-204/205/206 |

The end-to-end tests do not mock `process_recording`, `transcribe`, `ingest_transcript`, routes, repositories, or scheduling. They verify returned IDs against stored rows and search, original transcript and local-user ownership, six pending review dates per entry, and the next-day digest. They advance the digest's clock without changing stored schedules. They also test silence, late decoding failure, server rejection/recovery, and a response lost **after** saving (the client must report uncertainty and never automatically resubmit). Terminal end-to-end tests run the real recorder and CLI, checking PCM/WAV handoff, saved IDs, silence, cancellation, and cleanup after a lost response.

## Real hardware: desktop acceptance

The default suite does not prove physical microphone quality, global key delivery, OS permissions, or UI responsiveness.

| Boundary | Automated evidence | Remaining physical check |
|---|---|---|
| Microphone audio | Real PCM/WAV handoff, quiet/noisy fixture recognition, silence, corrupt audio, overflow, closure/cleanup | Actual input device, room noise, distance, clipping, dropout and intelligibility |
| Permissions | Device permission errors, clear status, no submission, successful next capture | macOS granting/denying access to the actual launching application |
| Hotkey | Exact chord parsing/native registration arguments, callback dispatch, conflicts/handler failures and recovery, duplicate start/stop, rapid toggles | Key delivery while another app has focus and competing shortcuts |
| UI lifecycle | Simulated event loop/worker dispatch, errors, cancellation and quit during processing | Native menu-bar responsiveness and OS event-loop behavior |

For an acceptance run: launch the menu-bar app from the intended terminal or IDE, use the shortcut with another application focused, speak the fixture script, and verify the new IDs. Repeat at normal and quieter speaking levels; inspect/listen to a deliberately retained **user-owned** recording to assess clipping and room noise. Deny microphone permission in System Settings, check the useful error and absence of a save, then restore permission and relaunch. Check shortcut conflict/recovery and restart capture. Do not automate changes to macOS privacy permissions.

A desktop runner with a configured virtual audio input can replay the fixture through the actual device API. That tests more OS integration but still bypasses the physical microphone and acoustics. Neither virtual audio routing nor synthetic key injection establishes that physical keys or microphones work; those checks require hardware and the intended desktop session.

## The manual-evidence rule

- A passing offline suite is never evidence of real Google, Keychain, multi-process rate-limit, microphone, hotkey, or UI behavior. Record which boundaries were substituted.
- Record actual execution — date, environment, commands, inputs, new IDs or artifacts, observed results, failure/recovery, cleanup, skips, and remaining limitations — in the ticket's **Verification record**. Never fill it with expected outcomes presented as observed results.
- Track newly created IDs through the flow. A success message or a pre-existing search match alone does not prove the new action persisted.
- Use an isolated test clock for time-dependent results instead of altering stored dates.
- Cite real command output; do not reuse a historical pass count as a current result. Phase status lives in [`PROGRESS.md`](../PROGRESS.md).