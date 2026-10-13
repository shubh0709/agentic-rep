# SRA-206 — Phase 2 integration verification

**Type:** Verification (no new feature code expected)
**Phase:** 2 — Voice capture
**Priority:** Highest — the "Phase 2 is actually done" checkpoint
**Status:** To do — planned
**Blocked by:** SRA-205 (transitively: SRA-201 through SRA-204)
**Blocks:** Phase 3 kickoff

The automated test scaffold is now prepared, including
`tests/e2e/test_client_ingestion_e2e.py`,
`tests/e2e/test_voice_capture_to_digest_e2e.py`, and
`tests/e2e/test_terminal_capture_e2e.py`. Learning functions remain for the user
to implement. The expected green results below are the completion criteria;
the present TDD red state is documented in the [test guide](../tests/README.md).

## Summary

Verify one continuous flow from the real macOS hotkey to recording, local Whisper, the HTTP client, server-side ingestion, stored entries, review scheduling, search, and the due-date digest. Close the phase only when both the automated checks and the desktop flow have evidence.

## Where things stand right now

At this point every earlier ticket has already demonstrated a working flow: text client to stored entries (201), real WAV to stored entries (202), the single-call pipeline and due-date checks (203), live microphone capture (204), and the menu-bar hotkey (205). Each has its own recorded manual evidence. This ticket reruns the complete flow against one server and checks regressions, restart behavior, and phase documentation.

## What this ticket confirms

You can capture what you studied by voice during normal desktop use and later retrieve the resulting scheduled entries. The client handles audio locally; the existing API remains responsible for the study data. That is the Phase 2 foundation for adding real users and login in Phase 3.

## Files

- Verification: the tests supplied for SRA-201 through SRA-205, particularly `tests/e2e/test_voice_capture_to_digest_e2e.py`, `tests/e2e/test_terminal_capture_e2e.py`, and `tests/integration/test_transcription_whisper.py`
- Status/documentation: `PROGRESS.md`, `tickets/README.md`, `README.md`, this ticket

## Acceptance criteria

- [ ] The full default suite, including Phase 1 and the new voice e2e flow, passes with no unimplemented Phase 2 behavior left
- [ ] The opt-in real Whisper fixture test passes, including a repeat with cached weights offline
- [ ] `ruff check .` and `pyright` pass
- [ ] The manual desktop flow below succeeds against a running server and creates entries for the existing local user
- [ ] Stored entries retain the transcription result and have six pending checkpoints each; search finds them immediately and the digest shows their first reviews when due
- [ ] Empty audio and dependency failures recover cleanly; no audio upload, automatic duplicate submission, or app-owned temporary-file leak occurs in the checked paths
- [ ] Phase-level status, ticket board, and setup documentation reflect verified results and any actual limitations
- [ ] Each preceding ticket has its own completed manual verification record; this final pass does not substitute for missing per-ticket evidence

## Full test suite

```bash
source .venv/bin/activate
pytest -q
pytest -q tests/e2e/
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
ruff check .
pyright
```

Run the desktop smoke test on a Mac with the desktop extra installed as well. Record actual results rather than guessing a test count. The default suite uses fake inference and an isolated database; the explicit Whisper run and manual flow provide the real audio/model evidence.

## End-to-end manual flow

With `.env` already configured and the server's `OPENAI_API_KEY` available, start the backend:

```bash
uvicorn sra.api.app:create_app --factory --reload --port 8000
```

In another terminal on macOS:

```bash
source .venv/bin/activate
python3 -m sra.capture.menubar_app
```

1. Focus another app, start capture with the hotkey, and say: "Today I covered photosynthesis, including light and dark reactions, and mitosis, the phases of cell division." Stop with the same hotkey.
2. Confirm recording and processing feedback, followed by saved entries for the two topics. If extraction is unexpected, inspect the transcript/result and record the finding before calling the phase complete.
3. Query search and chat using the existing endpoints:

   ```bash
   curl -s "http://127.0.0.1:8000/api/v1/search?q=mitosis" | python3 -m json.tool
   curl -s -X POST http://127.0.0.1:8000/api/v1/chat \
     -H "Content-Type: application/json" \
     -d '{"question": "what did I learn about mitosis?"}' | python3 -m json.tool
   curl -s http://127.0.0.1:8000/api/v1/digest | python3 -m json.tool
   ```

   Search should include the new entry and chat should answer from the stored topic. The digest may contain older due reviews, but a newly captured entry's first checkpoint is due tomorrow. Verify the new `1d` checkpoints when due or through an isolated check that advances the digest date; do not edit the stored review dates to make them appear today.

4. Inspect the new entries and their schedules through a read-only DB check: retain each returned ID, confirm the original transcribed text, the local user's ownership, and the six labels `1d`, `3d`, `7d`, `1mo`, `3mo`, `6mo`. Compare by entry ID so pre-existing notes do not count as evidence for the new capture.
5. Exercise no speech, server unavailability, and a failed transcription/permission path. Confirm no false saved message, no automatic POST retry, resource cleanup, and a successful capture after recovery. For timeouts, check search before resubmitting because the server may already have saved the note.
6. Restart the app and repeat a capture. Confirm configuration is honored and that cached local transcription works without a model download. Verify transcription offline separately; server ingestion still needs the backend and its model service.

## Verification record

Fill in when executing this ticket:

- Date, macOS version, and launch environment:
- Whisper model and fixture/manual transcription outcome:
- Automated test, lint, and type-check results (including skips):
- New entry IDs and observed transcript/schedule/search/digest results:
- Hotkey, error recovery, cleanup, and restart checks:
- Remaining issues, if any:

## After this ticket closes

- [ ] Mark Phase 2 done in `PROGRESS.md`, the README phase list, and `tickets/README.md`
- [ ] Record the actual verification date and evidence in this ticket
- [ ] Phase 3 can begin: Google OAuth, real per-user isolation, RBAC, and rate limiting

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Automated and manual results recorded; any blocking failures fixed and rechecked
- [ ] Phase 2 tickets marked done and documentation matches the working flow
