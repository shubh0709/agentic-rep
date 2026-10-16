SH-004 — `POST /api/v1/me/dev/pregenerate-now` discards `interview_plans_*` counts when `include_upcoming=true`

**Type:** Bug (silently drops accounting data on a code path that is type-annotated to include it)
**Source:** Application Self-Heal pass 1 (`tickets/self_heal/SH-001-coverage-matrix.md`)
**Priority:** Medium (no user-visible regression in today's quiz surface — the field is WIP — but the type contract is violated and the dev afforance lies to the operator about what was generated)
**Status:** Confirmed

## Summary

`src/sra/api/routers/dev.py` defines
`_generate_for_upcoming_entries(...) -> PregenerationSummary` and `pregenerate_now(...)` on top of it.
`PregenerationSummary` is declared in `src/sra/jobs/quiz_pregeneration.py` with
**eight** keys (the five MCQ counters plus `interview_plans_created`,
`interview_plans_skipped`, `interview_plans_errored`). The dev route builds the
upcoming summary with only the five MCQ counters and sums only those five into
the final `merged` dict, so every `InterviewPlan` that
`pregenerate_quizzes_for_user` produces on the natural path is silently dropped
from the dev response whenever `include_upcoming=true`.

## Preconditions / minimal reproduction

1. `pyright src/sra/api/routers/dev.py` (or `pyright` on the repo).
2. Two `Type "dict[str, int]" is not assignable to declared type "PregenerationSummary"`
   errors at `src/sra/api/routers/dev.py:52:37` and `:178:40`.
3. Each error names the missing `interview_plans_*` keys.

## Expected behavior

Both summaries and the merged total reflect whatever counter moved on the
server — even when the local helper did not generate any interview plans,
the summary must say so explicitly (`"interview_plans_created": 0`, …) so
the JSON shape stays stable across the `include_upcoming=true/false` paths
and the type contract holds at every assignment.

## Actual behavior (sanitized)

```
src/sra/api/routers/dev.py:52:12 - error: Type "dict[str, int]" is not assignable to declared type "PregenerationSummary"
   "interview_plans_created" is required in "PregenerationSummary"
   "interview_plans_skipped" is required in "PregenerationSummary"
   "interview_plans_errored" is required in "PregenerationSummary"
src/sra/api/routers/dev.py:178:40 - error: ... (same)
```

## Root cause

`_generate_for_upcoming_entries` mirrors the MCQ-only structure of the
natural pregeneration but is intentionally narrower — it does **not**
generate interview plans; it only generates `QuizSet`+`QuizQuestion`
records. The merge at the bottom of `pregenerate_now` therefore only
carries the five MCQ keys and the type alias flags the omission.

## Acceptance criteria

- [ ] `_generate_for_upcoming_entries` populates all eight
      `PregenerationSummary` keys (interview counters are zero for the
      synthetic path that does not call the planner).
- [ ] The `merged` dict sums all eight keys.
- [ ] `pyright` reports zero errors at the previous failing lines.
- [ ] Behavior of the existing dev-affordance API response shape stays
      a strict superset of the natural-path response — adding zero-valued
      fields is allowed; removing or renaming fields is not.

## Files to change

| File | Action | Reason |
|---|---|---|
| `src/sra/api/routers/dev.py` | (1) add the three `interview_plans_*` keys to the local `summary` literal with `0` values; (2) include those three keys in the `merged` aggregation. | Closes the type-contract violation and stops dropping interview-plan counts from the operator-visible response. |

## Verification record

- **Date / environment:** 2026-10-16, Python 3.13.12.
- **Closed by:** commit `6938f95 feat(jobs): interview counters in dev
  upcoming pregen + scheduler test fix (SRA-802 follow-up)`. The fix
  populates `interview_plans_created/skipped/errored` on the local
  summary literal in `_generate_for_upcoming_entries` (initialised
  to 0 — the upcoming pass only generates `QuizSet`/`QuizQuestion`,
  not `InterviewPlan`) and sums all three keys into the `merged`
  dict, so the dev response stays a strict superset of the natural
  pregen response.
- **Commands:**
  - `pyright src/sra/api/routers/dev.py` → 0 errors.
  - `pytest tests/unit/test_dev_pregenerate_route.py -v` → 6/6 PASS.
- **Behavior change:** the JSON body of `POST /api/v1/me/dev/pregenerate-now`
  now includes `summary.interview_plans_created/skipped/errored` on
  every response. The summary is a strict superset of the natural
  mode response shape (no fields were removed or renamed).
