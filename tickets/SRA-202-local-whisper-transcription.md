# SRA-202 — Local Whisper transcription into saved study entries

**Phase:** 2 — Voice capture · **Status:** implemented

## Summary

The backend accepts **text**, while a recording contains audio samples.
`src/sra/capture/transcription.py` bridges those two representations: a `Path` to
an audio file in, a `str` of recognized speech out. SRA-201's HTTP client then
accepts that string exactly as it accepts a typed note, so the server never needs
to know whether text came from typing or speaking. The new concept here is local
speech recognition; the manual check follows its result into the working app.

## Unlocks

- **Saved WAV → local Whisper → transcript → existing HTTP client → stored entries
  → search by returned ID.** No later Phase 2 ticket is needed for this check.
- SRA-203 composes this into a single `process_recording()`; SRA-204 supplies a WAV
  from a microphone instead of the fixture.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/transcription.py`](../src/sra/capture/transcription.py) | edit | `load_model(model_size)` caches a `faster-whisper` model; `transcribe(audio_path, model_size="base") -> str` validates the file, consumes segments, joins them trimmed; `TranscriptionError` covers missing/unreadable/corrupt audio and model loading/inference failures. |
| [`src/sra/config/`](../src/sra/config/) | edit | `ClientSettings.whisper_model_size` from `WHISPER_MODEL_SIZE`, default `base`, without requiring server credentials. |
| [`tests/unit/test_transcription.py`](../tests/unit/test_transcription.py) | run | Joining, caching, input preservation, and early/late failures using fake models. |
| [`tests/integration/test_transcription_whisper.py`](../tests/integration/test_transcription_whisper.py) | run | Opt-in (`SRA_RUN_WHISPER_TESTS=1`) evidence that the wrapper can use real Whisper on real audio. |

## Acceptance criteria

- [x] Repeated loads of the same model size return the same object; another size
  constructs a separate model
- [x] Segment text is consumed fully, joined in order, and stripped of surrounding
  whitespace
- [x] Missing/corrupt input and failures during model loading or segment iteration
  raise `TranscriptionError`
- [x] An empty segment stream returns `""`; input audio remains intact on success
  and failure
- [x] Unit tests use fake models and never download weights or access a cloud STT
  service
- [x] The opt-in test transcribes the real fixture with loose expected-keyword
  assertions and documents the first-run model download
- [ ] The manual flow uses that real transcript with the completed HTTP client,
  creates entries, and retrieves their exact IDs through search
- [ ] A missing file raises a clear error before ingestion; restoring the input
  allows the full flow to succeed

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_client_config.py tests/unit/test_transcription.py -v
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
```

The first run may download model weights; later runs work offline with the same
cached model. Clear the model cache between cases so one test cannot satisfy
another by accident. Use keyword assertions, not an exact sentence or punctuation
match.

## Manual verification

- [ ] With the Phase 1 server running and the capture extra installed, this prints
      a transcript then `Saved and found` per new ID:

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

- [ ] Repeat with a nonexistent input and confirm `TranscriptionError` occurs
      before the HTTP submission; restore the path and confirm recovery.

> **Note:** `load_model`'s `lru_cache` reuses a model object **within one Python
> process**, while downloaded weights are **files reused across process
> restarts**. Restarting clears the former but should not re-download the latter.

### Verification record

- **2026-10-13**, `tests/fixtures/study_note.wav`, `base`: "Today I studied
  photosynthesis, plants use sunlight to turn water and carbon dioxide into sugar
  and oxygen, chlorophyll absorbs light in the leaves."
- **40 focused configuration/transcription tests passed**; the opt-in real-Whisper
  fixture test passed; 91 Phase 1 regressions passed; Ruff and Pyright passed. The
  cached model transcribed the fixture with `HF_HUB_OFFLINE=1`.
- A clean Python 3.13 environment installed the pinned capture set and passed
  `pip check`. PyAV is constrained below 19 because `faster-whisper` 1.2.1 still
  passes an argument removed in PyAV 19.
- Full suite: **185 passed, 61 failed, 2 skipped** — failures confined to
  SRA-203–205 teaching stubs.
- **Not recorded:** live transcript-to-server entry IDs (this needs a running
  model-backed API), and the missing-file failure/recovery run.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).