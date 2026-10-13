# SRA-203 — Recording-to-ingestion pipeline

**Type:** Task
**Phase:** 2 — Voice capture
**Priority:** High — gives capture callers one complete operation
**Status:** TDD scaffold ready — user implementation pending
**Blocked by:** SRA-202 (transitively: SRA-201)
**Blocks:** SRA-204

## TDD starting point

The assistant has supplied this ticket's tests, typed stubs, and teaching docstrings. **You write the learning-function implementations.** See the [test-first guide](../tests/README.md) for the unit/e2e boundary and implementation order. Focused tests begin red at `NotImplementedError`; after implementing this ticket they must pass. Keep Phase 1 regressions green with `pytest -m 'not phase2' -q`. Later exercises remain visibly red until implemented; the complete default suite must be green for SRA-206. The expected passing results below describe the implemented ticket, not the current scaffold.

## Summary

Implement `process_recording(audio_path)` in `src/sra/capture/pipeline.py`. It owns the sequence from local transcription to HTTP ingestion, including no-speech and failure behavior, so every capture surface can call one function.

## Where things stand right now

SRA-202 proved a real WAV can become searchable entries by manually calling transcription and the HTTP client in sequence. That script does not yet provide a reusable operation that handles silence and failures consistently. This ticket establishes that operation and tests it through the real API and scheduler.

## What this ticket unlocks

**One call with a WAV → transcript → API → searchable entries and scheduled reviews.** Silence returns a no-speech result, transcription failures prevent submission, and client failures propagate without retrying. The next capture surfaces reuse exactly this behavior.

## Files

- New: `src/sra/capture/pipeline.py`
- New: `tests/unit/test_capture_pipeline.py`, `tests/e2e/test_voice_capture_to_digest_e2e.py`
- `README.md` (saved-recording usage)

## What to implement

- Use the supplied teaching docstring and behavior tests to implement `process_recording(audio_path: Path) -> list[dict[str, Any]]`.
- Read the client model setting, call `transcribe(audio_path, model_size=...)`, call `api_client.ingest_transcript(transcript)` once, and return the created entries.
- Empty/whitespace transcription returns `[]` without an HTTP call. Let `TranscriptionError` and `ApiClientError` reach the caller; never disguise them as silence or retry the operation.
- Leave the input audio intact. A caller may supply a user-owned recording; its creator owns any temporary-file cleanup.
- Add an e2e test through the real pipeline, transcription wrapper, HTTP wrapper, FastAPI route, graph, repository, and scheduler. Substitute Whisper/model inference and HTTP transport location, using an isolated DB. Confirm original transcript, server-resolved ownership, six pending reviews per entry, immediate search visibility, and the next-day digest. Keep the actual pipeline and ingestion call in the test.

## Where to look

- Technical design §3.30, which refines plan U5 to call the API rather than a local graph.
- `tests/conftest.py` and `tests/e2e/test_capture_to_digest_e2e.py` for isolated DB/API wiring and advancing the digest date without changing stored schedules.
- SRA-201 and SRA-202 for the completed client and transcription boundaries.

## Acceptance criteria

- [ ] The configured model size reaches transcription and one nonempty transcript produces one HTTP submission
- [ ] No speech returns `[]` with no submission; transcription/API errors preserve their typed failure and never trigger retries
- [ ] The supplied audio file remains intact on success, no-speech, and failure
- [ ] The e2e test proves stored transcript/ownership, six pending reviews, immediate search, no new review due on capture day, and `1d` reviews visible on the next day
- [ ] The single-call manual flow below creates searchable entries from real audio against the running backend
- [ ] A missing input and an unreachable server report failures; restoring the input/server allows the next call to succeed

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_capture_pipeline.py -v
pytest tests/e2e/test_voice_capture_to_digest_e2e.py -v
```

Expected: all pass without real inference, microphone access, or a network server. The e2e checks advance the digest date, so the due-date assertion needs no overnight wait. Finish with green completed-ticket and Phase 1 checks, lint, and type check; inspect remaining full-suite failures.

## Manual verification

With the server running and the capture extra installed, use the actual pipeline:

```bash
python3 - <<'PY'
from pathlib import Path
import httpx
from sra.capture.pipeline import process_recording
from sra.config import get_client_settings

audio = Path("tests/fixtures/study_note.wav")
before = audio.read_bytes()
entries = process_recording(audio)
assert entries, "No entries created from the spoken fixture"
assert audio.read_bytes() == before, "Input audio changed"
base = str(get_client_settings().api_base_url).rstrip("/")
with httpx.Client(timeout=30) as client:
    for entry in entries:
        response = client.get(f"{base}/api/v1/search", params={"q": entry["topic"]})
        response.raise_for_status()
        assert entry["id"] in {row["id"] for row in response.json()}
        print("Saved and found:", entry["id"], entry["topic"])
PY
```

Expect new IDs found in search and an unchanged input file. Repeat with a nonexistent path and then with the server unavailable; confirm typed errors, no false success, and a successful call after restoring the input/server. Use the deterministic tests for the exact no-speech/no-POST assertion. Confirm `1d` digest visibility using the focused e2e test above; a fresh entry is not due immediately.

## Verification record

- Date, audio path/model, and commands used:
- Created entry IDs and matching search IDs:
- Input-file preservation, missing-file/server failure, and recovery results:
- E2e scheduling/digest result and full test/lint/type-check results:

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Real single-call audio → API → search flow and error recovery recorded above
- [ ] Storage/scheduling/digest assertions pass through the real e2e wiring
- [ ] Focused/completed-ticket tests, Phase 1 regressions, `ruff check .`, and `pyright` pass (all default tests must pass by SRA-206); saved-recording usage documented
