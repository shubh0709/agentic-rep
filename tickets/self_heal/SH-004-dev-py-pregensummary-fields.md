# SH-004 — `POST /api/v1/me/dev/pregenerate-now` discards `interview_plans_*` counts when `include_upcoming=true`

**Status:** closed

## Bug

`PregenerationSummary` is declared in `src/sra/jobs/quiz_pregeneration.py` with **eight** keys (the five MCQ counters plus `interview_plans_created`, `interview_plans_skipped`, `interview_plans_errored`). `src/sra/api/routers/dev.py` defines `_generate_for_upcoming_entries(...) -> PregenerationSummary` and `pregenerate_now(...)` on top of it, but the dev route builds the upcoming summary with only the five MCQ counters and sums only those five into the final `merged` dict — so every `InterviewPlan` that `pregenerate_quizzes_for_user` produces on the natural path is silently dropped from the dev response whenever `include_upcoming=true`.

Reproduce: `pyright src/sra/api/routers/dev.py` → two `Type "dict[str, int]" is not assignable to declared type "PregenerationSummary"` errors at `src/sra/api/routers/dev.py:52:37` and `:178:40`, each naming the missing `interview_plans_*` keys.

Root cause: `_generate_for_upcoming_entries` mirrors the MCQ-only structure of the natural pregeneration but is intentionally narrower — it does **not** generate interview plans, only `QuizSet`+`QuizQuestion` records. The merge at the bottom of `pregenerate_now` therefore only carries the five MCQ keys, and the type alias flags the omission. The consequence is not a user-visible regression today (the field is WIP) but a violated type contract and a dev affordance that lies to the operator about what was generated.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/api/routers/dev.py` | edit | (1) Add the three `interview_plans_*` keys to the local `summary` literal with `0` values; (2) include those three keys in the `merged` aggregation. Closes the type-contract violation and stops dropping interview-plan counts from the operator-visible response. |

## Fix

Both summaries and the merged total now reflect whatever counter moved on the server. Even when the local helper generated no interview plans, the summary says so explicitly (`"interview_plans_created": 0`, …) so the JSON shape stays stable across the `include_upcoming=true/false` paths and the type contract holds at every assignment.

## Acceptance criteria

- [x] `_generate_for_upcoming_entries` populates all eight `PregenerationSummary` keys (interview counters are zero for the synthetic path that does not call the planner)
- [x] The `merged` dict sums all eight keys
- [x] `pyright` reports zero errors at the previous failing lines
- [x] The dev-affordance response stays a strict superset of the natural-path response — adding zero-valued fields is allowed; removing or renaming fields is not

## Verification

- **Date / environment:** 2026-10-16, Python 3.13.12.
- **Closed by:** commit `6938f95 feat(jobs): interview counters in dev upcoming pregen + scheduler test fix (SRA-802 follow-up)`.
- `pyright src/sra/api/routers/dev.py` → **0 errors**.
- `pytest tests/unit/test_dev_pregenerate_route.py -v` → **6/6 PASS**.
- **Behavior change:** the JSON body of `POST /api/v1/me/dev/pregenerate-now` now includes `summary.interview_plans_created/skipped/errored` on every response. The summary is a strict superset of the natural-mode response shape — no fields were removed or renamed.

Run summary: [`SH-REPORT.md`](SH-REPORT.md).

---

Design context: [`technical-design.md`](../../docs/design/technical-design.md). Process:
[`guides/self_heal.md`](../../guides/self_heal.md). Source: [`SH-001`](SH-001-coverage-matrix.md).