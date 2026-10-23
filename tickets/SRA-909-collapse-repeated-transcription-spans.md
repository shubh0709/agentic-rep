# SRA-909 — Collapse repeated spans in local transcription

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** verified

## Summary

Removes exact adjacent repetition of multi-word spans from the transcript returned by the local Whisper boundary, preserving normal word repetition and segment order. The previous implementation stripped and joined model segments verbatim, so a hallucinated loop inside one segment or across neighbouring segments reached the ingestion API unchanged — repeated Whisper text is then saved as noisy study material and produces poor quizzes downstream. When Whisper returns `"retrieval practice improves memory"` repeatedly in one segment or adjacent segments, `transcribe()` now returns one copy. Short natural emphasis such as `"very very important"` and non-adjacent reuse remain unchanged.

## Unlocks

- Reliable voice capture and downstream generation quality: the study material a captured entry is built from is clean text, not a loop.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/capture/transcription.py` | edit | Normalize segment whitespace and collapse exact adjacent repeated spans without changing file/model ownership or error behavior. |
| `tests/unit/test_transcription.py` | edit | Pin within-segment, cross-segment, multi-copy, punctuation/case, natural repetition, ordering, silence, and lazy-iteration contracts. |
| `tests/e2e/test_voice_capture_to_digest_e2e.py` | edit | Keep recorder/pipeline/API/storage real with fake Whisper output and prove only the cleaned transcript is submitted and persisted. |
| `tickets/README.md`, `PROGRESS.md` | edit | Track the bug and verification evidence. |

## Observed evidence

A read-only analysis of stored **entry 5** found **181 words**, a **21-word block repeated immediately**, and repeated 5–10-word n-grams occurring multiple times. No study content was printed during the analysis. The existing transcription code has no overlap or repetition handling — it only joins nonblank segment strings.

## Contracts

- Collapse exact adjacent repeated spans only when the repeated unit contains at least **four whitespace-delimited tokens**.
- Collapse **two or more** consecutive copies to one, including repetitions split across Whisper segment boundaries.
- Match repeated tokens case-insensitively while retaining the first copy's spelling and punctuation.
- Preserve single-word and short-phrase repetition, non-adjacent repetition, and the order of unrelated text.
- Retain the existing file validation, model cache, `vad_filter=True`, lazy iteration, typed errors, silence behavior, and caller-owned file contract.
- Tests substitute Whisper and HTTP/OpenAI boundaries; no real microphone, model download, or external request is needed.

## Acceptance criteria

- [x] The observed class of long adjacent repeated blocks collapses to one copy
- [x] Repetition both within and across segment boundaries is handled
- [x] Natural short repetition and non-adjacent reuse are preserved
- [x] The capture-to-storage flow persists the cleaned transcript exactly once
- [x] Existing Phase 2 transcription/capture regressions, Ruff, and Pyright pass

## Tests

```bash
python -m pytest tests/unit/test_transcription.py tests/unit/test_capture_pipeline.py -q
python -m pytest tests/e2e/test_voice_capture_to_digest_e2e.py -q
ruff check src/sra/capture/transcription.py tests/unit/test_transcription.py tests/e2e/test_voice_capture_to_digest_e2e.py
pyright src/sra/capture/transcription.py
```

## Manual verification

- [ ] Replay the post-processor read-only against a stored transcript known to contain a loop, and confirm the word count drops with no reordering of unrelated text.
- [ ] Confirm a real capture through the existing ingestion flow stores one cleaned transcript.

**Verification (2026-10-17, macOS, Python 3.13.12):**

- Fake Whisper segments and isolated capture/API/database fixtures; plus a read-only replay of the post-processor against the previously repeated stored transcript.
- Unit/pipeline **35 passed**; voice-capture E2E **7 passed**; Ruff clean; Pyright zero errors.
- The E2E path submitted one cleaned transcript and persisted one study entry. Read-only replay reduced the observed transcript from **181 words to 81 words**, removing **100 looped words**.
- Before implementation, four unit cases and one E2E case retained all repeated copies. The implementation collapses adjacent case-insensitive units of four or more tokens, using the **smallest repeating unit** to protect one-, two-, and three-token natural repetition. The historical database row was deliberately not modified.
- The algorithm intentionally does not remove non-adjacent or differently punctuated text.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).