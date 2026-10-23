# SRA-803 — Interview conversation graph

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds `src/sra/agents/interview_conversation_graph.py`, the per-turn agent driving an interactive interview attempt. Each HTTP turn reloads state from the database, optionally grades the user's free-form answer (0–100 with rationale and feedback), decides whether to **probe deeper** on the current outline item or **move on**, and persists the resulting `InterviewTurn` rows. The graph runs once per HTTP turn rather than start-to-finish in one call, mirroring the Phase 5 voice-quiz agent's two-state design: the database owns canonical state, the graph owns per-turn logic. The LLM seam is the same one `quiz_graph.py` and `interview_planner.py` use, so tests substitute a scripted fake without ever calling OpenAI.

## Unlocks

- SRA-805's route becomes a thin shim: load the attempt, call `process_interview_turn(...)`, serialize. The same module backs the dashboard's typed answers and the macOS client's voice turns.
- Before this ticket no function turns an `InterviewPlan` into a conversation — an attempt cannot advance, persist a turn, or finalize.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/interview_conversation_graph.py` | new | `InterviewTurnRole` StrEnum; `InterviewDecision` Pydantic; `InterviewLLMError` / `InterviewAttemptFullError` / `InterviewAttemptAlreadyCompletedError` (all `QuizGenerationError` subclasses, so the existing Phase 4 error path maps them without special-casing); `_get_chat_model` with `max_completion_tokens=512`, `timeout=15`, `max_retries=0`; `_build_decision_prompt`; `process_interview_turn(*, session, attempt, action, answer_text=None, llm=None) -> InterviewTurnResult`. |
| `src/sra/db/repository.py` | read only | SRA-804's `get_or_create_interview_plan`, `append_interview_turn`, `get_interview_turns`, `complete_interview_attempt`. The graph does not speak SQL directly. |
| `src/sra/agents/tracing.py` | read only | `trace_agent()` is invoked from `process_interview_turn`, so LangSmith shows per-turn prompts and decisions. |
| `tests/unit/test_interview_conversation_graph.py` | new | Stubbed LLM. `start` persists one `ai` turn; `answer` persists a user turn + ai feedback turn, then either a new `ai` turn (`ask_new`/`follow_up`) or a `system_summary` turn (`finish`); `next` advances `cursor_on_plan`; `end` calls `complete_interview_attempt`; the 21st `answer` raises `InterviewAttemptFullError`; a completed attempt raises `InterviewAttemptAlreadyCompletedError`; an LLM failure raises `InterviewLLMError`. |
| `tests/integration/test_phase8_voice_turn_e2e.py` | new | `start → answer → next → answer → end` through a real `create_app()` with the LLM seam patched. |

## Design notes

- **Persist the user turn before grading**, so a crash mid-LLM-call leaves a recoverable `score=null` row the next turn can re-grade.
- **One call grades *and* decides.** A single `{score, rationale, feedback, next_action, next_question_text}` lets the prompt reason once; two calls would let the second contradict the first ("you scored 80, but your follow-up assumes you knew nothing") and double per-turn latency.
- **`max_turns=20` is a hard server-side limit.** One user turn costs one LLM call (≈3 s); twenty turns keeps a runaway interview from eating the user's daily rate-limit budget. SRA-807 / SRA-808 surface it as a soft "N of 20" indicator.
- **Per-action behaviour.** `start` lazy-creates the plan and persists one `ai` turn; `answer` persists a `user` turn, calls the LLM, backfills the score, persists feedback, then branches; `next` advances `cursor_on_plan`; `end` persists a `system_summary` turn with a server-computed mean score and answered/remaining counts, then completes the attempt.
- **`_build_decision_prompt` is deterministic** (no `datetime.now()`), so tests can snapshot it. It pins the rubric and truncates the transcript to the last 2 000 characters.

## Acceptance criteria

- [ ] `process_interview_turn` handles `start | answer | next | end` and returns a typed `InterviewTurnResult` for each
- [ ] Every `ai` / `user` / `system_summary` turn is persisted before the function returns; a crash mid-call leaves a recoverable `score=null` user row
- [ ] The 21st `action="answer"` raises `InterviewAttemptFullError`; an already-completed attempt raises `InterviewAttemptAlreadyCompletedError`; an LLM failure raises `InterviewLLMError`
- [ ] `_get_chat_model` can be monkey-patched in unit tests; no live OpenAI call happens during `pytest`
- [ ] The structured `InterviewDecision` validates the LLM response: `score` is an integer in `[0, 100]`, `next_question_text` is required for non-finish decisions, and `summary` is required for finish decisions
- [ ] On `finish`, `complete_interview_attempt` is called with the mean of the user-turn scores and `attempt.completed_at` is set
- [ ] The graph's `_build_decision_prompt` is deterministic (no `datetime.now()` inside the prompt) so unit tests can snapshot it
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_interview_conversation_graph.py -v
pytest tests/integration/test_phase8_voice_turn_e2e.py -v
ruff check .
pyright
```

`tests/unit/test_quiz_conversation_graph.py` (per-turn reload + persist) and `tests/unit/test_quiz_graph.py` (`_get_chat_model` seam) are reused directly. The regression set needs explicit ignores; see [`tests/README.md`](../tests/README.md).

## Manual verification

With a real OpenAI key, drive an interview through the live server once SRA-805 ships (manual approval required). Until then the integration test exercises the same paths with the LLM faked.

**Verification (2026-10-15):**

- Local macOS dev, `gpt-4.1-nano` (default); no live OpenAI call — `_get_chat_model()` is monkey-patched in every test.
- `tests/unit/test_interview_conversation_graph.py` → **11 passed**. `tests/integration/test_phase8_voice_turn_e2e.py` → **2 passed** (full `start → answer(ask_new) → next → answer(finish)` lifecycle plus the user-turn-before-LLM ordering guarantee).
- **Regression gate blocked by a pre-existing SRA-802 circular import:** `src/sra/db/repository.py` imports `InterviewOutline` from `interview_planner`, which imports `QuizGenerationError` from `quiz_graph`, which imports `from sra.db import repository` — all at module top. This breaks `tests/e2e/test_phase4_web_quiz_e2e.py` collection with `ImportError: cannot import name 'QuizGenerationError' from partially initialized module 'sra.agents.quiz_graph'`. SRA-803 touches none of those three files; the gate was already broken before this ticket started. The fix belongs to SRA-802.
- Hand-driven LLM call (post-SRA-805): pending.
- `ruff check .` → **All checks passed!** `pyright` → **9 errors** (down from 14; 5 fixed via `# pyright: ignore[reportReturnType]` on the SRA-804 dynamic-import call sites). The remaining 9 are pre-existing: 7 in `tests/unit/test_auth_client.py`, 1 in `interview_planner.py:197`, 1 in `tests/integration/test_phase8_end_to_end.py:119`.
- Per-turn LLM call count: **1 per `answer` action**, 0 for `start` / `next` / `end`. > **Note:** the status line still reads `Planned`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).