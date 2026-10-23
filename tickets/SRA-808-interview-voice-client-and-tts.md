# SRA-808 — Interview voice client and TTS integration

**Phase:** 8 — Interactive AI interview mode · **Status:** implemented

## Summary

Adds the macOS client side of the interview feature: three methods on `src/sra/client/api_client.py` (`start_interview_attempt`, `send_interview_turn`, `get_interview`) and a new `run_interview(quiz_set_id)` driver in `src/sra/capture/voice_quiz_client.py` that composes the existing recorder, transcriber, and `speak()` boundary into one function. The driver speaks each question, records and transcribes the answer, posts `action="answer"` to the SRA-805 routes, and reads back feedback and the next question through the same `speak()` boundary. On `finished=true` it speaks the summary and returns `{score, answered_count, remaining_count}`. Client only — no new TTS module; `src/sra/capture/tts.py` is reused.

## Unlocks

- A user with a microphone and the macOS client running hears the interviewer ask a question, answers out loud, and receives spoken feedback plus the next question — without touching the browser.
- The same conversation is available hands-free through the menu-bar entry, completing the dual-transport (web + voice) commitment: the desktop is a true "live interview" surface.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/client/api_client.py` | edit | `start_interview_attempt(quiz_set_id, *, credential=None)`, `send_interview_turn(attempt_id, action, answer_text=None, *, credential=None)`, `get_interview(attempt_id, *, credential=None)`, following the SRA-506 contract: every call wrapped in `_create_http_client()` so sockets close on every path; `401` → `auth_required`, `429` → `rate_limited` (with `retry_after` when `Retry-After` is returned), `409` → `attempt_completed`, other `4xx`/`5xx` → `_http_error` preserving status + FastAPI `detail`. |
| `src/sra/capture/voice_quiz_client.py` | edit | `run_interview(quiz_set_id, *, api_client=None, recorder=None, transcriber=None)` composing `record_audio()` + `transcribe()` + `tts.speak()` + the three new client methods. Speaks the question truncated to 280 chars + ellipsis, prints the full question to the terminal, speaks feedback + next question until `finished=True`, then speaks the summary and reads `plan.outline` via `get_interview(attempt_id)` for `remaining_count = max(len(outline) - answered_count, 0)`. Returns `{score, answered_count, remaining_count}`. The existing `_VoiceQuizError` carries the failure code (`recorder_failed` / `transcription_failed` / `attempt_completed` / `server_error`). |
| `src/sra/capture/menubar_app.py` | edit | A "Start interview" `MenuItem` at the **end** of the menu list (after `cancel_sign_in_item`) so existing test indices stay stable. `begin_interview` → `_start_interview` → `_complete_interview` follows the dispatcher / worker / dispatcher pattern of `begin_sign_in`. |
| `src/sra/capture/tts.py`, `recording.py`, `transcription.py` | read only | `speak()` takes question, feedback, summary; `record_audio()` and `transcribe(audio_path)` use the same defaults and `WHISPER_MODEL_SIZE` as the Phase 5 driver. No new TTS, recorder, or transcriber code. |
| `tests/unit/test_api_client.py`, `test_voice_quiz_client.py` | edit | 14 new client cases (POST body + bearer header per method, `answer_text` present/absent, the GET, every `4xx`/`5xx`, credentials-only-in-header, module exports) and 5 new driver cases (speak-then-record, 280-char truncation, the three return values, `send_interview_turn(attempt_id, "answer", answer_text)`, module scope). |
| `tests/e2e/test_phase8_voice_interview_e2e.py` | new | Real `create_app()` + TestClient + patched `interview_planner` + scripted `InterviewDecision`, driving `start → 2 answers → finished=True` and asserting the recorded TTS sequence. |

## Design notes

- **Truncate the spoken question to 280 chars.** The user waits for the full speech before the recorder starts; the cap keeps each round-trip under ~25 s and matches the boundary `voice_quiz_client.py` already uses for MCQ feedback. The full question goes to the terminal so nothing is lost.
- **No artificial pause** between feedback and the next question — speak, record, transcribe, speak again is one continuous loop, matching the Phase 5 experience.

## Acceptance criteria

- [x] `start_interview_attempt`, `send_interview_turn`, `get_interview` are exported from `sra.client.api_client` and follow the SRA-506 contract
- [x] `run_interview(quiz_set_id)` returns `{score, answered_count, remaining_count}` after driving a multi-turn conversation to completion
- [x] The spoken question is truncated to 280 chars with an ellipsis; the full question is printed to the terminal
- [x] The recorder and transcriber are called with the same defaults the Phase 5 voice-quiz driver uses
- [x] A `4xx` / `5xx` from the SRA-805 routes raises `ApiClientError` and stops the driver
- [x] The `menubar_app` "Start interview" item calls `run_interview` and surfaces the saved / error status through the existing menu-bar UI
- [x] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pip install -e '.[dev,desktop]' -c requirements-lock.txt
pytest tests/unit/test_api_client.py -v
pytest tests/unit/test_voice_quiz_client.py -v
pytest tests/e2e/test_phase8_voice_interview_e2e.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

`tests/e2e/test_phase5_voice_quiz_e2e.py` is the closest analogue for the fake-audio / fake-transcriber seam.

## Manual verification

With a real OpenAI key, the macOS desktop client installed, and the SRA-805 routes in place:

- [ ] Launch the menu-bar app, choose "Start interview" on a quiz set from today's digest, and confirm the first question is spoken (truncated to 280 chars if long) with the full text in the terminal. Speaking an answer produces spoken feedback and the next question.
- [ ] "Next question" via the SRA-807 dashboard skips the current outline item; ending the interview speaks the summary and updates the menu-bar status. A second Mac with the same account sees the same conversation (the DB is canonical). Disabling the network mid-interview surfaces a clean error, and the transcript recovers through `GET /interview` once it returns.

**Verification (2026-10-15):**

- Isolated implementation session; no live OpenAI call — every Phase 8 case exercises the LLM seam through `_get_chat_model` monkey-patches.
- `tests/unit/test_api_client.py` → **82 passed** (14 new SRA-808 cases: POST body + bearer header per method, `send_interview_turn` with/without `answer_text`, the GET endpoint, `test_interview_client_methods_raise_typed_error_on_4xx_or_5xx[400/401/403/404/409/422/429/500]`, credentials only in the bearer header, module-exports assertion).
- `tests/unit/test_voice_quiz_client.py` → **16 passed** (5 new SRA-808 cases). `tests/e2e/test_phase8_voice_interview_e2e.py` → **1 passed**.
- Regression `pytest -m 'not phase8' -q --ignore=…` → **785 passed / 24 failed / 14 skipped / 153 deselected** (identical to the SRA-605 / SRA-801 / SRA-803 / SRA-804 / SRA-805 / SRA-806 baselines; no new regressions).
- Hand-driven macOS loop (TTS sequence, recorder lifecycle, menu-bar status, error recovery, two-Mac availability): **pending** — this runner exposes zero audio devices.
- `ruff check .` clean; `pyright` **10 errors, all pre-existing** (`interview_planner.py:198` SRA-802, `api/routers/dev.py` SRA-701, 7 in `test_auth_client.py`). The three SRA-808-modified files each run 0 errors, 0 warnings, 0 informations.
- **Deviations:** (1) no "Start voice quiz" menu item exists (SRA-508 never added one), so the interview item lands at the END of the menu list; a parallel voice-quiz item is a future UX ticket. (2) `_select_quiz_set_id` returns `None` until a future ticket wires the selection, so clicking "Start interview" shows "No quiz set available for an interview yet" rather than guessing an id that would 404. (3) The pre-written e2e fixture used `question_text=` for scripted `InterviewDecision` dicts, but `InterviewDecision` requires `next_question_text`; the two scripted decisions were renamed to the LLM-shape field.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).