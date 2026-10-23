# SRA-206 — Phase 2 integration verification

**Phase:** 2 — Voice capture · **Status:** implemented

## Summary

Verify one continuous flow from the real macOS hotkey to recording, local Whisper,
the HTTP client, server-side ingestion, stored entries, review scheduling, search,
and the due-date digest. Earlier tickets add a boundary one at a time; this one
checks product behavior across all of them. No new layer is needed — the output is
evidence and accurate docs.

## Unlocks

- Phase 3 can build real users and login on a foundation where "a spoken note
  becomes persisted knowledge you can search, chat about, and review on the correct
  date" is a proven claim.

## Files

| File | Change | Why |
|---|---|---|
| [`tests/e2e/test_client_ingestion_e2e.py`](../tests/e2e/test_client_ingestion_e2e.py) | run | Text-client → server → saved entries, including the lost-response case. |
| [`tests/e2e/test_voice_capture_to_digest_e2e.py`](../tests/e2e/test_voice_capture_to_digest_e2e.py) | run | Recording → transcript → ingestion → storage/search → next-day review visibility. |
| [`tests/e2e/test_terminal_capture_e2e.py`](../tests/e2e/test_terminal_capture_e2e.py) | run | Recorder/CLI wiring with controlled audio, plus file/device cleanup. |
| [`tests/integration/test_transcription_whisper.py`](../tests/integration/test_transcription_whisper.py) | run | Real Whisper behind the substituted inference boundary. |
| [`PROGRESS.md`](../PROGRESS.md), [`tickets/README.md`](README.md) | edit | Record the phase result; Phase 2 stays **open** until desktop evidence exists. |

## Acceptance criteria
- [x] The full default suite, including Phase 1 and the voice e2e flow, passes with
  no unimplemented Phase 2 behavior left
- [x] The opt-in real Whisper fixture test passes, including a repeat with cached
  weights offline
- [x] `ruff check .` and `pyright` pass
- [ ] The manual desktop flow below succeeds against a running server and creates
  entries for the existing local user
- [ ] Stored entries retain the transcript and have six pending checkpoints each;
  search finds them immediately and the digest shows their first reviews when due
- [x] Empty audio and dependency failures recover cleanly; no audio upload,
  duplicate submission, or temporary-file leak in the checked paths
- [x] Phase status, ticket board, and setup docs reflect verified results and any
  actual limitations
- [ ] Each preceding ticket has its own completed manual record; this pass does not
  substitute for missing per-ticket evidence

## Tests

```bash
source .venv/bin/activate
pytest -q
pytest -q tests/e2e/
SRA_RUN_WHISPER_TESTS=1 pytest tests/integration/test_transcription_whisper.py -v
ruff check . && pyright
```

Also run the desktop smoke test on a Mac with the desktop extra installed. Record
actual results rather than guessing a count. See
[`tests/README.md`](../tests/README.md) for the complete verification commands.

## Manual verification

- [ ] With `OPENAI_API_KEY` available, `uvicorn sra.api.app:create_app --factory
      --reload --port 8000` in one terminal and `python3 -m
      sra.capture.menubar_app` in another. Focus another app, start capture with
      the hotkey, say "Today I covered photosynthesis, including light and dark
      reactions, and mitosis, the phases of cell division." Stop with the hotkey;
      confirm two topics saved.
- [ ] `curl` search for `mitosis`, `POST /api/v1/chat {"question": "what did I
      learn about mitosis?"}`, and `/api/v1/digest`. Verify the new `1d`
      checkpoints when due or via an isolated check that advances the digest date;
      never edit stored review dates.
- [ ] Read-only DB check by returned ID: confirm the original transcribed text,
      local-user ownership, and the six labels `1d`, `3d`, `7d`, `1mo`, `3mo`,
      `6mo`. Pre-existing notes must not count.
- [ ] Exercise no speech, server unavailability, and a failed
      transcription/permission path. For timeouts, check search before resubmitting.
- [ ] Restart the app and repeat a capture; confirm cached transcription works
      with no model download.

> **Note:** for a two-topic note, record the returned IDs and follow those through
> every check. An older "mitosis" result would not establish that this capture
> succeeded, and an empty digest on capture day is expected.
>
> **Evidence layers:** unit tests isolate each boundary's contract; e2e tests prove
> real wiring reaches storage, schedules, and search; the opt-in Whisper fixture
> proves this installation recognizes real audio; only a real hotkey capture proves
> OS input, mic, inference, HTTP, and server inference together — and even that does
> not prove the stored transcript, ownership, review rows, or retrieval by the new
> IDs.

### Verification record

- **2026-10-14, macOS 26.6.2 (build 25G83), Python 3.13.12, project `.venv`.**
  Desktop dependencies import, but `sounddevice` 0.5.6 reports **zero available
  devices** here.
- Default suite **259 passed, 1 skipped** (opt-in Whisper); e2e **16 passed**;
  offline Whisper **1 passed**; complete opt-in run **260 passed**. `ruff check .`
  and `pyright` passed with no findings.
- `make test-data`: **280 passed, 1 deselected**. `make test-live`: **1 passed, 7
  deselected**, including actual OpenAI extraction and chat. Both replay the real
  spoken fixture through simulated device callbacks, production recorder/controller,
  Whisper, actual loopback HTTP, and isolated SQLite — asserting exact returned IDs,
  transcript/ownership, six schedules, search/chat/digest, restart, and cleanup.
- A silent WAV previously produced the hallucinated transcript "You"; enabling
  Whisper's voice-activity filter fixed this **without weakening the test**. Newly
  returned IDs were followed through persistence and search: exact transcript,
  local-user ownership, six pending checkpoints per entry, excluded on capture day,
  returned as `1d` the next day without modifying stored dates. These are automated
  test IDs, not live desktop captures.
- **Remaining manual evidence:** no input device is exposed here, so physical
  microphone capture, cross-application hotkey delivery, native UI feedback,
  permission denial/recovery, restart capture, live OpenAI-backed IDs, chat,
  read-only DB inspection, and live due-date digest verification were not run.
  SRA-204 through SRA-206 and Phase 2 remain open.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).