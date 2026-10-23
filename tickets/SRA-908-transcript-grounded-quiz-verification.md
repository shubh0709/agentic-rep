# SRA-908 — Verify quizzes generated from captured transcripts

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** verified

## Summary

Uses the entry transcript as verifier evidence when an entry has no attached `reference_materials`. The draft prompt already includes `raw_transcript`, so the draft model can generate grounded questions — but the Phase 9 verifier selects evidence only from reference rows and receives an empty passage for every legacy, voice-captured, and text-captured entry, rejecting valid drafts. After the fix a transcript captured through the existing ingestion flow can be used both to draft *and* verify MCQs. Reference-backed topics still prefer their stored reference excerpts, and a transcript-only entry receives a populated `QuizSet` instead of having every valid draft evaluated against an empty passage.

## Unlocks

- Reliable quiz generation for voice and text capture: a captured entry with no attached references produces a real quiz.
- SRA-907's startup catch-up now persists a usable quiz set for previously missing due entries.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/quiz_graph.py` | edit | Select the transcript as evidence only when no usable reference excerpt exists, retaining reference preference and existing truncation bounds. |
| `tests/unit/test_quiz_graph_phase9.py` | edit | Prove transcript fallback, reference preference, blank-transcript behavior, and the exact verifier passage. |
| `tests/integration/test_phase9_quiz_graph_e2e.py` | edit | Keep the real graph and repository path, substitute inference, and prove a transcript-only captured entry persists non-empty quiz questions. |
| `tickets/README.md`, `PROGRESS.md` | edit | Track and verify the regression. |

## Observed evidence

- The configured model and structured-output schema returned **10 valid drafts** in one approved, non-persisting diagnostic call for **entry 4**.
- Entry 4 has a transcript and no references.
- `_pick_source_passage()` returns `""` whenever `references` is empty, even though the same graph supplied `entry.raw_transcript` to the draft prompt.
- Existing quiz-generation tests covered reference-grounded verification but not the transcript-only application path.

## Contracts

- When at least one usable reference excerpt exists, verification uses the matching reference behavior SRA-903 already implemented.
- When no usable reference excerpt exists, verification uses `entry.raw_transcript` as the source passage.
- Blank references *and* a blank transcript remain an evidence-empty case — the code must not invent support.
- The same bounded transcript slice the draft prompt uses (1,500 characters) is supplied to the verifier, so the verifier cannot approve from unseen text.
- Tests substitute OpenAI; no additional paid call is required to implement or verify this regression.

## Acceptance criteria

- [x] A transcript-only entry persists at least one verified quiz question under deterministic fake inference
- [x] Reference-backed entries continue to verify against references
- [x] Empty-evidence entries remain explicit and do not fabricate a passage
- [x] Existing Phase 4 and Phase 9 quiz suites remain green
- [x] Ruff and Pyright pass on the changed surfaces

## Tests

```bash
python -m pytest tests/unit/test_quiz_graph_phase9.py tests/integration/test_phase9_quiz_graph_e2e.py -q
python -m pytest tests/unit/test_quiz_graph.py tests/unit/test_quiz_pregeneration.py -q
ruff check src/sra/agents/quiz_graph.py tests/unit/test_quiz_graph_phase9.py tests/integration/test_phase9_quiz_graph_e2e.py
pyright src/sra/agents/quiz_graph.py
```

## Manual verification

- [ ] Against a captured entry with a transcript and no references, confirm the live catch-up creates one quiz set with non-empty questions, and confirm a reference-backed entry still verifies against its references.

**Verification (2026-10-17, macOS, Python 3.13.12, SQLite):**

- Deterministic fake inference plus one explicitly approved real OpenAI draft diagnostic and the reload-triggered application catch-up.
- Focused SRA-908 suite **26 passed**; existing quiz / pre-generation regressions **24 passed**; Ruff clean; Pyright zero errors.
- The approved diagnostic returned **10 structured drafts** without persistence. After the fix, the live catch-up created exactly **one quiz set for entry 4** and persisted **nine questions**; one draft was rejected by the verifier as designed.
- Before the fix, whitespace-only/missing references produced empty verifier evidence and the integration flow persisted **zero** questions. No temporary rows were created by the diagnostic call.
- The real result proves the configured model path for one existing due entry — not every possible transcript or model response.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).