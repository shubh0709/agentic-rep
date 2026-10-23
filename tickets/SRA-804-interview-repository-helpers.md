# SRA-804 — Interview repository helpers

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds the five repository helpers the interview feature needs in `src/sra/db/repository.py`: `create_interview_plan` (already referenced by SRA-802 — this ticket owns its implementation), `get_or_create_interview_plan` (the lazy-create path SRA-805's router uses when pregeneration missed an entry), `append_interview_turn` (typed insert for `ai` / `user` / `system_summary` rows), `get_interview_turns` (ordered read for resume and the summary), and `complete_interview_attempt` (rounded mean user-turn score plus `utcnow`). Every helper enforces ownership through the parent `QuizSet` / `QuizAttempt` rows the way the existing Phase 4/5 helpers do. Storage only — no LLM is called here, and these are the only writers to `interview_turns` outside the conversation graph.

## Unlocks

- SRA-805's router can call `append_interview_turn` on every turn and `get_interview_turns` to render the summary panel; SRA-808's voice client can call `get_or_create_interview_plan` when pregeneration never produced an outline.
- The graph, router, and voice client all call typed helpers and inherit the existing Phase 4/5 ownership and cascade guarantees instead of each inlining SQL.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/db/repository.py` | edit | The five helpers below, following the existing free-function style (no per-feature class — tests patch one function and the SQL stays in one place). |
| `src/sra/db/models.py` | read only | The SRA-801 classes and `QuizAttempt.cursor_on_plan` are consumed here; no model changes. |
| `src/sra/jobs/quiz_pregeneration.py` | read only | SRA-802's `pregenerate_quizzes_for_user` imports `create_interview_plan` from here. |
| `tests/unit/test_quiz_repository.py` | edit | Idempotent `create_interview_plan`; `get_or_create_interview_plan` existing / missing / placeholder paths; `append_interview_turn` rejecting unknown `role` and empty content; `get_interview_turns` `id` ASC ordering; `complete_interview_attempt` rounding, non-null `completed_at`, double-call `ValueError`. |
| `tests/integration/test_phase8_cascade.py` | new | Create a user, entry, `QuizSet`, `InterviewPlan`, and `InterviewTurn` rows, call `delete_user_cascade`, assert both new tables are empty. Mirrors the SRA-602 cascade test. |

## Design notes

- **`create_interview_plan(*, quiz_set, outline, target_questions, session)`** wraps `InterviewPlanCreate` through `model_validate(update=...)` (mirroring `create_quiz_set`), validates `len(outline) == target_questions`, and on `IntegrityError` rolls back, re-reads the row by `quiz_set_id`, and returns it. Returns `None` for an empty outline so the SRA-802 job can count it as errored.
- **`get_or_create_interview_plan(quiz_set_id, session) -> tuple[QuizSet, InterviewPlan]`** raises `ValueError` when the parent `QuizSet` is missing. If no plan exists it builds a one-item placeholder (`subtopic="general"`, `difficulty="medium"`, `focus_prompt=entry.summary[:240]`) so the graph always has something to expand; SRA-802 replaces it with the real outline on the next run.
- **`append_interview_turn`** validates `role` against `InterviewTurnRole`, strips `content` (`ValueError` when empty), defaults `created_at` to `_utcnow()`.
- **`get_interview_turns`** is `SELECT … WHERE attempt_id = :id ORDER BY id ASC`, returning `[]` when nothing matches.
- **`complete_interview_attempt`** raises `ValueError` when `completed_at is not None`; sets `score = max(0, min(100, round(mean_score)))` and `completed_at = _utcnow()`, commits, returns the attempt.
- **Lazy-create lives in the router, not the graph**, which keeps the graph purely per-turn while pregeneration owns the "one plan per day" invariant.

## Acceptance criteria

- [ ] All five helpers compile, type-check, and pass their focused unit tests
- [ ] `create_interview_plan` is idempotent: two calls with the same `quiz_set_id` return the same row
- [ ] `get_or_create_interview_plan` raises `ValueError` when the parent `QuizSet` is missing and creates a placeholder when the `InterviewPlan` is missing
- [ ] `append_interview_turn` rejects an unknown `role` and an empty `content`
- [ ] `get_interview_turns` orders rows by `id` ASC; an empty attempt returns an empty list
- [ ] `complete_interview_attempt` sets `score` to the rounded mean and `completed_at` to a non-null timezone-aware value; calling it twice raises `ValueError`
- [ ] `delete_user_cascade` removes every `interview_plans` and `interview_turns` row owned by the user; the integration test proves the cascade
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_repository.py tests/integration/test_phase8_cascade.py tests/unit/test_interview_conversation_graph.py tests/integration/test_phase8_voice_turn_e2e.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

`conftest.py::session` uses StaticPool with no PRAGMA FK enforcement — matching the existing Phase 4/5/7 cascade tests, which is why the cascade is proven with explicit deletes rather than relying on the engine.

## Manual verification

With the SRA-801 migration applied and one user holding one `QuizSet`, open a `sqlite3 sra.db` session:

```sql
INSERT INTO interview_plans (quiz_set_id, outline, target_questions)
VALUES (1, '[{"subtopic":"x","difficulty":"easy","focus_prompt":"y"}]', 1);
INSERT INTO interview_plans (quiz_set_id, outline, target_questions)
VALUES (1, '[{"subtopic":"x","difficulty":"easy","focus_prompt":"y"}]', 1);
-- Second insert raises IntegrityError on uq_interview_plans_quiz_set_id.
SELECT count(*) FROM interview_turns WHERE attempt_id = 1;
-- 0 after a fresh attempt with no turns.
```

- [ ] The duplicate insert fails on `uq_interview_plans_quiz_set_id` from a raw `sqlite3` session.

**Verification (2026-10-15):**

- macOS, Python 3.13.12, in-memory SQLite via `tests/conftest.py::session`.
- `tests/unit/test_quiz_repository.py` → **20 passed in 0.44s** (9 pre-existing Phase 4 + 11 new). `tests/integration/test_phase8_cascade.py` → **1 passed in 0.31s**.
- `tests/unit/test_interview_conversation_graph.py tests/integration/test_phase8_voice_turn_e2e.py` → **13 passed in 0.56s**; the first-ai-turn-before-grading-call ordering test still returns before the LLM fires.
- Regression `pytest -m 'not phase8' -q --ignore=…` → **785 passed, 24 failed, 14 skipped, 153 deselected** — the 24 failures are the pre-existing Phase 1–7 limitations from SRA-605, confirmed by stashing the diff and re-running a Phase 4 subset (same 8 failures). No new regressions.
- Hand-driven cascade counts before → after for one demo user: 1 / 1 / 1 / 2 → 0 / 0 / 0 / 0. The cascade is now portable to engines without PRAGMA-enforced FKs.
- **Deviation:** the ticket assumed no `delete_user_cascade` change was needed because the SRA-801 model relied on `ondelete="CASCADE"` FKs it never declared. Explicit `interview_plans` / `interview_turns` deletes were added, matching the existing `quiz_answers` pattern.
- `ruff check .` → **1 error** (pre-existing unused `typing.Any` at `quiz_pregeneration.py:17` from SRA-802). `pyright` → **10 errors**, all pre-existing (`interview_planner.py:198`, `api/routers/dev.py:52/178`, 7 in `test_auth_client.py`); SRA-804 code is clean.
- > **Note:** the status line still reads `Planned`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).