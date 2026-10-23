# SRA-402 — MCQ generation agent and quiz repository

**Phase:** 4 — Daily MCQ quiz (web) · **Status:** not started

## Summary

Add the MCQ generation LangGraph agent and the quiz repository helpers that the
rest of Phase 4 depends on. One call takes a `StudyEntry` plus a question and
option count and returns a persisted `QuizSet` with its `QuizQuestion` rows loaded.
The agent goes through the same `_get_chat_model()` seam the ingestion and query
agents already use, so tests swap in a fake model without touching OpenAI. No HTTP
route, no scheduler, and no UI ship here — those are SRA-403, SRA-404, and SRA-405.

## Unlocks

- One Python call produces a readable `QuizSet` with `order_index` 0..n-1, each question carrying exactly `option_count` options and an in-range `correct_option_index`.
- SRA-403 can call `generate_and_persist_questions` per due entry; SRA-404 can read the rows back through the repository.
- A second call the same day fails fast on `uq_quiz_sets_entry_generated_on` — the idempotency guard SRA-403 relies on.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/schemas.py` | edit | Add `MCQDraft` (`question_text: str`, `options: list[str]`, `correct_option_index: int`, `explanation: str`) and the `MCQDrafts` container (`drafts: list[MCQDraft]`) beside `StudyEntryDraft`/`StudyEntryDrafts`, so the existing structured-output pattern works unchanged. |
| `src/sra/agents/quiz_graph.py` | new | `MCQState(TypedDict)`; `_get_chat_model()` mirroring `ingestion_graph._get_chat_model`; `generate_questions(state)`; `persist_quiz_set(state)`; `build_quiz_graph()` compiling `START -> generate_questions -> persist_quiz_set -> END` with `name="sra-quiz"`; public `generate_and_persist_questions(entry, user_id, session, question_count, option_count) -> QuizSet`. |
| `src/sra/db/repository.py` | edit | `create_quiz_set(*, entry, user_id, session, generated_on, question_count, option_count, drafts) -> QuizSet`; `get_quiz_set(user_id, quiz_set_id, session) -> QuizSet \| None`; `list_today_quiz_sets(user_id, today, session)`; `save_quiz_answer(*, attempt, question, selected_option_index, is_correct, session) -> QuizAnswer`; `complete_quiz_attempt(*, attempt, score, session) -> QuizAttempt`; `get_quiz_attempt(user_id, attempt_id, session) -> QuizAttempt \| None`. Every helper takes `user_id` first and never trusts caller-supplied ownership. |
| `tests/unit/test_quiz_schemas.py` | new | `MCQDraft`/`MCQDrafts` defaults, `correct_option_index` range validation, container carries a list. |
| `tests/unit/test_quiz_graph.py` | new | Monkeypatch `_get_chat_model`; assert the prompt covers topic + summary + tags, `create_quiz_set` receives the snapshot counts, and each draft's `order_index` is its list position. |
| `tests/unit/test_quiz_repository.py` | new | One-transaction `create_quiz_set` with `order_index`; ownership enforcement on every getter; `(attempt_id, question_id)` uniqueness surfaced as a typed `ValueError`. |
| `pyproject.toml` | edit | Register the `phase4` pytest marker. |
| `src/sra/api/routers/__init__.py` | read only | The quiz router is **not** added here — it ships with SRA-404. |

`_get_chat_model()` reads `settings.openai_model`, `settings.openai_api_key`,
`max_completion_tokens=2048`, `timeout=30`, `max_retries=1`. `generate_questions`
binds `MCQDrafts` via `model.with_structured_output(MCQDrafts, method="function_calling")`,
drops drafts with empty `question_text`, an `options` length ≠ `option_count`, or an
out-of-range `correct_option_index`, and — on LLM failure — sets `state["error"]` and
returns an empty drafts list rather than raising. `generate_and_persist_questions`
raises `QuizGenerationError(result["error"])` when `result["error"]` is set.

## Acceptance criteria

- [ ] `MCQDraft` and `MCQDrafts` round-trip with the expected field set, defaults, and validation
- [ ] `_get_chat_model()` in `quiz_graph.py` returns the same configured OpenAI model as the other agents, and tests can monkeypatch it without contacting OpenAI
- [ ] `generate_questions` builds a prompt that includes the entry's topic, summary, and tags; binds the structured-output schema; drops drafts whose `question_text` is empty, whose `options` length differs from `state["option_count"]`, or whose `correct_option_index` is out of range
- [ ] LLM failures set `state["error"]` and return an empty drafts list without raising
- [ ] `generate_and_persist_questions(entry, user_id, session, question_count, option_count)` produces a persisted `QuizSet` and its questions, with `order_index` set to each draft's position
- [ ] `create_quiz_set` is idempotent: a second call the same day with the same `(entry_id, generated_on)` raises a typed `ValueError` rather than creating a duplicate `QuizSet`
- [ ] `get_quiz_set`, `list_today_quiz_sets`, `save_quiz_answer`, `complete_quiz_attempt`, and `get_quiz_attempt` all enforce ownership by `user_id` and return `None` (or raise a typed error on duplicate) for another user's data
- [ ] Existing Phase 1/2/3 unit, e2e, and integration tests remain green; Pyright and Ruff pass without new warnings

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_schemas.py tests/unit/test_quiz_graph.py tests/unit/test_quiz_repository.py -v
pytest -m 'not phase4' -q
ruff check .
pyright
```

The focused tests run against a fake LLM and in-memory SQLite; no OpenAI key or
network call is required. Module-level `import` of the SRA-401 model names means
this ticket depends on SRA-401 landing first.

## Manual verification

- [ ] Run `generate_and_persist_questions(entry=..., user_id=..., session=..., question_count=3, option_count=4)` once against a real Phase 3 database with a configured `OPENAI_API_KEY`
- [ ] Record `quiz_set.id`, `user_id`, `entry_id`, `generated_on`, and the snapshot values; confirm `user_id` matches the entry's user
- [ ] Confirm three `QuizQuestion` rows with `order_index` 0, 1, 2, each with four options and an in-range `correct_option_index`
- [ ] Re-run the same call; confirm it raises on the unique constraint rather than creating a duplicate `QuizSet`
- [ ] Do not use a production database for the first run; the call incurs a small OpenAI charge

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).