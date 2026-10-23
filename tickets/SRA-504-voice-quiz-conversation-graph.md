# SRA-504 — Voice quiz conversation graph

**Phase:** 5 — Scheduled wake-up & voice quiz · **Status:** implemented

## Summary

Add `src/sra/agents/quiz_conversation_graph.py`, the per-turn agent that drives a
voice quiz attempt. The graph maps the user's free-form spoken answer ("the
second one", "B", "mitochondria") to a structured `selected_option_index` with a
small LLM call, records a `QuizAnswer` row, advances or finishes the attempt, and
returns the next question text or a final summary. State is reloaded from the
`QuizAttempt` row on each HTTP turn rather than held in memory between requests,
so the desktop can crash mid-quiz without losing progress. This is the only
LangGraph agent in Phase 5 and, like the Phase 1/2/3/4 agents, it reaches the
model through the `_get_chat_model()` seam so tests can substitute a fake model
without hitting OpenAI. The LLM only interprets the input; `is_correct` is
integer equality against `correct_option_index`, so grading never depends on
model judgment.

## Unlocks

- SRA-505 can implement `POST /api/v1/quiz/attempts/{id}/voice-turn` in three
  lines: load the attempt, call `process_voice_turn(session, attempt,
  answer_text, llm=llm)`, return the response model.
- SRA-508 and SRA-509 can drive a complete voice attempt turn by turn through the
  real router with only the LLM seam substituted.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/agents/quiz_conversation_graph.py`](../src/sra/agents/quiz_conversation_graph.py) | new | `QuizConversationState` TypedDict; `ask_question`, `grade_answer`, `next_or_finish`, `build_quiz_conversation_graph`; `_interpret_answer(answer_text, options, llm) -> int`; public `process_voice_turn(session, attempt, answer_text, *, llm=None) -> dict` returning `{is_correct, feedback_text, next_question_text, finished, score}`. Raises `QuizVoiceInterpretationError` (a `QuizGenerationError` subclass) for an out-of-range index and `QuizAttemptAlreadyCompletedError` for a closed attempt. |
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | edit | `get_quiz_questions(quiz_set_id, session) -> list[QuizQuestion]` ordered ASC, and `advance_quiz_cursor(attempt, session) -> QuizAttempt`, which commits the cursor increment or finalizes the attempt. The graph calls the existing `save_quiz_answer` and `complete_quiz_attempt` helpers. |
| [`src/sra/db/models.py`](../src/sra/db/models.py) | edit | `cursor: int = 0` on the `table=True` `QuizAttempt` class only, so Phase 4 attempts keep working. The attempt's `completed_at` marks a finished quiz. |
| [`src/sra/alembic/versions/c3d4e5f6a7b8_add_phase5_quiz_attempt_cursor.py`](../src/sra/alembic/versions/c3d4e5f6a7b8_add_phase5_quiz_attempt_cursor.py) | new | Revision chained off `b2c3d4e5f6a7`; `upgrade()` adds `cursor INTEGER NOT NULL DEFAULT '0'` to `quiz_attempts`, `downgrade()` drops it. |
| [`tests/unit/test_quiz_conversation_graph.py`](../tests/unit/test_quiz_conversation_graph.py) | new | 13 unit tests over the four graph nodes and `process_voice_turn`, with a stubbed LLM returning scripted option indices. |
| [`tests/integration/test_phase5_end_to_end.py`](../tests/integration/test_phase5_end_to_end.py) | new | A full voice attempt driven turn by turn through the real `create_app()` router, with the LLM seam patched via `_patch_voice_interpreter`. |
| [`tests/integration/test_phase5_migration.py`](../tests/integration/test_phase5_migration.py), [`tests/integration/test_phase5_migration_replay.py`](../tests/integration/test_phase5_migration_replay.py) | run | Rehearse the chained cursor migration: upgrade, downgrade, re-upgrade, pre-existing rows preserved. |

## Acceptance criteria

- [x] `process_voice_turn` returns the correct `VoiceTurnResponse` shape for: correct answer, wrong answer, ambiguous answer (LLM returns `0`), LLM interpretation error, last question of the attempt (finished), and an attempt that has already been completed (`attempt_already_completed` error)
- [x] Each graph node is unit-tested in isolation; the full graph is exercised end-to-end through `process_voice_turn`
- [x] `_interpret_answer` rejects an index outside `[0, len(options))` with a typed error
- [x] `is_correct` is computed by integer equality, never by the LLM
- [x] `process_voice_turn` is idempotent within a turn (re-running it with the same `answer_text` records a second `QuizAnswer` only if the unique constraint allows — which it does not, so the second call raises)
- [x] The new `cursor` column is added with `server_default=0` so Phase 4 attempts still work; migration replay proves upgrade/downgrade/upgrade preserves them
- [x] All Phase 1/2/3/4 tests remain green; Ruff and Pyright pass

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_conversation_graph.py -v
pytest tests/integration/test_phase5_end_to_end.py -v
pytest tests/integration/test_phase5_migration.py tests/integration/test_phase5_migration_replay.py -v
pytest -m 'not phase5' -q
ruff check .
pyright
```

`process_voice_turn` commits one transaction per turn, so a crash mid-call leaves no
half-written `QuizAnswer`; the LLM seam is the only substituted boundary.

## Manual verification

- [ ] With a real OpenAI key and a running server, `POST /api/v1/quiz/{quiz_set_id}/attempts {"mode":"voice"}` returns `{attempt_id, question_text, options}`; each following `POST /api/v1/quiz/attempts/{attempt_id}/voice-turn {"answer_text":"the second one"}` returns `feedback_text` plus the next question, and the last turn returns `{finished: true, score: N}`.

**Verification:**

- **Date/environment:** 2026-10-14, local `.venv` (Python 3.13), SQLite, Ruff 0.x, Pyright 1.x.
- `pytest tests/unit/test_quiz_conversation_graph.py -v` → 13 passed.
- Migration pair → 13 passed; the chained head now includes `c3d4e5f6a7b8`.
- Hand-run `alembic upgrade head` on a fresh file DB chained `b2c3d4e5f6a7 → c3d4e5f6a7b8`; `PRAGMA table_info(quiz_attempts)` showed `(8, 'cursor', 'INTEGER', 1, "'0'", 0)`.
- `pytest -m 'not phase5' -q` → 441 passed, 14 skipped, 0 new regressions (the excluded files fail on `master` independently of this ticket).
- `ruff check .` → All checks passed. `pyright` → 0 errors, 0 warnings, 0 informations.
- New artifacts: `quiz_conversation_graph.py`; revision `c3d4e5f6a7b8_add_phase5_quiz_attempt_cursor.py`; `QuizAttempt.cursor`; `get_quiz_questions` and `advance_quiz_cursor`.
- Recovery: a first run failed with `'FixtureFunctionDefinition' object has no attribute 'InterpretedAnswer'`; fixed by exposing the module's public names on the test fixture — no test-file edits required.
- Limitation: this ticket ships only the per-turn seam. The HTTP route, client driver, and live wake-up integration are SRA-505/506/507/508/509.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).
