# SRA-903 — MCQ generation prompt uplift (Bloom's, distractor rubric, source-grounded)

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** not started

## Summary

Rewrites `src/sra/agents/quiz_graph.py` so every MCQ is source-grounded, Bloom-distributed, distractor-rubriced, and verified by a follow-up LLM call. The existing `MCQDraft` / `MCQDrafts` schema gains three additive fields (`bloom_level`, `source_passage_quote`, `distractor_misconception`) — no breaking change. A new `OPENAI_VERIFY_MCQS` setting (default `true`) gates the per-question verify pass. The conversation graph, planner, and dashboard all consume `QuizQuestion` rows and are unaffected; no read-side schema changes. The new fields are persisted alongside the existing ones so old quiz rows keep working.

## Unlocks

- SRA-904 (interview uplift) can mirror the same source-grounding approach for interview outlines.
- A quiz set generated from an entry whose `reference_materials` carry two transformer blog posts now cites its source per question, so a reviewer can check the answer rather than take it on trust.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/agents/schemas.py` | edit | `MCQDraft` gains `bloom_level: Literal["remember","understand","apply","analyze","evaluate","create"]`, `source_passage_quote: str` (8–30 words, strip-and-reject-empty validator), and `distractor_misconception: str` (1–200 chars, required). Add `MCQVerifyResult` (`verdict: Literal["yes","no"]`, `reason: str`) for the second-pass verdict. |
| `src/sra/agents/quiz_graph.py` | edit (rewrite) | Six-section system prompt with Bloom distribution, citation requirement, distractor rubric, and a few-shot exemplar; per-draft verify pass gated on `OPENAI_VERIFY_MCQS`; the SRA-901 bundle helper replaces the entry-level summary as the source of truth. |
| `src/sra/db/models.py` | edit | Persist `bloom_level`, `source_passage_quote`, `distractor_misconception` on `QuizQuestion` with `sa_column_kwargs={"nullable": True}` each, so the migration is purely additive. |
| `src/sra/alembic/versions/<new>_phase9_mcq_quality_fields.py` | new | Migration off `f6a7b8c9d0e1`; `downgrade()` drops the three columns. |
| `src/sra/api/schemas.py` | edit | `QuizQuestionOut` exposes the three new fields when present, omitting them on legacy rows. |
| [`src/sra/config/`](../src/sra/config/) | edit | `OPENAI_VERIFY_MCQS: bool = True`, with a validator that forbids `True` if `OPENAI_API_KEY` is empty. |
| `src/sra/db/repository.py` | edit | `create_quiz_set` writes the new columns per draft; defaults to null on the `QuizQuestion` ORM side. `delete_user_cascade` needs no change — the new columns drop with the table. |
| `src/sra/jobs/quiz_pregeneration.py` | edit | Pass an optional `references=` parameter through to `generate_and_persist_questions`. |
| `tests/unit/test_quiz_graph_phase9.py` | new | Bloom distribution, distractor rubric honored, `source_passage_quote` present, the verify pass drops unsupported drafts, `OPENAI_VERIFY_MCQS=false` short-circuits the second pass. |
| `tests/unit/test_quiz_models_phase9.py` | new | Additive columns nullable on legacy rows; CHECK-constraint-free by design (intentional additive change). |
| `tests/integration/test_phase9_migration.py` | edit | Forward + reverse + replay preserves pre-Phase-9 quiz rows. |
| `tests/integration/test_phase9_verifier_quality.py` | new | A planted bad MCQ is rejected by a fake verify-LLM; a good MCQ passes. |

## Design notes

- **Six-section system prompt** assembled from constants in the module — Role, Source-First Rule, Citation, Bloom distribution, Distractor rubric, Few-shot — so tests can snapshot the rendered text. The human message carries entry metadata + a `source_excerpts = repository.get_reference_text_bundle(entry.id, session)` block (truncated per SRA-901's defaults) + the user's transcript slice as a "context" block.
- **Bloom distribution** reads `prefs['quiz_question_count']` and asks the LLM for that many, broken down across the six levels so the requested split is approximated within ±1 per level.
- **`_verify_draft(draft, source_passage, llm)`** builds a 4-line system + human message asking whether the question's correct option is actually supported by the cited passage and returns `MCQVerifyResult`. Bounded on `max_completion_tokens=64`, `timeout=10s`, `max_retries=0`, and skipped entirely when `Settings.openai_verify_mcqs is False`.
- **Discard transparency.** Rejected drafts (LLM error, validate fail, verify-fail) accumulate reasons in `state["discarded_for_evidence"]` rather than being silently dropped; `_build_quiz_set_out` and the dashboard quiz-list page surface that per quiz set when at least one draft was rejected.
- **Phase 4 regression safety.** The schema additions are nullable, the validate step still drops empty/invalid drafts the same way, the per-day `quiz_sets(entry_id, generated_on)` unique constraint is unchanged, and `create_quiz_set` keeps its empty-drafts fallback (mirroring the SRA-509 dev affordance).

## Acceptance criteria

- [ ] Quiz generated from a topic with two references contains `bloom_level` and `source_passage_quote` on every question
- [ ] Distractor misconception is non-empty on every question
- [ ] Bloom distribution approximates the requested split (within ±1 per level)
- [ ] A planted MCQ whose correct option is unsupported by the cited passage is dropped by the verifier (fake LLM returns `verdict="no"`)
- [ ] `OPENAI_VERIFY_MCQS=false` skips the verifier entirely; the verify test suite uses `monkeypatch.setenv("OPENAI_VERIFY_MCQS", "false")` to assert it
- [ ] Migration adds the three columns nullable; a legacy `QuizQuestion` row loads with `bloom_level=None` and is returned through `QuizQuestionOut` without the new fields rendered
- [ ] Existing Phase 4 quiz regression suite (26 tests in `test_quiz_routes.py` + `test_quiz_graph.py`) stays green without modification
- [ ] `ruff check .` clean; `pyright` clean on changed modules

## Tests

```bash
pytest tests/unit/test_quiz_graph_phase9.py tests/unit/test_quiz_models_phase9.py tests/integration/test_phase9_verifier_quality.py -v
pytest tests/integration/test_phase9_migration.py -v
OPENAI_VERIFY_MCQS=false pytest tests/unit/test_quiz_graph.py -v
pytest -m 'not phase9' -q
ruff check src/sra
pyright src/sra/agents/quiz_graph.py src/sra/agents/schemas.py
```

`_get_chat_model()` stays the LLM seam; tests bind `_get_draft_model` / `_get_verify_model` lazily instead of calling OpenAI.

## Manual verification

- [ ] Seed a temporary SQLite file with one entry + one paste reference carrying real transformer material.
- [ ] With `OPENAI_VERIFY_MCQS=true` and the real `OPENAI_API_KEY`, hit the SRA-509 dev-mode endpoint to trigger `pregenerate_quizzes_for_user`.
- [ ] Pick 5 questions at random from the resulting `QuizSet`; confirm each cites a passage that exists in the source and that the distractor misconception reads like a known confusable error.
- [ ] Run once with `OPENAI_VERIFY_MCQS=false` and confirm the same structure but without the verify-rejection drop.

> **Note:** the verification record for this ticket is unfilled — no actual run has been recorded.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).