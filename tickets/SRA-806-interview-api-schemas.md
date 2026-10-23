# SRA-806 — Interview API schemas

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Adds the typed request / response DTOs the interview feature needs to `src/sra/api/schemas.py`: the `interview` mode value on `StartAttemptRequest`, the `InterviewTurnRequest` / `InterviewTurnResponse` pair, the read-only `InterviewPlanOut` / `InterviewTurnOut` / `InterviewSummaryOut` triple, `InterviewDetailOut`, the `cursor_on_plan` field on `QuizAttemptOut`, and the two new preference fields on `PreferencesIn` / `PreferencesOut`. Every new schema is a concrete Pydantic model, with `model_config = ConfigDict(extra="forbid")` on the request types so the SRA-302 ownership and SRA-304 rate-limit guarantees extend to the new surface without per-route validation. DTOs only: no HTTP handler and no LLM call is touched.

## Unlocks

- SRA-805 can type its route signatures and serialize the SRA-803 `InterviewTurnResult`; SRA-807's dashboard deserializes the same shapes without inline ad-hoc parsing.
- The FastAPI auto-generated OpenAPI docs describe the whole new surface, so the wire contract is inspectable without reading route code.

## Files

| File | Change | Why |
|---|---|---|
| `src/sra/api/schemas.py` | edit | The single source of truth for `/api/v1/*` shapes. Extend `StartAttemptRequest.mode` to `Literal["web","voice","interview"]`; add `InterviewOutlineItemOut`, `InterviewPlanOut`, `InterviewTurnOut`, `InterviewSummaryOut`, `InterviewTurnRequest`, `InterviewTurnResponse`, `InterviewDetailOut`; add `cursor_on_plan: int` to `QuizAttemptOut`; add `interview_question_count` (3–12) and `interview_target_minutes` (1–20) to `PreferencesIn` / `PreferencesOut`. Request types carry `ConfigDict(extra="forbid")`; read-only `*Out` types mirror the existing `QuizQuestionOut` / `QuizAttemptOut` style with no `extra` config. |
| `src/sra/db/models.py` | edit (read only) | The DTOs read the SRA-801 columns; no new model fields. |
| `src/sra/agents/interview_conversation_graph.py` | edit (read only) | SRA-803 owns the in-process `InterviewTurnResult`; SRA-806 defines the wire shape only. |
| `src/sra/api/routers/preferences.py` | edit (read only) | `PUT /api/v1/me/preferences` reads the extended `PreferencesIn`. No new endpoint. |
| `tests/unit/test_quiz_schemas.py` | edit | Every new DTO round-trips `model_validate(model_dump())`; `extra="forbid"` rejects an unknown key on `InterviewTurnRequest` and the extended `PreferencesIn`; `action` rejects out-of-set values; the two preference fields reject out-of-range values; the `*Out` types accept the same JSON the SRA-805 router emits. |
| `tests/unit/test_user_preferences.py` | edit | The two new fields participate in the existing range / serializer / round-trip tests. |

## Design notes

- **`InterviewTurnResult` (in-process) vs `InterviewTurnResponse` (wire).** The in-process shape carries `transcript_so_far` as typed `InterviewTurn` records for the graph's own resume and summary logic; the wire shape carries it as `InterviewTurnOut` DTOs the dashboard renders directly. Splitting them keeps the agent free of HTTP / serialization concerns and lets the wire shape evolve independently.
- **`InterviewPlanOut` is separate from the attempt.** The plan lives on the `QuizSet` (one per set, generated once per day) while the attempt lives on the user (created at start). Separate shapes give `GET /quiz/attempts/{id}/interview` clear ownership and let the dashboard render the plan before an attempt exists.
- **`InterviewTurnRequest` uses `model_validator(mode="after")`**, not a per-field validator, for the cross-field rule "non-empty `answer_text` when `action == 'answer'`" — per-field validators cannot read sibling fields.
- **`InterviewTurnOut.score` / `outline_index` default to `None`** so a router can emit an `ai` turn with just `(id, role, content, created_at)`; the round-trip test relies on this.
- **`StartAttemptRequest` was given `extra="forbid"`** even though the ticket did not name it a strict body — allowing unknown keys would hide SRA-805 schema drift.

## Acceptance criteria

- [ ] Every new DTO compiles, type-checks, and round-trips through `model_validate(model_dump())`
- [ ] `extra="forbid"` rejects unknown keys on `InterviewTurnRequest` and on the extended `PreferencesIn`
- [ ] `action` rejects values outside `{"start","answer","next","end"}`; `answer_text` is required and non-empty when `action == "answer"`
- [ ] `interview_question_count` rejects values outside `[3, 12]`; `interview_target_minutes` rejects values outside `[1, 20]`
- [ ] `QuizAttemptOut.cursor_on_plan` is always present (default `0`)
- [ ] The SRA-805 router can import every new DTO without a circular import
- [ ] The FastAPI auto-generated OpenAPI docs at `/api/v1/openapi.json` describe every new DTO with the documented field names
- [ ] No production credentials, no live OpenAI calls, and no Phase 1–7 regression tests are broken by this ticket

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_quiz_schemas.py tests/unit/test_user_preferences.py -v
pytest tests/unit/test_quiz_models.py tests/integration/test_phase8_migration.py -v
pytest -m 'not phase8' -q --ignore=tests/unit/test_auth_refresh.py --ignore=tests/integration/test_init_db.py
ruff check .
pyright
```

Each new DTO gets at least one round-trip and one `extra="forbid"` test; no new fixtures are required.

## Manual verification

Start the server and open `http://localhost:8000/api/v1/openapi.json`.

- [ ] `InterviewPlanOut`, `InterviewTurnOut`, `InterviewSummaryOut`, `InterviewTurnRequest`, `InterviewTurnResponse`, `InterviewDetailOut` appear under `components.schemas`.
- [ ] `StartAttemptRequest.mode` enum contains `"interview"`; `QuizAttemptOut` carries `cursor_on_plan`; `PreferencesIn` / `PreferencesOut` carry both `interview_question_count` and `interview_target_minutes`.

**Verification (2026-10-15):**

- Environment: darwin, repo at master `93fddf7` (Phase 6 close); Phase 8 scaffold files (SRA-801–804) already on disk.
- `pytest tests/unit/test_quiz_schemas.py tests/unit/test_user_preferences.py -v` → **96 passed**, including all 24 new SRA-806 phase8 tests.
- `pytest tests/unit/test_quiz_models.py tests/integration/test_phase8_migration.py -v` → **53 passed**.
- Regression `pytest -m 'not phase8' -q --ignore=…` → **785 passed, 24 failed, 14 skipped, 153 deselected**. The 24 failures match the pre-existing SRA-605 / SRA-801 baselines (verified by `git stash` reverting only `schemas.py`); no new regressions.
- OpenAPI spec snapshot: **not captured** (manual server start deferred). The DTOs are import-clean and round-trip via `model_validate(model_dump())`, satisfying the auto-generated schema contract.
- `ruff check .` clean; the 1 pre-existing unused `typing.Any` import at `src/sra/jobs/quiz_pregeneration.py:17` is unchanged. `pyright` → 10 pre-existing errors (9 documented in SRA-803 + 1 at `src/sra/api/routers/dev.py:178` for `PregenerationSummary`); `schemas.py` is clean.
- > **Note:** the status line still reads `Planned`.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).