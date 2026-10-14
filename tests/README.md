# Test-first learning guide

## Phase 3: authentication and multi-tenancy

Phase 3 started with executable contracts. SRA-301 through SRA-304 are now
implemented and their automated checks pass. Default tests do not contact Google, open a browser, touch Keychain, wait
for a real clock, or use the microphone.

```bash
source .venv/bin/activate
pytest tests/unit/test_user_model.py -v
pytest tests/unit/test_auth.py -v
pytest tests/integration/test_authenticated_routes.py -v
pytest tests/unit/test_auth_client.py tests/unit/test_api_client.py -m phase3 -v
pytest tests/unit/test_rate_limit.py -v
pytest tests/integration/test_rate_limited_routes.py -v
pytest tests/e2e/test_authenticated_capture_e2e.py -v
pytest tests/integration/test_route_policy_audit.py -v
```

The intended red-to-green order is:

1. SRA-301: make `test_user_model.py` green, then add and rehearse the Alembic
   migration against the actual Phase 2 revision.
2. SRA-302: implement provider verification, identity upsert, application
   credentials, completion codes, and protected route/RBAC integration.
3. SRA-303: implement desktop loopback and secure-storage behavior, then add the
   bearer header without changing the one-POST uncertainty contract.
4. SRA-304: implement deterministic, synchronized token buckets, then wire them
   ahead of route side effects.
5. Run the authenticated capture E2E test after each server/client wiring step;
   it keeps the graph, repository, schedules, and SQLite storage real.

Use `pytest -m 'not phase3' -q` while working one red contract at a time. Phase 3
is not complete until the full suite, migration rehearsals, route integration
tests, lint, type checking, and the manual evidence in SRA-305 all pass. A
passing default/offline suite is not evidence of real Google, Keychain, or
multi-process rate-limit behavior.

SRA-305 verification on 2026-10-14: default suite **386 passed, 9 opt-in skips**;
cached-Whisper `make test-data` **394 passed, 1 live-provider deselected**;
Phase 3 alone **112 passed, 283 deselected**. Ruff/Pyright passed. Real localhost
tests cover callback nonce binding, incomplete-header deadline/cancellation and
port/thread cleanup; route inventory rejects unreviewed mounted endpoints.
The secure-storage tests use a fake backend (including adapter recreation), and
native menu tests substitute Cocoa. The real-audio HTTP harness now uses signed
test credentials and an isolated OAuth-shaped owner, preserving auth guards
through application/engine/server recreation. No real Google, Keychain or
physical desktop acceptance is implied; see SRA-305 for the outstanding record.

## Phase 2 voice capture

## Two complete verification commands

From the repository root, after installing `.[dev,capture]`:

```bash
make test-data   # Full suite + real fixture audio; no OpenAI calls
make test-live   # Real fixture audio + real OpenAI extraction and chat
```

Both use `.venv/bin/python` by default; override with `make PYTHON=python test-data`.
Both require the cached Whisper `base` model and use `HF_HUB_OFFLINE=1` to prevent
implicit downloads. To populate the cache once, run
`SRA_RUN_WHISPER_TESTS=1 WHISPER_MODEL_SIZE=base .venv/bin/python -m pytest tests/integration/test_transcription_whisper.py -v`
with network access. Missing dependencies or weights fail an explicitly requested
run. Plain `pytest -q` remains the fast suite and skips these opt-in audio tests.

`test-live` reads `OPENAI_API_KEY` and `OPENAI_MODEL` from the shell or repository
`.env`. It sends only the synthetic fixture transcript and a study-history
question to OpenAI and incurs normal API charges. Tracing is disabled. Neither
command uses the normal study database or needs an already running backend.
Do not provide production credentials to untrusted CI jobs.

`tests/integration/test_audio_flow.py` starts a real Uvicorn server on an
ephemeral loopback port with a temporary SQLite file. It replays the fixture's
PCM through the production recorder/controller, runs real local Whisper, sends
one real HTTP request, and checks storage, ownership, all six review dates,
search, chat, due-date digest, application/engine restart, and file cleanup.
Only extraction/query models are replaced in `test-data`; `test-live` uses the
configured OpenAI models. Restart means recreated application/server/DB engine
in the same test process, not a desktop process or OS restart.

The fixture command also covers quieter audio (20% amplitude), deterministic
low-level noise, denied-device permission followed by recovery, overflow followed
by recovery, real digital silence, and a corrupt WAV. Reused callback buffers are
overwritten after delivery to verify the recorder copies them correctly. Whisper
voice-activity filtering prevents the observed silent-input hallucination from
being submitted. These variants establish behavior for those signals, not a
general guarantee of recognition in every room or with every microphone.

## Desktop coverage and its limits

| Boundary | Automated evidence | Remaining physical check |
|---|---|---|
| Microphone audio | Real PCM/WAV handoff, quiet/noisy fixture recognition, silence, corrupt audio, overflow, closure/cleanup | Actual input device, room noise, distance, clipping, dropout and intelligibility |
| Permissions | Device permission errors, clear status, no submission, successful next capture | macOS granting/denying access to the actual launching application |
| Hotkey | Exact chord parsing/native registration arguments, callback dispatch, conflicts/handler failures and recovery, duplicate start/stop, rapid toggles | Key delivery while another app has focus and competing shortcuts |
| UI lifecycle | Simulated event loop/worker dispatch, errors, cancellation and quit during processing | Native menu-bar responsiveness and OS event-loop behavior |

For a desktop acceptance run, launch the menu-bar app from the intended terminal
or IDE, use the shortcut with another application focused, speak the fixture
script, and verify the new IDs. Repeat at normal and quieter speaking levels;
inspect/listen to a deliberately retained **user-owned** recording to assess
clipping and room noise. Deny microphone permission in System Settings, check
the useful error and absence of a save, then restore permission and relaunch.
Check shortcut conflict/recovery and restart capture. Record results in SRA-204,
SRA-205 and SRA-206. Do not automate changes to macOS privacy permissions.

A dedicated desktop runner with a configured virtual audio input can replay the
fixture through the actual device API. That would test more OS integration but
still bypass the physical microphone/acoustics. Neither virtual audio routing
nor synthetic key injection establishes that physical keys or microphones work;
those checks require hardware and the intended desktop session.

Verification on 2026-10-14: `make test-data` **280 passed, 1 deselected**;
`make test-live` **1 passed, 7 deselected**, with real OpenAI extraction/chat.
The deselections separate provider-backed tests from local fixture tests.

Phase 2 began with tests, typed signatures, teaching docstrings, and
`NotImplementedError` exercises. **SRA-201 through SRA-205 are now implemented**;
continue with **SRA-206 verification**.
SRA-206 verifies the finished system and the real desktop flow.

Client configuration and HTTP submission are implemented: their **77 unit/e2e
tests pass**, including strict response validation, interruption/timeout errors,
storage, scheduling, search, and recovery. Transcription, pipeline, recorder, controller, and adapter wiring tests also pass.

## Red → green → refactor

```bash
source .venv/bin/activate
pytest -m 'not phase2' -q   # existing Phase 1 regression suite
pytest tests/unit/test_client_config.py tests/unit/test_api_client.py -x -v
```

1. Read the failing assertion and the relevant function's docstring.
2. Implement enough behavior to satisfy that contract, then rerun the focused test.
3. Run all unit tests for that ticket, then its end-to-end tests.
4. Refactor with those checks green, and rerun the Phase 1 regression suite.
5. Complete the ticket's real-component manual checks and record the evidence.

`pytest -q` includes all Phase 2 contracts and now passes. The `phase2` marker
lets you separately verify Phase 1 functionality. There are no expected-failure markers hiding
unfinished behavior and no tests that pass just because a stub raises
`NotImplementedError`. All default tests must pass before SRA-206 closes. CI
continues to run the full suite.

## What each layer proves

| Layer | Real components | Replaced boundaries |
|---|---|---|
| Unit | One function, class, or lifecycle controller | Its collaborators: HTTP responses, Whisper model, audio stream, pipeline, worker/UI queues |
| Automated end to end | Text client → API/storage, or terminal → recorder/WAV → transcription → pipeline/client → FastAPI → graph → repository → SQLite → scheduler → search/digest | In-process HTTP transport, deterministic inference, controlled PCM/input/clock |
| Whisper integration | Real spoken WAV, transcription wrapper, local Whisper model | None; opt-in because packages/weights are needed |
| Desktop acceptance | Actual hotkey, microphone, macOS UI, Whisper, running API, model provider | None; manual steps in SRA-204/205/206 |

The end-to-end tests do not mock `process_recording`, `transcribe`,
`ingest_transcript`, routes, repositories, or scheduling. They verify returned
IDs against stored rows and search, original transcript and local-user ownership,
six pending review dates per entry, and the next-day digest. They advance the
digest's clock without changing stored schedules. They also test silence, late
decoding failure, server rejection/recovery, and a response lost **after** saving
(the client must report uncertainty and never automatically resubmit).
Terminal end-to-end tests also run the real recorder and CLI, checking PCM/WAV
handoff, saved IDs, silence, cancellation, and cleanup after a lost response.

The default suite does not prove physical microphone quality, global key
delivery, OS permissions, or UI responsiveness. Those require the explicit
desktop acceptance checks; a platform skip never counts as a verified feature.

## Ticket commands

| Ticket | Focused checks |
|---|---|
| SRA-201 | `pytest tests/unit/test_client_config.py tests/unit/test_api_client.py tests/e2e/test_client_ingestion_e2e.py -v` |
| SRA-202 | `pytest tests/unit/test_transcription.py -v` |
| SRA-203 | `pytest tests/unit/test_capture_pipeline.py tests/e2e/test_voice_capture_to_digest_e2e.py -v` |
| SRA-204 | `pytest tests/unit/test_recording.py tests/e2e/test_terminal_capture_e2e.py -v` |
| SRA-205 | `pytest tests/unit/test_menubar_app.py -v` |
| SRA-206 | `pytest -q`, `ruff check .`, `pyright`, plus real Whisper and desktop acceptance |

Client configuration tests include the later model-size and hotkey fields so
their defaults and independence from server credentials are specified early.
Recorder tests use captured PCM frames and read the resulting WAV to check its
format and content; they also check cleanup after completion, error, and
cancellation. Controller tests drive queued worker/UI callbacks to check
non-overlap, recovery, and file ownership through quit.

## Optional real-component checks

`pip install -e '.[dev]'` is sufficient for default test collection and fake
inference. No model weights are downloaded and no device is opened by those
tests. After implementing transcription:

```bash
pip install -e '.[dev,capture]'
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
```

The spoken fixture's text, provenance, and generation command are in
[`fixtures/README.md`](fixtures/README.md). First use may download weights;
repeat with cached weights offline before recording SRA-202 completion.

On macOS, install `'.[dev,desktop]'` for real recording and menu-bar acceptance.
The module launch commands in the tickets become usable after you implement
their stubs. `requirements-lock.txt` covers the server/dev environment and the
implemented `capture` extra, including local Whisper inference, plus macOS-only
audio/UI dependencies guarded with platform markers. The desktop dependency set
was installed and checked in a clean Python 3.13 environment for SRA-204; physical
microphone and hotkey behavior still require manual verification.
