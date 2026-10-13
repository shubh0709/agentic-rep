# SRA-204 — Microphone capture through the working pipeline

**Type:** Task
**Phase:** 2 — Voice capture
**Priority:** High — first live voice-to-study-entry flow
**Status:** TDD scaffold ready — user implementation pending
**Blocked by:** SRA-203
**Blocks:** SRA-205

## TDD starting point

The assistant has supplied this ticket's tests, typed stubs, and teaching docstrings. **You write the learning-function implementations.** See the [test-first guide](../tests/README.md) for the unit/e2e boundary and implementation order. Focused tests begin red at `NotImplementedError`; after implementing this ticket they must pass. Keep Phase 1 regressions green with `pytest -m 'not phase2' -q`. Later exercises remain visibly red until implemented; the complete default suite must be green for SRA-206. The expected passing results below describe the implemented ticket, not the current scaffold.

## Summary

Build the microphone recording portion of plan U5 and wire it directly into `process_recording`. Use a small terminal entry point to prove live capture before introducing the menu-bar event loop and global hotkey.

## Where things stand right now

SRA-203 processes saved audio end to end, but the user still has to provide a recording. The plan originally put microphone capture and desktop UI in one module. Extracting the recorder here lets a developer prove actual device access, WAV finalization, and cleanup through the working ingestion flow before adding UI callbacks.

## What this ticket unlocks

**Speak into the microphone → stop with Enter → local transcription → stored entries → search.** This is the same recorder and pipeline the menu-bar app will use. The terminal entry point is a small verification surface for the planned recording behavior.

## Files

- New: `src/sra/capture/recording.py`
- New: `tests/unit/test_recording.py`
- End to end: `tests/e2e/test_terminal_capture_e2e.py` (real recorder/CLI/pipeline/API; controlled PCM and inference)
- `pyproject.toml`, `requirements-lock.txt`, `README.md`

## What to implement

- Implement the supplied typed recorder interface against its teaching docstrings and tests using `sounddevice`. Expose `start()`, `stop() -> Path`, and `close()` on a recorder/context manager. `stop()` closes the stream and finalizes a valid WAV; its path remains usable until cleanup.
- Own a unique temporary file per recording. Always close the device and delete recorder-owned audio on `close()` / context exit, including capture failure, pipeline failure, interruption, and normal completion. Do not delete a recording before the pipeline has finished consuming it.
- Keep start/stop separate so the next ticket can drive them from hotkey events. Handle invalid state transitions without opening a second stream. Expose a useful typed `RecordingError` for permission/device/capture failures. Importing the module must not open a device.
- Add a guarded `python3 -m sra.capture.recording` entry point: Enter starts, Enter stops, then call the real pipeline once. Print recording/processing status and saved IDs/topics, or an explicit no-speech/error result. A failure exits unsuccessfully; cancellation cleans up without submitting unfinished audio. Preserve uncertain-save messaging on HTTP timeouts.
- Add a macOS `desktop` extra that includes the existing capture dependencies plus recording dependencies. SRA-205 extends it with UI/hotkey libraries. Document actual microphone setup and permissions for the launch environment.
- Test recorder lifecycle and the terminal-to-pipeline handoff using a fake audio stream and temporary directory. Assert a readable WAV with the captured frames, one completed recording passed to the pipeline, and cleanup on success/error/interruption. Real device behavior remains a required manual check.

## Where to look

- Plan R1/R2 and U5: the `sounddevice` recording and temporary-file responsibilities originally assigned to `menubar_app.py`.
- SRA-203's pipeline and file-ownership contract.
- The next ticket's UI needs: start/stop operations, a finalized path, and deterministic cleanup.

## Acceptance criteria

- [ ] Starting and stopping actual microphone capture produces a valid WAV that the existing pipeline can transcribe
- [ ] The terminal entry point drives capture → pipeline → server and reports saved IDs/topics only after successful ingestion
- [ ] A completed recording is processed once; cancellation does not submit unfinished audio
- [ ] Audio remains available throughout processing and is removed afterward, with the microphone closed on completion, error, and interruption
- [ ] Permission/device/server failures have useful messages; the next attempt succeeds after recovery
- [ ] The manual flow finds the exact newly reported IDs in search before this ticket closes

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_recording.py -v
pytest tests/e2e/test_terminal_capture_e2e.py -v
```

Expected: all tests pass without a real device. Fake only the audio source and downstream pipeline in lifecycle tests; SRA-203's e2e test already proves the downstream behavior. The manual check below must use the actual microphone, Whisper, pipeline, and backend. Finish with completed-ticket and Phase 1 checks, lint, and type check; inspect remaining full-suite failures.

## Manual verification

On macOS with the Phase 1 server running:

```bash
pip install -e ".[dev,desktop]"
python3 -m sra.capture.recording
```

1. Press Enter to start, say "Today I studied mitosis, the phases of cell division," then press Enter to stop. Confirm processing feedback followed by the saved IDs/topics.
2. Search the running server and compare the result IDs to the IDs just printed:

   ```bash
   curl -fsS "http://127.0.0.1:8000/api/v1/search?q=mitosis" | python3 -m json.tool
   ```

   Adjust the search term to the actual returned topic if needed. Seeing only an older matching note fails this check.
3. Confirm the recorder-owned temporary file is removed after processing and that a second recording can acquire the microphone. Provide a documented way to inspect the owned path during verification, such as debug output.
4. Cancel while recording; confirm no submission, device closure, and cleanup. Repeat with denied microphone permission and an unavailable backend. Confirm clear errors and a successful next capture after restoring permission/server access.
5. Speak a two-topic note and confirm the returned entries are searchable. This exercises the same downstream flow as the saved-file check with actual newly captured audio.

## Verification record

- Date, macOS version, launch environment, and microphone:
- Spoken note, created IDs, and matching search IDs:
- Owned temporary path and observed cleanup:
- Cancellation, permission/server failure, and recovery results:
- Focused/full test, lint, and type-check results:

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Real microphone → pipeline → API → search flow and failure recovery recorded above
- [ ] Recorder lifecycle tests pass and the reusable recorder is ready for SRA-205
- [ ] Focused/completed-ticket tests, Phase 1 regressions, `ruff check .`, and `pyright` pass (all default tests must pass by SRA-206); microphone setup documented
