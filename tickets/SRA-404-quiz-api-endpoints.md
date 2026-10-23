# SRA-404 — Quiz API endpoints (web)

**Phase:** 4 — Daily MCQ quiz (web) · **Status:** not started

## Summary

Wire the quiz router into the API so a signed-in dashboard can list today's
quizzes, fetch one quiz, start an attempt, submit per-question answers, complete an
attempt, and read the attempt summary. The router sits behind the same `require_user`
dependency as the other authenticated routes and joins the same `limit_authenticated`
bucket. **Voice mode** is intentionally not implemented here — that is Phase 5's
`quiz_conversation_graph` plus a `voice-turn` endpoint. `StartAttemptRequest.mode` is
accepted and validated today, but only `"web"` succeeds at the route boundary. This
ticket does **not** touch the dashboard; SRA-405 ships the JavaScript that consumes
these endpoints.

## Unlocks

- A `curl` run against a live server completes the entire web-quiz loop.
- SRA-405's `quiz.js` has a fixed, documented contract to code against.
- Every Phase 5 route sits beside these six and inherits the same ownership and rate-limit policy.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/api/schemas.py` | edit | `QuizQuestionOut` (id, question_text, options, order_index — **no** `correct_option_index`), `QuizSetOut` (id, entry: EntryOut, generated_on, questions), `StartAttemptRequest` (`mode: Literal["web", "voice"]`), `SubmitAnswerRequest` (question_id, selected_option_index), `AnswerResult` (is_correct, correct_option_index, explanation \| None), `QuizAttemptOut` (id, quiz_set_id, mode, started_at, completed_at, score, total_questions). |
| `src/sra/api/routers/quiz.py` | new | The six endpoints, using `Depends(require_user)` and `Depends(get_session)` like `entries.py`. A small mapper strips `correct_option_index` from `QuizSet` → `QuizSetOut`. |
| `src/sra/api/routers/__init__.py` | edit | `from sra.api.routers import quiz`, included in the same `include_router(..., dependencies=[Depends(limit_authenticated)])` group as `entries`, `digest`, `search`, and `chat`. Router prefix `/quiz`; `/api/v1` is applied by `create_app()`. |
| `src/sra/db/repository.py` | read (use) | Reuse `get_quiz_set`, `list_today_quiz_sets`, `save_quiz_answer`, `complete_quiz_attempt`, `get_quiz_attempt` — the router does not bypass the repository. |
| `tests/unit/test_quiz_routes.py` | new | Per-route coverage of the payload shapes and the full 401/400/403/404/409 matrix, including `mode: "voice"` → `400` with no row created. |
| `tests/e2e/test_quiz_web_e2e.py` | new | Seeds a user and an entry, runs the SRA-403 pregeneration, then drives the complete loop through the real router, repository, and SQLite with only the model substituted. |

Routes: `GET /api/v1/quiz/today`; `GET /api/v1/quiz/{quiz_set_id}`;
`POST /api/v1/quiz/{quiz_set_id}/attempts`;
`POST /api/v1/quiz/attempts/{attempt_id}/answers`;
`POST /api/v1/quiz/attempts/{attempt_id}/complete`;
`GET /api/v1/quiz/attempts/{attempt_id}`.

> **Note:** `QuizQuestionOut` and `QuizSetOut` must never carry `correct_option_index`
> — the answer key reaches the client only through `AnswerResult` after submission.
> The `mode: "voice"` `400` on the start route is a deliberate placeholder: Phase 5
> removes that branch without changing the schema.

## Acceptance criteria

- [ ] `GET /api/v1/quiz/today` returns the authenticated user's pre-generated sets for today, scoped by `user_id`, with `correct_option_index` stripped from each question
- [ ] `GET /api/v1/quiz/{quiz_set_id}` returns the set for the owner, `404` when missing, `403` when owned by another user, `401` when unauthenticated
- [ ] `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode": "web"}` creates a `QuizAttempt` and returns `{attempt_id, question_text: null}`; the same call with `mode: "voice"` returns `400` and does not create a row
- [ ] `POST /api/v1/quiz/attempts/{attempt_id}/answers` records one `QuizAnswer`, returns `is_correct`, the real `correct_option_index`, and the explanation; rejects `voice` mode with `400`; rejects a completed attempt with `409`; rejects a question that does not belong to the attempt's quiz set with `404`
- [ ] `POST /api/v1/quiz/attempts/{attempt_id}/complete` counts correct answers, stamps `completed_at`, returns `{score, total_questions}`; rejects `voice` mode with `400`; rejects a completed attempt with `409`
- [ ] `GET /api/v1/quiz/attempts/{attempt_id}` returns the attempt summary for the owner, `404`/`403` discipline
- [ ] The new router shares the per-user rate-limit bucket used by `entries`, `digest`, `search`, and `chat`
- [ ] The end-to-end test completes the full web-quiz loop through the real router, repository, and SQLite, with only the model substituted
- [ ] Phase 1/2/3 unit, e2e, and integration tests remain green; Pyright and Ruff pass without new warnings

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_routes.py -v
pytest tests/e2e/test_quiz_web_e2e.py -v
pytest -m 'not phase4' -q
ruff check .
pyright
```

`tests/conftest.py`'s existing `get_session` dependency override is honored by the
mounted router, so the `client` fixture works unchanged.

## Manual verification

- [ ] Run SRA-403's pregeneration once against a real database, then start a server
- [ ] Drive the six-route `curl` loop with `Authorization: Bearer <token>`; record the `attempt_id`, every `is_correct` with its `correct_option_index`, and the final `score` / `total_questions`
- [ ] Confirm `/quiz/today` and `/quiz/{id}` never return `correct_option_index`
- [ ] Confirm a second user's token gets `403` on the first user's quiz, and an unauthenticated request gets `401`
- [ ] Confirm `POST /quiz/{id}/attempts {"mode": "voice"}` returns `400` and inserts no `QuizAttempt` row
- [ ] Confirm a short burst from one user produces `429` at the same threshold as `entries`/`digest`/`search`/`chat`
- [ ] Do not use a production database for the first run

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).