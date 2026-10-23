# Phase 9 — Topic authoring with reference materials + question-quality uplift

> **Archived plan (2026-10-16).** Superseded — kept for decision history only.
> Current design: [`../design/technical-design.md`](../design/technical-design.md) ·
> current status: [`../../PROGRESS.md`](../../PROGRESS.md) ·
> current work items: [`../../tickets/README.md`](../../tickets/README.md).

## Scope at the time

Responds to three user complaints: there was no way to author a topic except by
dictating a transcript; the generation agents only ever saw `topic / subtopic /
tags / summary` plus a 1500-char transcript slice, never the actual source
material; and the resulting questions were mediocre. The plan added a
`reference_materials` table (URLs, pasted text, PDF/DOCX/TXT/MD uploads) that
the MCQ and interview agents consume as source evidence, a `POST /api/v1/topics`
authoring endpoint plus dashboard UI, and a prompt-quality uplift (source-first
rule, citations, Bloom distribution, distractor rubric, per-question verifier).

## Decisions recorded only here

- **Quality is prompt-driven, not a model upgrade** — `OPENAI_MODEL` deliberately
  left untouched; the verifier pass is the principal defense against hallucinated
  support, with a bounded ceiling rather than a zero-risk claim.
- **Raw uploaded bytes are never stored on disk.** `extracted_text` becomes the
  single source of truth once ingestion completes, and the user-supplied
  bytes/HTML are discarded — predictable storage cost, no content-addressable
  store or signed-URL story in Phase 9.
- **JSON endpoint with base64-encoded uploads, not multipart** — one endpoint,
  one request, one DTO; the `httpx` test shape stays identical to the curl shape.
- **trafilatura over `readability-lxml` / `newspaper3k` / `goose3` / `jusText`** —
  independent benchmarks put it ahead on accuracy and speed for general
  HTML-to-text, and it is MIT, zero-config, and a single pure-Python wheel.
- **No retry policy, intentionally.** A failed fetch still persists the row with
  `fetch_status='failed'` and the error so the user sees *why*; they re-click
  "Regenerate" rather than the server retrying behind their back.
- **Synchronous generation with partial-success semantics** — on an LLM error the
  entry and references are still created and the response reports
  `quiz_set_id: null` / `interview_plan_id: null` with the typed error.
- **Verifier defaults ON despite cost:** ~11 LLM calls per topic (1 draft + 10
  verifies) at the 10-question default; the complaint was quality, not cost, and
  `OPENAI_VERIFY_MCQS=false` is the documented escape hatch.
- **SSRF allow-listing is explicitly out of scope** — `httpx` defaults for now,
  with a possible future `REFERENCE_URL_ALLOWLIST` setting.
- **Explicitly out of scope:** touching `OPENAI_MODEL`, reviewing the Phase 1
  ingest route, any review-schedule change, and OCR for scanned-image PDFs
  (those surface "couldn't extract text" inline).

### A. Reference material model (referenced by SRA-901)

The kind discriminator is enforced by a SQL CHECK constraint *and* in Pydantic:

```
(kind = 'url'    AND url IS NOT NULL AND pasted_text IS NULL AND file_name IS NULL)
OR (kind = 'paste' AND pasted_text IS NOT NULL AND url IS NULL AND file_name IS NULL)
OR (kind = 'upload' AND file_name IS NOT NULL AND content_type IS NOT NULL
    AND url IS NULL AND pasted_text IS NULL)
```

`extracted_text` is populated for `url` (by the fetcher) and `upload` (by the
extractor) and mirrors user-supplied `pasted_text` when `kind='paste'`;
`fetch_status` is `pending` / `fetched` / `failed`.

### E. Quality uplift (referenced by SRA-903)

Prompt budget: **6,000 characters per reference, 12,000 total**, with overflow
marked `[… reference truncated at 6,000 chars …]` so the model knows the bounds.
Bloom distribution for the default 10 questions: 3 *remember/recall*, 4
*apply/explain*, 2 *analyze/compare*, 1 *evaluate/create*, labelled per item in
`bloom_level`.

- **Interview grader uplift** raises the per-turn prompt budget from ~2,000 to
  ~6,500 characters (the planner's `source_passage_quote` +
  `ideal_answer_rubric` reach the grader), with `max_completion_tokens=512`
  unchanged, and falls back to the pre-Phase-9 prompt with a logged warning when
  an in-flight attempt's plan lacks those fields.

## Superseded by

- Reference-materials schema, fetcher, extractor, repository helpers, ingestion
  route → [`SRA-901`](../../tickets/SRA-901-reference-materials-and-ingestion-route.md).
- "Create topic" dashboard modal and generate-now endpoint →
  [`SRA-902`](../../tickets/SRA-902-dashboard-create-topic-ui.md).
- MCQ prompt uplift and verifier pass →
  [`SRA-903`](../../tickets/SRA-903-quiz-graph-quality-uplift.md); interview
  planner and grader uplift →
  [`SRA-904`](../../tickets/SRA-904-interview-quality-uplift.md).
- Verification, README, board and Phase 9 status →
  [`SRA-905`](../../tickets/SRA-905-phase9-integration-verification.md),
  [`../../PROGRESS.md`](../../PROGRESS.md),
  [`../../tickets/README.md`](../../tickets/README.md).
- `SRA-906`–`SRA-909` postdate this plan and are tracked only in the board.