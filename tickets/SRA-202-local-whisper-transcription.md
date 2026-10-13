# SRA-202 — Local Whisper transcription into saved study entries

**Type:** Task
**Phase:** 2 — Voice capture
**Priority:** High — first saved-audio-to-study-entry slice
**Status:** TDD scaffold ready — user implementation pending
**Blocked by:** SRA-201
**Blocks:** SRA-203

## TDD starting point

The assistant has supplied this ticket's tests, typed stubs, and teaching docstrings. **You write the learning-function implementations.** See the [test-first guide](../tests/README.md) for the unit/e2e boundary and implementation order. Focused tests begin red at `NotImplementedError`; after implementing this ticket they must pass. Keep Phase 1 regressions green with `pytest -m 'not phase2' -q`. Later exercises remain visibly red until implemented; the complete default suite must be green for SRA-206. The expected passing results below describe the implemented ticket, not the current scaffold.

## Summary

Implement the local transcription wrapper in `src/sra/capture/transcription.py`: take an audio file, load a cached `faster-whisper` model, and return the spoken text. Then send that text through SRA-201's completed HTTP client and verify the created entries in search. The new concept is local speech recognition; the manual check follows its result all the way into the working app.

## Where things stand right now

After SRA-201, the Python client already creates searchable entries from text. Nothing turns audio into that text yet. Starting from a saved WAV lets you add this boundary before dealing with microphone permissions or UI callbacks.

## What this ticket unlocks

**Saved WAV → local Whisper → transcript → existing HTTP client → stored entries → search by returned ID.** No later Phase 2 ticket is needed for this check. Local transcription can also run offline once weights are cached; ingestion uses the running backend and its model service.

## Files

- New: `src/sra/capture/__init__.py`, `src/sra/capture/transcription.py`
- New: `tests/unit/test_transcription.py`, `tests/integration/test_transcription_whisper.py`
- New: `tests/fixtures/study_note.wav`, `tests/fixtures/README.md`
- `src/sra/config.py`, `tests/unit/test_client_config.py`, `.env.example`
- `pyproject.toml`, `requirements-lock.txt`, `README.md`

## What to implement

- Implement the supplied typed stubs against their tests and teaching docstrings. Extend `ClientSettings` with `whisper_model_size` from `WHISPER_MODEL_SIZE`, default `base`, without requiring server credentials. Extend the `capture` extra with `faster-whisper` and document installation/model caching.
- Add a short spoken-study-note WAV with non-sensitive content. Document the words, expected keywords, and source/permission to use it in the fixture README. The real Whisper fixture test is opt-in with `SRA_RUN_WHISPER_TESTS=1`; default tests fake inference and never download weights.
- `load_model(model_size: str)` — construct and cache a `faster-whisper` model by size. Same size returns the same model object; different sizes have distinct cache entries. Keep construction replaceable in tests, and load lazily rather than at module import.
- `transcribe(audio_path: Path, model_size: str = "base") -> str` — validate the input file, get the model, consume the returned segments, and join their text in order into a trimmed transcript. Errors can occur while consuming segments as well as when starting transcription; handle both.
- `TranscriptionError` — expose a clear, typed failure for missing/unreadable/corrupt audio and model loading/inference failures. Preserve the underlying cause for debugging. No speech yields an empty string; the pipeline decides whether anything should be submitted.
- Do not mutate or delete the supplied audio file. The component that created a temporary recording owns its cleanup.

## Where to look

- Plan U4 and technical design §3.26 in `docs/`.
- `get_client_settings()` and `ingest_transcript()` from SRA-201; this ticket adds the client model-size setting and passes it to `transcribe`.
- The fixture README for what was spoken and which keywords the live transcription check should recognize.

## Acceptance criteria

- [ ] Repeated loads of the same model size return the same object; another size constructs a separate model
- [ ] Segment text is consumed fully, joined in order, and stripped of surrounding whitespace
- [ ] Missing/corrupt input and failures during model loading or segment iteration raise `TranscriptionError`
- [ ] An empty segment stream returns `""`; input audio remains intact on success and failure
- [ ] Unit tests use fake models and never download weights or access a cloud STT service
- [ ] The opt-in test transcribes the real fixture with loose expected-keyword assertions and documents the first-run model download
- [ ] The manual flow uses that real transcript with the completed HTTP client, creates entries, and retrieves their exact IDs through search
- [ ] A missing file raises a clear error before ingestion; restoring the input allows the full flow to succeed

## How to test this ticket alone

Start with the supplied scaffold:

```bash
source .venv/bin/activate
pytest tests/unit/test_client_config.py tests/unit/test_transcription.py -v
```

Expected: all transcription unit tests pass without installing or loading a real model. Clear the model cache between cases so one test cannot satisfy another by accident.

Run the real model check explicitly:

```bash
pip install -e ".[dev,capture]"
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
```

The first run may download model weights. Subsequent runs should work offline with the same cached model. Use keyword assertions, not an exact sentence or punctuation match.

## Manual verification

With the Phase 1 server running and the capture extra installed, run the two completed functions in sequence:

```bash
python3 - <<'PY'
from pathlib import Path
import httpx
from sra.capture.transcription import transcribe
from sra.client.api_client import ingest_transcript
from sra.config import get_client_settings

settings = get_client_settings()
transcript = transcribe(Path("tests/fixtures/study_note.wav"), settings.whisper_model_size)
print("Transcript:", transcript)
assert transcript.strip(), "No speech recognized"
entries = ingest_transcript(transcript)
assert entries, "No entries created"
base = str(settings.api_base_url).rstrip("/")
with httpx.Client(timeout=30) as client:
    for entry in entries:
        response = client.get(f"{base}/api/v1/search", params={"q": entry["topic"]})
        response.raise_for_status()
        assert entry["id"] in {row["id"] for row in response.json()}
        print("Saved and found:", entry["id"], entry["topic"], entry["summary"])
PY
```

Expect readable text matching the spoken fixture followed by `Saved and found` for the new entry IDs. Inspect the topics/summaries to confirm they reflect the recording. Merely printing plausible transcription is insufficient to close the ticket.

Repeat with a nonexistent input and confirm `TranscriptionError` occurs before the HTTP submission. Restore the path and confirm recovery. Separately rerun just `transcribe(...)` offline using cached weights; that local step requires no server or OpenAI key. The full save flow uses the server's real model.

## Verification record

- Date, fixture, model size, and observed transcript:
- Created entry IDs and matching search IDs/topics:
- Missing-file failure, recovery, and cached offline transcription results:
- Unit/opt-in/full test, lint, and type-check results:

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Unit tests and the opt-in real-fixture test pass
- [ ] Real audio → transcript → client → API → search flow and recovery recorded above
- [ ] Cached transcription also works offline
- [ ] Focused/completed-ticket tests, Phase 1 regressions, `ruff check .`, and `pyright` pass (all default tests must pass by SRA-206); audio setup documented
