# SRA-505 — Voice quiz router endpoint

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Replace the Phase 4 router's typed `400` rejection of `mode: "voice"` with a real
voice-attempt flow. `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "voice"}`
starts an attempt and returns the first question text alongside the `attempt_id`.
A new `POST /api/v1/quiz/attempts/{attempt_id}/voice-turn {"answer_text": "..."}`
route runs one turn through `quiz_conversation_graph.process_voice_turn` and
returns the next question or the final score. This is the smallest Phase 5 router
ticket: it adds two routes, removes one rejection branch, and adds two
request/response DTOs to `api/schemas.py`. All grading and persistence happens
inside the graph from SRA-504; the router is HTTP plumbing.

## Unlocks

- The desktop voice quiz client (SRA-508) and the extended client API (SRA-506)
  both get real endpoints to call, so a full voice attempt becomes `curl`-drivable
  without any audio.
- `POST /api/v1/quiz/{quiz_set_id}/attempts` now returns the first question inline,
  so a client can `speak()` it without a second round-trip.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/api/schemas.py`](../src/sra/api/schemas.py) | edit | Add `VoiceTurnRequest { answer_text: str }` (strips whitespace, empty answer rejected) and `VoiceTurnResponse { is_correct: bool \| None, feedback_text: str, next_question_text: str \| None, finished: bool, score: int \| None }`. `is_correct` is `None` only for the final turn, which has no answer to grade. `feedback_text` is `"Correct."` / `"Incorrect."`, or a one-sentence explanation when `QuizQuestion.explanation` is set, then the next question's text if `not finished`, else `"You scored N out of M."`. |
| [`src/sra/api/routers/quiz.py`](../src/sra/api/routers/quiz.py) | edit | Remove the `mode: "voice"` rejection in `start_quiz_attempt`; create the attempt and return the first `QuizQuestion` (ordered by `order_index` ascending) inline as `{attempt_id, question_text, options}`. Add `POST /attempts/{attempt_id}/voice-turn`, which calls `process_voice_turn(...)` and returns `VoiceTurnResponse`. Both routes reuse the existing `_get_owned_quiz_set` / `_get_owned_attempt` helpers. `submit_quiz_answer` and `complete_quiz_attempt_route` keep their web-only `VOICE_UNSUPPORTED` rejections. |
| [`tests/unit/test_quiz_routes.py`](../tests/unit/test_quiz_routes.py) | edit | Tests for the new routes: voice start, voice turn correct, voice turn wrong, voice turn last-question, cross-user `403`, completed-attempt `409`, LLM-interpretation error `422`. |
| [`tests/e2e/test_phase5_voice_quiz_e2e.py`](../tests/e2e/test_phase5_voice_quiz_e2e.py) | new | End-to-end test through a real `create_app()` instance with the LLM seam patched. |
| [`tests/integration/test_route_policy_audit.py`](../tests/integration/test_route_policy_audit.py) | edit | Register `POST /api/v1/quiz/attempts/{attempt_id}/voice-turn` in the policy dict with `policy="user"` and `limit=limit_authenticated`, matching the rest of the quiz router. |

## Acceptance criteria

- [x] `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "voice"}` returns `201 {attempt_id, question_text, options}` for an owned quiz set; `403` for a foreign quiz set
- [x] `POST /api/v1/quiz/attempts/{id}/voice-turn {"answer_text": "..."}` returns `200 VoiceTurnResponse`; `400` when the attempt is web-mode; `409` when the attempt is already completed; `403` for a foreign attempt
- [x] Each `voice-turn` call records exactly one `QuizAnswer` row; the attempt's `cursor` advances; on the final question the attempt's `completed_at` is set and `score` matches the correct-answer count
- [x] The route-policy audit covers the new `/voice-turn` route under the same `user` policy + `limit_authenticated` as the rest of the quiz router
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_routes.py -v
pytest tests/e2e/test_phase5_voice_quiz_e2e.py -v
pytest tests/integration/test_route_policy_audit.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

`VOICE_UNSUPPORTED` stays defined for the branches that still raise it — only the
voice branch of `start_quiz_attempt` stops rejecting.

## Manual verification

- [ ] With a running server, `POST /api/v1/quiz/${QUIZ_SET_ID}/attempts {"mode":"voice"}` returns `{attempt_id, question_text, options}`, then each `POST /api/v1/quiz/attempts/${ATTEMPT_ID}/voice-turn {"answer_text":"the second one"}` returns `{is_correct, feedback_text, next_question_text, finished, score}`; the last turn returns `{finished: true, score: N}`.

**Verification:**

- **Date/environment:** 2026-10-14, local dev (Python 3.13.12, pytest 9.1.1, ruff 0.x, pyright).
- `pytest tests/unit/test_quiz_routes.py tests/e2e/test_phase5_voice_quiz_e2e.py tests/integration/test_route_policy_audit.py -v` → 33 passed.
- `pytest -m 'not phase5' -q` → 479 passed / 13 pre-existing Phase 4 failures / 14 skipped (the SRA-502 baseline, minus 3 obsolete voice-rejection tests deleted by this ticket).
- `ruff check .` → All checks passed. `pyright` → 0 errors, 0 warnings, 0 informations.
- New surfaces: `VoiceTurnRequest` / `VoiceTurnResponse` in `api/schemas.py`; `start_quiz_attempt` voice branch plus `POST /attempts/{attempt_id}/voice-turn` in `api/routers/quiz.py`, mapping `QuizVoiceInterpretationError` → `422`, `QuizAttemptAlreadyCompletedError` → `409`, wrong mode → `400`.
- Graph seams extended by this ticket: `_interpret_answer` now accepts dict-shaped structured output so unit-test fakes returning `{"selected_option_index": n}` work; `process_voice_turn` persists `attempt.score` on every non-finishing turn; `_expose_public_symbols_on_test_fixture` also binds the public symbols onto the e2e fixture.
- Failure/recovery: the Phase 5 voice-interpreter fakes returned a fresh scripted model on every `_get_chat_model()` call, resetting the script between turns — `_patch_voice_interpreter` in both test files now builds the fake once and reuses it. `test_voice_turn_completed_attempt_returns_409` seeded a 2-question set, so one turn never finished; it now seeds a 1-question set so the first turn finishes and the second hits `409`.
- Limitation: the three removed Phase 4 voice-rejection tests are replaced by 11 new `phase5` tests; the `not phase5` failures are the documented SRA-404/405/406 limitations, not regressions.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
