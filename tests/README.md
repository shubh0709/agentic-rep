# Phase 2 test-first learning guide

The tests are written; you implement the functions. Phase 1 already works.
Phase 2 learning functions contain typed signatures, teaching docstrings, and
`raise NotImplementedError`. Errors, protocols, settings fields, and test fixtures
are supporting plumbing. **SRA-201 and SRA-202 are implemented**; continue with
**SRA-203** through **SRA-205**.
SRA-206 verifies the finished system and the real desktop flow.

Client configuration and HTTP submission are implemented: their **77 unit/e2e
tests pass**, including strict response validation, interruption/timeout errors,
storage, scheduling, search, and recovery. The audio capture functions remain stubs.

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

`pytest -q` includes all Phase 2 contracts. It is intentionally red while later
learning functions are unimplemented; the `phase2` marker lets you separately
verify existing functionality. There are no expected-failure markers hiding
unfinished behavior and no tests that pass just because a stub raises
`NotImplementedError`. All default tests must pass before SRA-206 closes. CI
continues to run the full suite and will remain red during this scaffold stage.

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
implemented `capture` extra, including local Whisper inference. Desktop-only
audio/UI dependencies still need their own installation and lock verification
as those tickets are implemented.
