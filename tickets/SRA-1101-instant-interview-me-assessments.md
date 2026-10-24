# SRA-1101 — Instant “Interview Me” assessments

**Type:** Feature
**Phase:** 11 — On-demand assessments
**Priority:** High — turns the existing generators and interview loop into a user-directed workflow
**Status:** Implemented; automated verification complete
**Blocked by:** SRA-901, SRA-904, SRA-807
**Blocks:** Phase 11 verification and adaptive weak-topic retries

## Summary

Add an authenticated **Interview Me** workflow where a user supplies a topic and
one or more pasted-text, URL, PDF, DOCX, TXT, or Markdown references; reviews
AI-suggested source-grounded topics; chooses MCQ, AI interview, or both; chooses
question counts; completes the assessment; and later reopens a saved result with
answers, feedback, strengths, weaknesses, and recommended deep dives.

This is an on-demand assessment, not a daily scheduled quiz. It reuses the Phase
8 interview planner/conversation graph and Phase 9 reference extractors, while an
`AssessmentSession` groups preparation, child attempts, and the final report.

## Where things stand right now

- `POST /api/v1/topics` already ingests up to ten references and can synchronously
  generate a daily `QuizSet` and one `InterviewPlan`.
- MCQ answers are persisted in `quiz_answers`; interview transcripts, scores, and
  feedback are persisted in `interview_turns` and can be resumed.
- The existing flow generates before the user can review suggested concepts, uses
  global preference counts, does not group MCQ + interview as one session, has no
  assessment history, and does not persist the grader rationale or a structured
  strengths/weaknesses/deep-dive report.

## What this ticket unlocks

A signed-in user clicks **Interview Me**, supplies reference material, chooses
concepts suggested from that material, configures a five-question MCQ plus a
three-question AI interview, completes both in sequence, and receives one durable
result. Refreshing or revisiting the history page reloads the same attempts and
report rather than invoking the LLM again.

## How this fits into the whole app

```mermaid
flowchart TD
    A[Interview Me button] --> B[POST /assessments/analyze]
    B --> C[Existing URL fetcher and document extractor]
    C --> D[StudyEntry + ReferenceMaterial rows]
    D --> E[Source-grounded topic suggestions]
    E --> F[User selects topics, mode, and counts]
    F --> G[POST /assessments/{id}/generate]
    G --> H[Existing MCQ generator]
    G --> I[Existing interview planner]
    H --> J[MCQ attempt]
    I --> K[Interview attempt and persisted turns]
    J --> L[Assessment report builder]
    K --> L
    L --> M[Saved results + history]
```

The assessment routes own orchestration and authorization. Existing topic source
services still own retrieval/extraction, quiz code still owns multiple-choice
generation and grading, and the interview graph still owns conversational turns.
The browser never supplies ownership IDs or computed scores.

## Files and why they matter

| File | Responsibility |
|---|---|
| `src/sra/db/models.py` | Add the parent session and durable structured report using the repository Base/Create/Table convention. |
| `src/sra/alembic/versions/*_phase11_assessments.py` | Upgrade/downgrade the schema without disturbing existing attempts. |
| `src/sra/db/repository.py` | User-scoped session creation, configuration, child-attempt linking, history, and report persistence. |
| `src/sra/agents/assessment_analyzer.py` | Produce validated source-grounded topic suggestions through an injectable model boundary. |
| `src/sra/agents/assessment_reporter.py` | Build the saved report from persisted answers/evaluations and source-backed topic metadata. |
| `src/sra/api/schemas.py` | Strict analyze, configure/generate, session, topic-suggestion, and results wire contracts. |
| `src/sra/api/routers/assessments.py` | Authenticated analyze/generate/start/read/history/results orchestration. |
| `src/sra/api/routers/__init__.py` | Mount the assessment router under `/api/v1`. |
| `web/routes.py` | Serve protected `/interview-me` and results/history views. |
| `web/templates/_nav.html`, `web/templates/dashboard.html` | Add the prominent entry points. |
| `web/templates/interview_me.html`, `web/static/js/interview-me.js`, `web/static/css/site.css` | Accessible wizard, MCQ runner/handoff, resume, report, and history UI. |
| `src/sra/agents/interview_conversation_graph.py` | Persist grading rationale in the assessment result and notify report finalization without changing legacy interview semantics. |
| `tests/unit/` | Pin schemas, repository ownership, analyzer/report contracts, and browser state transitions. |
| `tests/e2e/` | Exercise the real app and database from references through a saved combined result, replacing only network fetch and model inference. |

## Contracts

- `POST /api/v1/assessments/analyze` accepts a topic plus 1–10 references using
  the existing `url | paste | upload` discriminator. It persists the draft and
  returns 2–12 unique suggestions. Each suggestion has a stable key, label,
  description, and non-empty evidence excerpt.
- `POST /api/v1/assessments/{id}/generate` accepts selected suggestion keys,
  `mode: mcq | interview | both`, `mcq_question_count` in 1–20, and
  `interview_question_count` in 3–12. It rejects foreign sessions and unknown or
  empty selections, then creates only the requested artifacts.
- `POST /api/v1/assessments/{id}/start/{mode}` starts only a configured child
  mode and records its attempt on the parent session. Repeated starts return the
  resumable existing attempt rather than duplicating it.
- `GET /api/v1/assessments` returns the caller’s sessions newest-first.
- `GET /api/v1/assessments/{id}` returns draft/configuration/progress state.
- `GET /api/v1/assessments/{id}/results` returns immutable question/answer
  snapshots, per-answer grading, overall score, strengths, weaknesses, deep-dive
  recommendations, and source evidence. It never exposes another user’s data.
- The first results read after all required child attempts complete builds and
  persists the report idempotently. A `both` session is final only after both
  child attempts complete.
- Reports are persisted and versioned; reading results performs no model call.

## TDD starting point

Write failing tests before production code. Unit tests isolate validation,
ownership, idempotency, and report aggregation. End-to-end tests keep FastAPI,
SQLModel, repositories, routes, and browser workflow real while substituting URL
network access and model inference with handwritten fakes.

## Acceptance criteria

- [x] Authenticated navigation and dashboard expose **Interview Me**.
- [x] A user can combine paste, URL, and supported upload references and see
      per-reference extraction failures without losing successful references.
- [x] Suggested topics are source-grounded and selectable, and selection is
      validated server-side.
- [x] MCQ, interview, and both modes respect per-session question counts without
      changing global preferences.
- [x] Both mode runs MCQ first and then the AI interview under one session.
- [x] Progress survives refresh and duplicate start/generate requests do not make
      duplicate attempts.
- [x] Results save the questions, answers, correctness/scores, AI feedback,
      grading rationale, strengths, weaknesses, and deep-dive recommendations.
- [x] Completed and in-progress assessments appear in owner-scoped history and
      can be reopened.
- [x] Empty references, unsupported uploads, failed extraction, invalid counts,
      unknown selections, cross-user reads, model failure, and partial generation
      return explicit recoverable states.
- [x] Unit and end-to-end coverage passes with no live network or OpenAI calls;
      the regression suite, Ruff, and Pyright remain green.

## Verification record

**2026-10-24, macOS / Python 3.13:**

- TDD red step: the tests-only subagent added the unit and e2e contracts; the
  focused command collected cleanly with **12 expected failures** for missing
  Phase 11 behavior, and its two test files passed Ruff.
- `pytest tests/unit/test_assessment_contracts.py tests/e2e/test_phase11_interview_me_e2e.py -q`
  → **12 passed**. The e2e test used pasted text and handwritten inference fakes;
  FastAPI, auth, SQLModel, quiz/interview routes, persistence, history, isolation,
  and cached report reads remained real.
- Migration and route-policy focus → **26 passed**.
- `pytest -q` with loopback socket access → **1246 passed, 36 skipped** in 36.12s.
  The first sandboxed run produced three expected localhost-bind failures plus an
  initially missing route-audit entry; the audit was updated and the unrestricted
  rerun was green.
- `ruff check .` → **All checks passed**. `pyright` → **0 errors**.
- `node --check web/static/js/interview-me.js` → exit 0.
- Not executed: live OpenAI topic analysis/question generation and a real-browser
  visual/accessibility pass. These are not implied by the offline result.
