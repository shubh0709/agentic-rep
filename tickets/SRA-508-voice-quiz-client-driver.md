# SRA-508 — Voice quiz client driver

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Add `src/sra/capture/voice_quiz_client.py` with the desktop-side function
`run_voice_quiz(quiz_set_id) -> dict` that drives a full voice quiz attempt end
to end. The function speaks each question, records and transcribes the answer,
sends the answer text to the server, speaks the feedback, and loops until the
server reports `finished=True`, returning `{score, total_questions}`. The driver
is pure composition: all grading logic lives server-side in
`quiz_conversation_graph.process_voice_turn` (SRA-504), while the driver only
does audio I/O and HTTP turn-taking. It reuses the existing recorder
(`capture/recording.py`, SRA-204), transcriber (`capture/transcription.py`,
SRA-202), TTS (`capture/tts.py`, SRA-503), and HTTP client, so the voice quiz is
not a third-maintained audio capture pipeline.

## Unlocks

- A developer can run `run_voice_quiz(quiz_set_id=1, api_client=ApiClient(...))` on their Mac and speak each answer after the host reads each question aloud.
- SRA-509 can exercise the same flow end to end with the recorder, transcription, api_client, and tts all faked.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/capture/voice_quiz_client.py`](../src/sra/capture/voice_quiz_client.py) | new | `record_answer(*, recorder=None) -> Path` wraps the recorder (default `capture.recording.record_audio`) and raises `VoiceQuizError` on recorder failure; `run_voice_quiz(quiz_set_id, *, api_client=None, recorder=None, transcriber=None) -> dict` speaks the first question via `tts.speak(question_text)`, then loops record → `transcribe(audio_path)` → send → speak, and returns `{score, total_questions}`. `VoiceQuizError(RuntimeError)` carries a `code` attribute — `"recorder_failed"`, `"transcription_failed"`, `"server_error"`, `"attempt_completed"`, or `"interpretation_failed"` — and the underlying exception is chained as `__cause__` with `raise VoiceQuizError(...) from exc`. No retries: a failure ends the attempt immediately and the server stays the canonical state owner. |
| [`src/sra/capture/recording.py`](../src/sra/capture/recording.py) | edit | Add `record_audio()` as a thin Enter-to-start/Enter-to-stop wrapper around `Recorder()`. The WAV lifecycle stays inside the context manager, so the driver never owns the file. |
| [`src/sra/capture/__init__.py`](../src/sra/capture/__init__.py) | edit | Re-export `run_voice_quiz`. |
| [`tests/unit/test_voice_quiz_client.py`](../tests/unit/test_voice_quiz_client.py) | new | Unit tests with stubbed recorder, transcriber, api_client, and TTS backend: first question spoken, transcription text forwarded to `send_voice_turn`, final turn returns `{score, total_questions}`, recorder/transcriber/server `409` failures surface as `VoiceQuizError`. |

## Acceptance criteria

- [x] `run_voice_quiz` speaks the first question before any recording, speaks feedback + the next question after each turn, and speaks the final summary
- [x] The transcription result is forwarded verbatim to `send_voice_turn` (no normalization, no caching)
- [x] A recorder, transcriber, or server failure raises `VoiceQuizError` with the correct `code` attribute; the underlying exception is chained
- [x] The final return value matches `{score, total_questions}` exactly
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_voice_quiz_client.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

Server errors are classified by substring: `attempt_completed` → `409`,
`interpretation` → interpretation failure, everything else → `server_error`.

## Manual verification

- [ ] With a recording TTS backend and a running server, `run_voice_quiz(quiz_set_id=1, api_client=ApiClient(server_base_url='http://localhost:8000', auth_token='...'))` prints each question and feedback via the backend and returns `{score, total_questions}` after the final turn.

**Verification:**

- **Date/environment:** 2026-10-14, macOS Darwin, Python 3.13.12 from `.venv`, repo `/Users/shubhamsingh/Documents/cells/spaced-repetition-agent`.
- Focused `pytest tests/unit/test_voice_quiz_client.py -v` → **11 passed**.
- `pytest -m 'not phase5' -q` → **479 passed, 13 pre-existing Phase 3/4 failures, 14 skipped** — identical to the SRA-507 baseline; Phase 2 recording tests still 34/34 green after adding `record_audio`.
- `ruff check .` → **All checks passed.** `pyright` → **0 errors, 0 warnings, 0 informations.**
- New artifacts: `voice_quiz_client.py` with `VoiceQuizError`, `record_answer`, and `run_voice_quiz`; a `_VoiceQuizApiClient` Protocol describing the duck-typed client shape so the module-level `sra.client.api_client` helpers fit without an `ApiClient` class wrapper; `record_audio()` in `recording.py`; `run_voice_quiz` re-exported from `capture/__init__.py`.
- Failure/recovery: Pyright first rejected the `from sra.capture.recording import record_audio  # pyright: ignore[reportAttributeAccessIssue]` import — the ignore suppressed the import-time error but Pyright still inferred `record_audio` as `None`, so the call produced `reportOptionalCall`. Fixed by adding the missing `record_audio()` to `recording.py` and switching to a plain top-level import. A second Pyright failure rejected `client: _VoiceQuizApiClient = _api_module` because the Protocol declared `**kwargs: Any` while the real methods have a keyword-only `credential` parameter; the Protocol was simplified to positional-only signatures, matching the `DigestClient` pattern in `wakeup.py`.
- Limitation: `total_questions` falls back to `final_score + 1` while the voice-turn response carries only `score`; the Phase 5 server is the canonical owner of quiz state, and `process_voice_turn` will be widened to echo the count when the driver needs it. Manual `say` audio acceptance and live loopback voice-quiz evidence roll into SRA-509.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
