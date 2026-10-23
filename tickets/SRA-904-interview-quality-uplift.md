# SRA-904 — Interview planner + per-turn grader prompt uplift

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** not started

## Summary

Rewrites `src/sra/agents/interview_planner.py` so each outline item carries a verbatim source passage quote plus an ideal-answer rubric, and threads those into `src/sra/agents/interview_conversation_graph.py` so the per-turn grader scores the user's free-form answer against the same anchor material the planner wrote. The upgrade is additive on `InterviewOutlineItem` (the JSON-column outline) — older attempts lacking the new fields fall back to today's behaviour with a logged warning. The database shape is unchanged: the new fields are stored as JSON keys inside the existing `outline` array, so **no migration is needed** and the column stays `JSON`.

## Unlocks

- SRA-905 gains a surface whose rationale is anchored to supplied source material rather than LLM-invented, so the verification run can check quality against the references.
- An attempt generated from a topic with two references produces plan items carrying `source_passage_quote` and `ideal_answer_rubric`, and the grader sees those same two fields when scoring.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/interview_planner.py` | edit (rewrite) | Six-section prompt, six difficulty levels for items (Bloom's for interviews), `source_passage_quote` + `ideal_answer_rubric`, few-shot exemplar. |
| `src/sra/agents/interview_conversation_graph.py` | edit | `_build_decision_prompt` threads both new fields into the human message as a `<grader_rubric>` block, falling back to today's prompt when absent with a logged warning. |
| `src/sra/db/repository.py` | edit (verify only) | `create_interview_plan` already serializes `InterviewOutline` via `model_dump()`, so the new keys flow through naturally. `get_or_create_interview_plan`'s placeholder stays a single-item dict with no source passage, handled by the conversation-graph fallback. |
| `tests/unit/test_interview_planner_phase9.py` | new | Items carry `source_passage_quote` + `ideal_answer_rubric`; validate/resize shape preserved. |
| `tests/unit/test_interview_conversation_graph_phase9.py` | new | The grader sees the new fields when the plan has them and falls back to the prior prompt when it doesn't. |
| `tests/integration/test_phase9_interview_e2e.py` | new | One attempt: start → answer → grade → second answer → grade → end, with the transcript reflecting rubric-anchored grading. |

## Design notes

- **`InterviewOutlineItem` gains three optional fields** so legacy callers and tests keep working: `source_passage_quote: str | None = None` (8–30 words, strip-then-empty-stripped check), `ideal_answer_rubric: str | None = None` (1–200 chars, same check), and `bloom_level: Literal["remember","understand","apply","analyze","evaluate","create"] | None = None`.
- **Six-section prompt** mirrors SRA-903: Role, Source-First Rule, Citation, Difficulty distribution, free-form rubric, Few-shot exemplar. Difficulty distribution is 1/3 easy, 1/3 medium, 1/3 hard — the same escalation rule as today.
- **`interview_few_shot_example()`** is a hand-written constant composed of one item per difficulty level, with no PII.
- **`target_questions`** still comes from `user.interview_question_count`; `_resize_outline` keeps its existing padding logic, and the pads also carry the new fields.
- **Cost is unchanged:** still one LLM call per `(entry, day)` — pregeneration cadence is untouched.
- **`_build_decision_prompt(plan_item, transcript, answer)`** reads the new keys when present and renders `"ideal_answer_rubric: <…>\nsource_passage_quote: <…>"`. When absent it emits `logger.warning("interview plan item missing source_passage_quote; using fallback prompt", extra={"entry_id": …})` once per turn and degenerates to today's text — backwards compatible with plans from an older planner.
- **`_get_chat_model` is unchanged**: `max_completion_tokens=512`, `timeout=15s` keep their existing values.

## Acceptance criteria

- [ ] `generate_interview_outline` returns items carrying non-empty `source_passage_quote` and `ideal_answer_rubric` when called with an entry that has references and an LLM seam that returns them
- [ ] `_resize_outline` continues to pad with general-recap items; the pads also carry the new fields
- [ ] `_build_decision_prompt` includes the rubric block when the plan has it and skips it (with a warning) when it doesn't
- [ ] The conversation graph end-to-end test runs an attempt start → answer → next question → answer → finish and the returned rationale cites the planner's rubric
- [ ] Existing Phase 8 regression suite (entries in `test_interview_conversation_graph.py` + `test_interview_planner.py`) stays green without modification

## Tests

```bash
pytest tests/unit/test_interview_planner_phase9.py tests/unit/test_interview_conversation_graph_phase9.py tests/integration/test_phase9_interview_e2e.py -v
pytest -m 'not phase9' -q
ruff check src/sra/agents
pyright src/sra/agents/interview_planner.py src/sra/agents/interview_conversation_graph.py
```

`get_or_create_interview_plan`'s placeholder (no `source_passage_quote`) is exercised by the conversation-graph fallback test.

## Manual verification

- [ ] With a seeded entry that has two URL-fetched references, run the SRA-509 dev affordance and confirm an interview plan materializes.
- [ ] Open `/dashboard/interview` and complete the attempt. Read the recorded rationale in the transcript and confirm it reads like a comparison against the supplied rubric, not an LLM-invited one.

> **Note:** the verification record for this ticket is unfilled — no actual run has been recorded.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).