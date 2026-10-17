# SRA-904 — Interview planner + per-turn grader prompt uplift

**Type:** Implementation
**Phase:** 9
**Priority:** High (parallels SRA-903)
**Status:** Planned
**Blocked by:** SRA-901
**Blocks:** SRA-905 (verification)

## Summary

Rewrites `src/sra/agents/interview_planner.py` so each outline item carries a
verbatim source passage quote plus an ideal-answer rubric, and threads those into
`src/sra/agents/interview_conversation_graph.py` so the per-turn grader scores the
user's free-form answer against the same anchor material the planner wrote. The
upgrade is additive on `InterviewPlanItem` (the JSON-column outline) — older
attempts that lack the new fields fall back to today's behaviour with a logged
warning.

## Where things stand right now

- `interview_planner._call_planner` builds a 3-paragraph prompt from
  `entry.topic / subtopic / tags / summary` + 1500-char transcript slice.
- `InterviewOutlineItem` is `{ subtopic, difficulty, focus_prompt }`.
- `interview_conversation_graph._build_decision_prompt` builds the grader prompt
  from the outline item's `subtopic`/`difficulty`/`focus_prompt`/a "summary" key
  that's currently blank because the outline item shape has no `summary` field.

## What this ticket unlocks

After SRA-904, an attempt generated from a topic with two references has plan items
like:

```json
{
  "subtopic": "Scaled dot-product attention",
  "difficulty": "medium",
  "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
  "source_passage_quote": "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable as d_k grows",
  "ideal_answer_rubric": "A correct answer states that without scaling, large d_k pushes softmax into saturated regions; sqrt(d_k) keeps dot products in a low-variance range."
}
```

…and the per-turn grader sees the same two fields when scoring.

## How this fits into the whole app

```mermaid
sequenceDiagram
    participant P as interview_planner (SRA-904)
    participant DB as interview_plans (JSON outline)
    participant G as interview_conversation_graph (SRA-904)
    participant U as User (browser, SRA-807)
    participant M as graded LLM
    P->>P: build system + human prompt with source excerpts
    P->>P: with_structured_output(InterviewOutline) — items include source_passage_quote + ideal_answer_rubric
    P->>DB: create_interview_plan(outline)
    Note over DB,G: Course-grained outline; per-turn logic still reloaded from DB between turns.
    U->>G: POST /quiz/attempts/{id}/interview-turn {action:'answer', text:'…'}
    G->>G: _build_decision_prompt(plan_item, transcript, answer)
    G->>G: pull plan_item.source_passage_quote + ideal_answer_rubric if present
    G->>M: InterviewDecision LLM call
    M-->>G: {score, rationale, feedback, next_action, ...}
    G->>DB: append_turn + commit
    G-->>U: InterviewTurnResponse
```

The DB shape is unchanged — the new fields are stored as JSON keys inside the
existing `outline` array. No migration needed; the column stays `JSON`.

## Files and why they matter

| File | Action | Reason |
|---|---|---|
| `src/sra/agents/interview_planner.py` | rewrite | Six-section prompt, six difficult levels for items (Bloom's for interviews), source_passage_quote + ideal_answer_rubric, few-shot exemplar |
| `src/sra/agents/interview_conversation_graph.py` | modify | `_build_decision_prompt` threads source_passage_quote + ideal_answer_rubric into the human message; falls back to today's behaviour when absent with a logged warning |
| `src/sra/db/repository.py` | verify | `create_interview_plan` accepts `InterviewOutline` whose items carry the new keys via `model_dump()`; `get_or_create_interview_plan` placeholder stays a single-item dict (no source passage) — gracefully handled by the conversation graph fallback |
| `tests/unit/test_interview_planner_phase9.py` | new | Items carry source_passage_quote + ideal_answer_rubric; validate/resize shape preserved |
| `tests/unit/test_interview_conversation_graph_phase9.py` | new | Grader sees the new fields when plan has them, falls back to the prior prompt when it doesn't |
| `tests/integration/test_phase9_interview_e2e.py` | new | Single end-to-end attempt: start → answer → grade → second answer → grade → end; the conversation transcript reflects the rubric-anchored grading |

## TDD starting point

- `InterviewOutlineItem` keeps its existing 3 Pydantic fields; the new two fields
  default to optional `None` so legacy callers/tests still work in step 2.
- `_call_planner` returns the existing 3-key shape; the new fields are *absent*
  in step 2 (this is what makes the new tests fail for the right reason).
- `_build_decision_prompt` builds the prompt with only the existing 4 keys.

## What to implement

1. **Planner rewrite** (`interview_planner`):
   - Add three **optional** fields to `InterviewOutlineItem`:
     - `source_passage_quote: str | None = None` (8–30 words; strip-then-empty-stripped check)
     - `ideal_answer_rubric: str | None = None` (1–200 chars; same)
     - `bloom_level: Literal["remember","understand","apply","analyze","evaluate","create"] | None = None`
   - Six-section prompt mirroring SRA-903 (Role, Source-First Rule, Citation,
     Difficulty distribution, free-form rubric, Few-shot exemplar). Difficulty
     distribution: 1/3 easy, 1/3 medium, 1/3 hard — same as today's escalation
     rule.
   - Few-shot exemplar: a hand-written `interview_few_shot_example()` constant
     composed of one item per difficulty level; no PII.
   - `target_questions` continues to come from `user.interview_question_count`.
   - `_resize_outline` keeps the existing padding logic.
   - **Cost:** still one LLM call per `(entry, day)` — pregeneration cadence is unchanged.

2. **Per-turn grader upgrade** (`interview_conversation_graph`):
   - `_build_decision_prompt(plan_item, transcript, answer)` reads the new keys
     from `plan_item` if present, and renders a `<grader_rubric>` block in the
     human message: `"ideal_answer_rubric: <…>\nsource_passage_quote: <…>"`.
   - When the keys are absent: a `logger.warning("interview plan item missing source_passage_quote; using fallback prompt", extra={"entry_id": …})` is emitted once per turn and the prompt degenerates to today's text. (Backwards compatible with plans generated by an older planner.)
   - No `_get_chat_model` change; `max_completion_tokens=512`, `timeout=15s` keep their existing values.

3. **Repository** (no schema change):
   - `repository.create_interview_plan` continues to serialize `InterviewOutline` via `model_dump()`; the new fields naturally flow through as JSON keys.

## Where to look

- `src/sra/agents/interview_planner.py::_call_planner` — the full rewrite lives here.
- `src/sra/agents/interview_conversation_graph.py::_build_decision_prompt` — the per-turn scoring seam.
- `src/sra/db/repository.py::get_or_create_interview_plan` — the placeholder fallback (which has no source_passage_quote) is exercised in the conversation-graph fallback test.
- `docs/plans/2026-10-16-phase9-topic-authoring-and-quality.md` — design contract for the prompt structure.

## Acceptance criteria

- [ ] `generate_interview_outline` returns items carrying non-empty `source_passage_quote` and `ideal_answer_rubric` when called with an entry that has references and an LLM seam that returns them.
- [ ] `_resize_outline` continues to pad with general-recap items; the pads also carry the new fields.
- [ ] `_build_decision_prompt` includes the rubric block when the plan has it and skips it (with a warning) when it doesn't.
- [ ] The conversation graph end-to-end test runs an attempt start → answer → next question → answer → finish and the returned rationale cites the planner's rubric.
- [ ] Existing Phase 8 regression suite (entries in `test_interview_conversation_graph.py` + `test_interview_planner.py`) stays green without modification.

## How to test this ticket alone

```bash
pytest tests/unit/test_interview_planner_phase9.py tests/unit/test_interview_conversation_graph_phase9.py tests/integration/test_phase9_interview_e2e.py -v
ruff check src/sra/agents
pyright src/sra/agents/interview_planner.py src/sra/agents/interview_conversation_graph.py
pytest -m 'not phase9' -q   # baseline stays green
```

## Manual verification

1. With a seeded entry that has two URL-fetched references, run the SRA-509 dev affordance; an interview plan materializes.
2. Open `/dashboard/interview`, complete the attempt; read the recorded rationale in the transcript — confirm it reads like a comparison against the supplied rubric, not an LLM-invited one.

## Verification record

- Date and environment:
- Commands and inputs:
- New IDs/artifacts and observed downstream results:
- Failure/recovery and cleanup:
- Automated/manual results, skips, and limitations:

## Definition of Done

- [ ] All acceptance criteria met
- [ ] Default regression suite stays green
- [ ] No new lint / type errors
- [ ] PROGRESS.md updated
