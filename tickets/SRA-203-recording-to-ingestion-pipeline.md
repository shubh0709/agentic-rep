# SRA-203 — Recording-to-ingestion pipeline

**Phase:** 2 — Voice capture · **Status:** implemented

## Summary

SRA-202 teaches two calls separately: first `transcribe(path)`, then
`ingest_transcript(text)`. `src/sra/capture/pipeline.py` makes them one
application operation — "process this recording and give me the created entries."
It owns their order and the decision to submit. The function is small, but that is
the point: a terminal command and a menu-bar callback should both treat silence,
errors, and submission the same way instead of each recreating that policy.

## Unlocks

- **One call with a WAV → transcript → API → searchable entries and scheduled
  reviews.** Silence returns a no-speech result, transcription failures prevent
  submission, and client failures propagate without retrying.
- SRA-204's recorder and SRA-205's menu-bar app reuse exactly this behavior.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/pipeline.py`](../src/sra/capture/pipeline.py) | edit | `process_recording(audio_path: Path) -> list[dict[str, Any]]` — reads the client model setting, calls `transcribe(...)`, then `api_client.ingest_transcript(transcript)` **once** and returns the created entries. |
| [`tests/unit/test_capture_pipeline.py`](../tests/unit/test_capture_pipeline.py) | run | Orchestration: configured model size, original text forwarded once, silence skips HTTP, errors propagate, input survives. |
| [`tests/e2e/test_voice_capture_to_digest_e2e.py`](../tests/e2e/test_voice_capture_to_digest_e2e.py) | run | Real pipeline through transcription, HTTP, route, graph, repository, and scheduler — only inference and transport location substituted. |

## Acceptance criteria

- [x] The configured model size reaches transcription and one nonempty transcript
  produces one HTTP submission
- [x] No speech returns `[]` with no submission; transcription/API errors preserve
  their typed failure and never trigger retries
- [x] The supplied audio file remains intact on success, no-speech, and failure
- [x] The e2e test proves stored transcript/ownership, six pending reviews,
  immediate search, no new review due on capture day, and `1d` reviews visible on
  the next day
- [ ] The single-call manual flow below creates searchable entries from real audio
  against the running backend
- [x] A missing input and an unreachable server report failures; restoring the
  input/server allows the next call to succeed

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_capture_pipeline.py -v
pytest tests/e2e/test_voice_capture_to_digest_e2e.py -v
```

Passes without real inference, microphone access, or a network server. The e2e
checks advance the digest date, so the due-date assertion needs no overnight wait.

## Manual verification

- [ ] With the server running and the capture extra installed, this prints new IDs
      found in search and confirms the input file was not modified:

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

- [ ] Repeat with a nonexistent path and then with the server unavailable; confirm
      typed errors, no false success, and a successful call after restoring the
      input/server.

> **Note:** `[]` means **no returned entries**, and by itself does not prove
> whether a POST happened — the HTTP wrapper also permits a valid `201` with an
> empty list. The no-speech tests explicitly verify no request was made. Use those
> deterministic tests for the exact no-POST assertion; a fresh entry is also not
> due in the digest immediately.

### Verification record

- **2026-10-13:** deterministic WAV/model boundary via the focused unit and e2e
  commands above. The real `tests/fixtures/study_note.wav` and `faster-whisper`
  are available, but sending its transcript to the configured external OpenAI
  service was not authorized, so the live submission was not run.
- The isolated e2e flow created two unique IDs and found each exact ID through the
  real search route; **live external-service IDs remain pending**.
- **15 SRA-203 focused tests passed**; **108 completed-ticket tests passed**; **91
  Phase 1 regressions passed**; Ruff and Pyright passed. Success, silence,
  late-decoder failure, missing-file, server-rejection, and subsequent-call
  recovery assertions all passed; every caller-owned input remained byte-for-byte
  intact.
- Full suite: **200 passed, 46 failed, 2 skipped** — all failures are the
  intentional SRA-204/SRA-205 teaching stubs.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).