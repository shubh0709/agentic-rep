# SRA-204 — Microphone capture through the working pipeline

**Phase:** 2 — Voice capture · **Status:** implemented

## Summary

The pipeline accepts a file path; a microphone provides a stream of audio samples.
`src/sra/capture/recording.py` turns that live stream into the finalized WAV the
pipeline already understands, and a small terminal entry point joins the two so
speaking produces the same saved entries as a typed note. Proving actual device
access, WAV finalization, and cleanup here — before any menu-bar UI — keeps the
desktop concerns separable.

## Unlocks

- **Speak into the microphone → stop with Enter → local transcription → stored
  entries → search.** This is the same recorder and pipeline SRA-205 will drive
  from a hotkey.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/recording.py`](../src/sra/capture/recording.py) | edit | `_open_audio_stream` adapts optional `sounddevice`; `Recorder` exposes `start()`, `stop() -> Path`, `close()` and owns its temporary WAV; `main()` connects Enter presses to `process_recording` and prints the result. |
| [`tests/unit/test_recording.py`](../tests/unit/test_recording.py) | run | WAV contents, lifecycle transitions, cleanup, cancellation, and the terminal handoff with controlled audio and failures. |
| [`tests/e2e/test_terminal_capture_e2e.py`](../tests/e2e/test_terminal_capture_e2e.py) | run | Actual recorder, terminal command, pipeline, HTTP client, and backend with fake hardware/inference. |

## Acceptance criteria

- [ ] Starting and stopping actual microphone capture produces a valid WAV that the
  existing pipeline can transcribe
- [x] The terminal entry point drives capture → pipeline → server and reports saved
  IDs/topics only after successful ingestion
- [x] A completed recording is processed once; cancellation does not submit
  unfinished audio
- [x] Audio remains available throughout processing and is removed afterward, with
  the microphone closed on completion, error, and interruption
- [ ] Permission/device/server failures have useful messages; the next attempt
  succeeds after recovery
- [ ] The manual flow finds the exact newly reported IDs in search before this
  ticket closes

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_recording.py -v
pytest tests/e2e/test_terminal_capture_e2e.py -v
```

Passes without a real device. Fake only the audio source and downstream pipeline in
lifecycle tests; SRA-203's e2e test already proves the downstream behavior. The
manual check must use the actual microphone, Whisper, pipeline, and backend.

## Manual verification

- [ ] On macOS with the server running, `pip install -e ".[dev,desktop]"` then
      `python3 -m sra.capture.recording`. Press Enter to start, say "Today I
      studied mitosis, the phases of cell division," press Enter to stop. Confirm
      processing feedback then the saved IDs/topics.
- [ ] `curl -fsS "http://127.0.0.1:8000/api/v1/search?q=mitosis" | python3 -m
      json.tool` returns the IDs just printed — adjust the term to the actual
      returned topic. Seeing only an older matching note fails this check.
- [ ] Inspect the path printed in `Processing recording: /…/sra-recording-….wav`,
      then `test ! -e "/printed/path.wav"` after exit to confirm cleanup. Run a
      second recording to verify microphone reacquisition.
- [ ] Cancel while recording: no submission, device closed, file cleaned up.
      Repeat with denied microphone permission and an unavailable backend; confirm
      clear errors and a successful next capture after recovery.
- [ ] Speak a two-topic note and confirm both entries are searchable.

| Resource or result | Owner | Lifetime / handoff |
|---|---|---|
| Microphone stream | `Recorder` | Acquired on start; released on stop or cleanup, including partial failures. |
| Temporary WAV | `Recorder` | Created for one capture; readable throughout processing; removed on close/context exit. |
| Borrowed WAV path | Pipeline and transcription | Read without deleting or taking ownership. |
| Transcript and entries | Pipeline caller | Submitted once; the backend's actual result is displayed. |

> **Note:** `stop()` and `close()` cannot be the same operation — deleting the WAV
> at stop would leave transcription with a missing input. A timeout retains
> SRA-201's uncertain-save message: deleting the local recording does not cancel
> or roll back a server write.

### Verification record

- **2026-10-14 automated:** 38 focused recorder/terminal tests passed; 146
  completed-ticket tests passed; 91 Phase 1 regressions passed. Full default suite:
  **239 passed, 15 failed, 1 skipped** — all failures SRA-205 `NotImplementedError`
  stubs, the skip opt-in real Whisper. Ruff and Pyright pass.
- E2E tests verify exact newly persisted IDs, PCM contents, silence,
  cancellation, and a lost response after persistence. Hardware and model inference
  are fake in those checks.
- **Environment:** macOS 26.6.2, Python 3.13, agent command runner, with
  sounddevice 0.5.6 and PortAudio 19.7.0 installed. **Device enumeration returned no
  audio devices.** The real module entry point was run with Enter-to-start in this
  environment: it exited with code 1, reported `Error querying device -1` with
  device/permission guidance, and left its temporary directory empty.
- **Dependency verification:** a clean Python 3.13 environment installed the lockfile
  plus `.[dev,desktop]` and passed `pip check`; reinstalling the updated lockfile
  offline passed. The shared dev environment has an unrelated pre-existing
  `google-genai`/`tenacity` conflict, left unchanged.
- **Real-device acceptance pending:** spoken notes, new live/search IDs, physical
  permission denial/recovery, microphone reacquisition, and observed real-capture
  cleanup cannot be recorded without an accessible input device. No live microphone
  recording or external note submission is claimed.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).