# SRA-905 — Phase 9 integration verification + README

**Phase:** 9 — Topic authoring with reference materials + question-quality uplift · **Status:** not started

## Summary

Closes Phase 9: re-runs the full regression matrix on the new pipeline, adds the four cross-cutting integration tests that span SRA-901–904, updates the README with a "Creating a topic with references" walkthrough and a "How we generate questions" rubric, and seals the migration record in `PROGRESS.md`. The pre-Phase-9 baseline is the SRA-509 (Phase 5) close-out record: roughly **479 passed / 14 skipped / 0 Phase 5-attributed failures**, with the documented Phase 3/4/6/8 limitations unchanged. This ticket adds no new behaviour — every test here is either net-new integration coverage or a sanity check on the *rendered* prompt of the existing prompts.

## Unlocks

A user-visible claim backed by reproducible evidence: sign in, click **Create topic**, type "Transformer attention heads", add a URL and a pasted snippet, click **Generate now** — the dashboard renders a new entry card with **Start quiz** and **Start interview** links, and both are demonstrably grounded in the source material (open any question's explanation and the cited passage is in the source; open any interview turn and the rationale references the planner's rubric).

## Files

| File | Change | Why |
|---|---|---|
| `tests/integration/test_phase9_end_to_end.py` | new | Topic → references → quiz → interview start → quiz question → submit answer → interview answer → finish; partial success on LLM failure; cross-user isolation. |
| `tests/integration/test_phase9_route_policy_audit.py` | new | `/api/v1/topics` and the regenerate endpoint appear under `user` + `limit_authenticated`. |
| `tests/integration/test_phase9_cascade.py` | new | `delete_user_cascade` drops `reference_materials` for the user's `study_entries`. |
| `tests/integration/test_phase9_prompt_smoke.py` | new | The rendered system prompts of `quiz_graph.generate_questions` and `interview_planner._call_planner` both contain all six rubric sections, asserted as exact textual fragments from those modules' constants. |
| `README.md` | edit | `## Creating a topic with references` after the "Submit a typed note from Python" block (screenshot-style description, copy-pasteable `curl`, and a bullet list of what each reference kind does); `## How we generate questions` pointing at the SRA-903 / SRA-904 rubric as the source of truth; env-var row for `OPENAI_VERIFY_MCQS` (default `true`); a row noting `trafilatura` is in base `dependencies` (no extra group); Phase 9 marked `[x]` in the build-phases checklist. |
| `PROGRESS.md` | edit | A `## Phase 9 — …` section at the bottom mirroring SRA-509's per-ticket close-out pattern: focused test count, integration suite outcome, ruff/pyright, open Phase 4 / 8 documentation limitations, and real OpenAI evidence. |
| `tickets/README.md` | edit | Phase 9 board with the five SRA-9xx entries and the dependency diagram. |

## Design notes

- **The end-to-end test** sets up a real-DB session with a fresh user, one `StudyEntry`, and two `reference_materials` rows (one paste, one URL — the URL mocked via a fake `httpx.Client` or by stubbing `sources.fetcher.fetch_url_text`), then runs `POST /api/v1/topics`. It asserts the response carries a `quiz_set_id` and an `interview_plan_id`; runs `GET /api/v1/quiz/{quiz_set_id}` and confirms every returned question has the three new fields populated; runs `POST /api/v1/quiz/{quiz_set_id}/attempts` with `mode="interview"`, then `…/interview-turn {action:"answer", answer_text:"<covers the rubric>"}`, then a follow-up turn that completes the attempt, and confirms the rationale references the supplied source passage.
- **The cascade test** proves `delete_user_cascade(user)` removes every `reference_materials` row even when FK enforcement is off — the same SQLite caveat SRA-901 had to handle explicitly.

## Acceptance criteria

- [ ] Default `pytest -q` result is ≥ the SRA-509 baseline; no new failures; no new skips
- [ ] `ruff check .` clean
- [ ] `pyright` clean on the changed modules (matches the SRA-509 record)
- [ ] Live verification records one new topic entry + quiz set + interview transcript anchored to its source references
- [ ] Migration round-trip + replay preserves pre-Phase-9 data
- [ ] README has the new sections; PROGRESS.md closes Phase 9

## Tests

```bash
pip install -e '.[dev]'
alembic upgrade head
pytest tests/integration/test_phase9_end_to_end.py tests/integration/test_phase9_cascade.py tests/integration/test_phase9_route_policy_audit.py tests/integration/test_phase9_prompt_smoke.py -v
pytest -q
ruff check .
pyright
```

`tests/integration/test_phase8_end_to_end.py` is the closest precedent for the end-to-end shape; `tests/integration/test_phase5_end_to_end.py` for the wakeup-loop evidence pattern.

## Manual verification

Live run with the user's own real API key:

```bash
export OPENAI_API_KEY=...
export OPENAI_VERIFY_MCQS=true
export OPENAI_MODEL=gpt-4.1
alembic upgrade head
python -m uvicorn sra.api.app:create_app --factory --no-access-log
# In another shell, with a real session cookie:
curl -b sra_session=… -X POST http://127.0.0.1:8000/api/v1/topics \
  -H 'Content-Type: application/json' \
  -d @phase9_topics_demo.json
# Then run /quiz/{quiz_set_id}/attempts … and the interview route
```

- [ ] Capture the new entry id, the quiz set id, the interview plan id, the rendered 5 MCQs' `source_passage_quote` and `bloom_level`, and one full interview attempt transcript including rationale; paste the raw output into the verification record.
- [ ] Clean up the disposable SQLite file after the run (it is already in `.gitignore`).

> **Note:** the verification record for this ticket is unfilled — no actual run has been recorded.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).