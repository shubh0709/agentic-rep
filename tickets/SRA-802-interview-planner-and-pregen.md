# SRA-802 — Interview planner agent and pregeneration

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds `src/sra/agents/interview_planner.py`, a LangGraph that turns one `StudyEntry` into a structured `InterviewPlan` outline — an ordered list of `{subtopic, difficulty, focus_prompt}` items the SRA-803 conversation graph expands one at a time. Extends `src/sra/jobs/quiz_pregeneration.py` so the nightly batch produces one `InterviewPlan` per new `QuizSet`, idempotent against reruns, using the user's `interview_question_count` preference as `target_questions`. The existing dev pregeneration summary gains `interview_plans_created / skipped / errored` counts. This is the only place the planner LLM is called at scale; per-turn expansion stays inside SRA-803.

## Unlocks

- A real outline is already on disk for every `QuizSet` the nightly batch produced, so an interview start is not a slow LLM call. Before this ticket the only path to an outline is SRA-803's lazy-create.
- The conversation graph (SRA-803) reloads the outline from the DB on every turn, and the dashboard can show "questions 1 of 6" without re-querying the LLM.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/interview_planner.py` | new | `InterviewOutlineItem` (`subtopic`, `difficulty: Literal["easy","medium","hard"]`, `focus_prompt`), `InterviewOutline` container of N items, `_get_chat_model()` mirroring `quiz_graph._get_chat_model`, `generate_interview_outline(entry, target_questions, *, llm=None) -> InterviewOutline`, and `build_interview_planner_graph()` wiring `_call_planner -> _validate -> END`. |
| `src/sra/db/repository.py` | edit | `create_interview_plan(*, quiz_set, outline, target_questions, session) -> InterviewPlan` — catches `IntegrityError` on the unique `quiz_set_id` and returns the existing row. |
| `src/sra/jobs/quiz_pregeneration.py` | edit | After a successful `create_quiz_set`, build the outline and call `create_interview_plan`. Adds `interview_plans_created`, `interview_plans_skipped`, `interview_plans_errored` to `PregenerationSummary`. |
| `src/sra/api/routers/dev.py` | edit (read only) | `POST /api/v1/me/dev/pregenerate-now` (only when `DEV_MODE=true`) returns the three new counters beside the existing MCQ counts. No new route. |
| `src/sra/agents/tracing.py` | edit (read only) | `trace_agent()` is invoked from the planner graph the same way `quiz_graph` does. |
| `tests/unit/test_interview_planner.py` | new | Stubbed LLM returning a scripted `InterviewOutline`: length matches `target_questions`, all three fields present, empty outline on LLM failure, empty outline when the LLM returns a non-list, `_get_chat_model()` monkey-patchable. |
| `tests/unit/test_quiz_pregeneration.py` | edit | A pregen run creates a `QuizSet` **and** an `InterviewPlan` per new `(entry, today)`; a second run same-day does not duplicate; a planner LLM failure increments `interview_plans_errored` without stopping the batch. |
| `tests/integration/test_phase8_end_to_end.py` | new | Call `pregenerate_quizzes_for_day` against a real DB seeded with a Phase 5/6 user; assert the three new counts and read the `InterviewPlan` row back to confirm the outline JSON shape. |

## Design notes

- **One planner call per `(entry, day)`** — the same cardinality as the existing MCQ pregeneration. The outline is bounded (default six, max twelve from the preference validator), so the structured-output payload stays small.
- **The planner may fail silently.** `quiz_graph.py` already returns an empty draft list on LLM failure and routes to `END` without persisting. The same invariant holds here: an LLM failure or empty outline means the job records `interview_plans_errored += 1` and moves on. The SRA-805 router can still lazily call the planner on interview start.
- **The conversation graph never calls the planner**; it only reads `interview_plans.outline`. The decoupling keeps a planner failure from breaking a running interview, and a live-only approach would re-call the planner every turn to decide the next topic — more flexible, but more expensive and harder to make resumable after a disconnect.
- **Validation.** `InterviewOutline` rejects when `len(items) != target_questions`; the validate helper trims from the end or pads with a fallback `"general recap"` item so the contract holds even when the LLM produces the wrong count. `raw_transcript` is truncated to 1 500 characters to keep the prompt small. `_get_chat_model()` uses `max_completion_tokens=1024`, `timeout=20`, `max_retries=1`.

## Acceptance criteria

- [ ] `generate_interview_outline` returns a validated `InterviewOutline` of the configured length for the happy path
- [ ] An LLM failure or non-list response yields an empty `InterviewOutline` and a logged `QuizGenerationError`; the pregen job continues to the next entry
- [ ] The planner LLM seam (`_get_chat_model`) can be monkey-patched in unit tests; no live OpenAI call happens during `pytest`
- [ ] `pregenerate_quizzes_for_user` creates one `InterviewPlan` per new `QuizSet`; a second run the same day does not create a duplicate and increments `interview_plans_skipped`
- [ ] `dev_pregenerate_now` returns the three new counters in the `PregenerationSummary` JSON
- [ ] The new `users.interview_question_count` preference is read at generation time and snapshotted into `interview_plans.target_questions`
- [ ] The `interview_plans.outline` JSON column round-trips the `{subtopic, difficulty, focus_prompt}` shape
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_interview_planner.py -v
pytest tests/unit/test_quiz_pregeneration.py -v
pytest tests/integration/test_phase8_end_to_end.py -v
pytest -m 'not phase8' -q
ruff check .
pyright
```

Mirrors `tests/unit/test_quiz_graph.py`'s `_get_chat_model` seam, so no new fixture is invented. The end-to-end file reuses the `seeded_user` and `client` fixtures from the Phase 4/5/6 integration tests.

## Manual verification

With `DEV_MODE=true` and a real OpenAI key, log in as a user with an entry that already has today's `QuizSet` row but no `InterviewPlan`, then call:

```bash
curl -X POST -b 'sra_session=…' \
  'http://127.0.0.1:8000/api/v1/me/dev/pregenerate-now?include_upcoming=true'
# {"summary": {"users": 1, "entries_seen": 1, "sets_created": 1, "sets_skipped": 0,
#  "errors": 0, "interview_plans_created": 1, "interview_plans_skipped": 0,
#  "interview_plans_errored": 0}, "today": "...", "include_upcoming": true, ...}
```

- [ ] Inspect the `interview_plans` row: `outline` has exactly `interview_question_count` items, each with all three fields populated.
- [ ] Call the endpoint again: `interview_plans_created` stays the same and `interview_plans_skipped` increments.

> **Note:** the verification record for this ticket is still unfilled — no actual run has been recorded.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).