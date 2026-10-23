# SRA-805 — Interview router endpoints

**Phase:** 8 — Interactive AI interview mode · **Status:** implemented

## Summary

Extends `src/sra/api/routers/quiz.py` with the three HTTP routes the interview feature needs: an `interview` branch in `POST /api/v1/quiz/{id}/attempts` that creates the attempt and returns the first question inline, a new `POST /api/v1/quiz/attempts/{id}/interview-turn` driving `process_interview_turn` for `start | answer | next | end`, and a new `GET /api/v1/quiz/attempts/{id}/interview` returning the full plan + transcript for resume and the summary panel. All three inherit the existing per-user rate-limit bucket, enforce ownership through the parent `QuizSet` / `QuizAttempt` rows, and map the SRA-803 typed errors to the same HTTP shapes the Phase 4/5 routes use. HTTP only: the router delegates per-turn logic to the SRA-803 graph and storage to the SRA-804 helpers.

## Unlocks

- SRA-807's dashboard can start an interview, drive a turn, render the summary, and resume after a disconnect; SRA-808's voice client does the same through `start_interview_attempt`, `send_interview_turn`, and `get_interview`.
- Every interview action is one HTTP call away, with the same `400 / 403 / 404 / 409 / 422 / 429` shapes as the rest of the quiz surface.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/api/routers/quiz.py` | edit | Add the `interview` branch to `start_quiz_attempt` (first question inline), plus `interview_turn(attempt_id, body, user, session)` and `get_interview(attempt_id, user, session)`. Reuse `_get_owned_quiz_set` / `_get_owned_attempt`; new helpers `_interview_turn_to_out` / `_attempt_to_out` / `_interview_plan_to_out` map rows into the SRA-806 DTOs. |
| `src/sra/api/schemas.py` | read only | SRA-806 supplies `StartAttemptRequest`, `InterviewTurnRequest` / `InterviewTurnResponse` / `InterviewPlanOut` / `InterviewTurnOut` / `InterviewSummaryOut`, and `QuizAttemptOut` (carrying `cursor_on_plan`). |
| `src/sra/api/deps.py`, `src/sra/agents/interview_conversation_graph.py`, `src/sra/db/repository.py`, `src/sra/api/rate_limit.py` | read only | SRA-805 imports `process_interview_turn` plus the three typed errors, SRA-804's helpers, and `get_session` / `require_user`. No new LLM logic, helpers, or rate-limit code — the routes inherit the router-level `limit_authenticated` bucket from `sra.api.routers.__init__`. |
| `tests/unit/test_quiz_routes.py` | edit | 201 with `question_text` + first transcript row; 200 with feedback + next question (LLM patched); `400` wrong mode; `409` completed attempt; `403` another user's; `401` anonymous; `429` rate limited. `GET /interview`: 200 owner, 403 other, 404 missing. |
| `tests/integration/test_phase8_interview_routes_e2e.py` | new | Real `create_app()`, LLM patched: `start → 2 answers → next → 1 answer → end`; asserts response shapes, persisted `interview_turns`, resume payload. |
| `tests/integration/test_phase8_rate_limit_inheritance.py`, `test_route_policy_audit.py` | new / edit | Shared per-user bucket with existing `/quiz/*` (mirrors SRA-304/405/505); the two new paths join the audit so SRA-302 ownership is re-checked. |

## Design notes

- **Start folds into `POST /attempts`.** Two routes would force two LLM-touching round-trips before the user sees the first question; folding keeps the first turn to one HTTP call and matches Phase 5 voice mode.
- **`GET /interview` is read-only**, so resume is a single fetch and a second tab can poll without writing. The two new routes are added at the end of the file so existing `404` tests for older `/quiz/*` paths keep matching first.
- **Status codes:** `attempt.mode != "interview"` → `400` (same shape as `VOICE_UNSUPPORTED`); `attempt.completed_at is not None` → `409`; `InterviewAttemptAlreadyCompletedError` and `InterviewAttemptFullError` → `409`; `InterviewLLMError` → `422`.

## Acceptance criteria

- [ ] `POST /api/v1/quiz/{id}/attempts {mode: "interview"}` returns 201 with `{attempt_id, question_text, transcript_so_far}` and creates exactly one `QuizAttempt` + one `InterviewTurn` row
- [ ] `POST /api/v1/quiz/attempts/{id}/interview-turn` handles `start | answer | next | end` and returns the typed `InterviewTurnResponse` for each
- [ ] `GET /api/v1/quiz/attempts/{id}/interview` returns 200 with `{plan, turns, summary?, attempt}` for the owner; 403 for another user; 404 for a missing attempt
- [ ] An anonymous request to any of the three new paths returns 401; a rate-limited request returns 429; a wrong-mode request returns 400
- [ ] `InterviewAttemptAlreadyCompletedError` and `InterviewAttemptFullError` map to 409; `InterviewLLMError` maps to 422
- [ ] The new routes share the existing per-user rate-limit bucket with the rest of `/quiz/*` (proven by the inheritance test)
- [ ] The protected-route audit covers the two new paths and the extended `POST /attempts` branch
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_routes.py -v
pytest tests/integration/test_phase8_interview_routes_e2e.py -v
pytest tests/integration/test_phase8_rate_limit_inheritance.py -v
pytest tests/integration/test_route_policy_audit.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

Two focused tests are known-red against pre-existing baselines (the SRA-505 voice-rejection rollback and the SRA-306 `/auth/refresh` audit omission); both are counted in the 24-failure regression baseline.

## Manual verification

With a real OpenAI key and a user who already has a `QuizSet` row:

```bash
TOKEN=$(...) ; QUIZ_SET_ID=$(...)
# 1. Start — returns the first question inline
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"interview"}' \
  "http://127.0.0.1:8000/api/v1/quiz/${QUIZ_SET_ID}/attempts" | python3 -m json.tool
# {"attempt_id": 12, "question_text": "...", "transcript_so_far": [...]}

ATTEMPT_ID=12
# 2. Answer
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"answer","answer_text":"Mitochondria produce ATP via oxidative phosphorylation."}' \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/${ATTEMPT_ID}/interview-turn" | python3 -m json.tool
# {"score": 82, "rationale": "...", "feedback": "...", "question_text": "...",
#  "finished": false, "transcript_so_far": [...], "summary": null}

# 3. End
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"end"}' \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/${ATTEMPT_ID}/interview-turn" | python3 -m json.tool
# {"finished": true, "summary": "...", "score": 78, "transcript_so_far": [...], ...}

# 4. Resume
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/${ATTEMPT_ID}/interview" | python3 -m json.tool
# {"plan": {...}, "turns": [...], "summary": "...", "attempt": {...}}
```

- [ ] Each call returns the documented shape, the transcript grows by one row per turn, and `summary` appears only after the `end` action.

**Verification (2026-10-15, local `.venv` / Python 3.13.12 / pytest 9.1.1; `OPENAI_API_KEY=sk-test` patched at the test seam — no live calls):**

- `tests/unit/test_quiz_routes.py` → **28 passed, 1 failed** (`test_start_attempt_voice_is_rejected_with_400_and_no_row` — pre-existing SRA-505 rollback, out of scope).
- `tests/integration/test_phase8_interview_routes_e2e.py` → **3 passed** (full lifecycle, wrong-mode 400, owns-attempt 403).
- `tests/integration/test_phase8_rate_limit_inheritance.py` → **2 passed** (shared per-user bucket; `limit_authenticated` dependency present).
- `tests/integration/test_route_policy_audit.py` → **1 failed** (pre-existing SRA-306 `('POST', '/api/v1/auth/refresh')` omission, unchanged).
- Regression `pytest -m 'not phase8' -q --ignore=…` → **785 passed / 24 failed / 14 skipped / 153 deselected** — identical to the SRA-804 / SRA-806 baseline.
- Hand-driven curl loop with a real LLM: deferred to SRA-807 / SRA-808; the e2e lifecycle test covers the same assertions.
- `ruff check .` clean; `pyright` **10 pre-existing errors, 0 new** (`interview_planner.py:198`, `api/routers/dev.py:52,178`, 7 in `test_auth_client.py`).
- **Note:** the `interview` start branch stashes `request.app.state.last_attempt_id_for_test = attempt.id` so a unit-level `GET /interview` test can read the most-recent attempt id without a DB query.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).