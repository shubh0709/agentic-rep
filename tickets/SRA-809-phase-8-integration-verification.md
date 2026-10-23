# SRA-809 — Phase 8 integration verification

**Phase:** 8 — Interactive AI interview mode · **Status:** not started

## Summary

Phase 8 ships the interactive AI interview mode end to end. After SRA-801 through SRA-808 land, this ticket re-runs the full automated suite, rehearses the migration, and records the manual evidence runs (web loop, voice loop, resume, recovery) that prove the new surface works on real components. It adds no new code — it is the closing checkpoint the repo's phase model requires, and it re-runs every automated check the earlier phase-verification tickets ran (SRA-107, SRA-206, SRA-305, SRA-406, SRA-509, SRA-605) plus the new Phase 8 checks. It is the only ticket that touches the full repository at once.

## Unlocks

- Phase 8 becomes part of the verified application: database, agents, router, dashboard, voice client, and migration all proven together.
- Later phases start from a green baseline; no new ticket depends on SRA-809.

## Files

| File | Change | Why |
|---|---|---|
| `PROGRESS.md` | edit | Add the Phase 8 close-out entry with date, environment, command outputs, and the manual evidence runs. Mark Phase 8 Done. |
| `README.md` | edit | Update the Phase 8 board entry from "Planned" to the actual status with date. Confirm the interview mode line item in the API rate-limit table (it inherits the per-user bucket). |
| `tickets/README.md` | edit | Confirm the Phase 8 board reflects SRA-801 through SRA-809. |
| `tests/integration/test_phase8_migration_replay.py` | new | The migration round-trip: upgrades a Phase 5/6-shaped file DB through the SRA-801 revision, runs the Phase 5/6 + Phase 8 row inserts, downgrades, re-upgrades, then asserts every pre-existing row has the same IDs and timestamps. Mirrors `tests/integration/test_phase5_migration_replay.py`. |
| Phase 8 unit files: `test_interview_planner`, `test_interview_conversation_graph`, `test_quiz_repository`, `test_quiz_schemas`, `test_quiz_routes`, `test_quiz_models`, `test_interview_dashboard`, `test_api_client`, `test_voice_quiz_client` | read only | Owned by SRA-801…808; exercised as part of the full suite. |
| Phase 8 integration/e2e files: `test_phase8_migration`, `test_phase8_cascade`, `test_phase8_end_to_end`, `test_phase8_voice_turn_e2e`, `test_phase8_interview_routes_e2e`, `test_phase8_rate_limit_inheritance`, `test_route_policy_audit`, `test_phase8_interview_dashboard_e2e`, `test_phase8_voice_interview_e2e` | read only | Owned by SRA-801…808; exercised here. |

## Design notes

- **The migration round-trip matters specifically here.** The SRA-801 migration is the only Phase 8 code that mutates the database schema. If the upgrade / downgrade / upgrade cycle regresses a Phase 5/6 user, existing data is at risk. The round-trip evidence is the only way to prove the cascade chain on `interview_plans` and `interview_turns` survives a downgrade without orphaning rows.
- **The `pytest -q` run is the regression gate; the manual evidence is the closing record.**

## Acceptance criteria

- [ ] Every Phase 8 focused, integration, and e2e test passes individually
- [ ] `pytest -q` (no marker) is green; no Phase 1–7 regression is broken
- [ ] `test_phase8_migration_replay.py` proves upgrade / downgrade / upgrade preserves every pre-Phase-8 row
- [ ] `ruff check .` is clean
- [ ] `pyright` is clean (0 errors, 0 warnings)
- [ ] The manual web loop records a new attempt ID, the mean score, and the transcript order against a real OpenAI key
- [ ] The manual voice loop records the spoken TTS sequence and the returned `{score, answered_count, remaining_count}` against a real OpenAI key
- [ ] The manual resume and recovery runs are recorded
- [ ] `PROGRESS.md` and `README.md` reflect the actual Phase 8 state
- [ ] `tickets/README.md` board reflects the actual state of every SRA-8xx ticket

## Tests

```bash
source .venv/bin/activate
pip install -e '.[dev,capture,desktop]' -c requirements-lock.txt

# Focused suite
pytest tests/unit/test_interview_planner.py -v
pytest tests/unit/test_interview_conversation_graph.py -v
pytest tests/unit/test_quiz_repository.py -v
pytest tests/unit/test_quiz_schemas.py -v
pytest tests/unit/test_quiz_routes.py -v
pytest tests/unit/test_quiz_models.py -v
pytest tests/unit/test_interview_dashboard.py -v
pytest tests/unit/test_api_client.py -v
pytest tests/unit/test_voice_quiz_client.py -v

# Integration suite
pytest tests/integration/test_phase8_migration.py -v
pytest tests/integration/test_phase8_migration_replay.py -v
pytest tests/integration/test_phase8_cascade.py -v
pytest tests/integration/test_phase8_end_to_end.py -v
pytest tests/integration/test_phase8_voice_turn_e2e.py -v
pytest tests/integration/test_phase8_interview_routes_e2e.py -v
pytest tests/integration/test_phase8_rate_limit_inheritance.py -v
pytest tests/integration/test_route_policy_audit.py -v

# End-to-end suite
pytest tests/e2e/test_phase8_interview_dashboard_e2e.py -v
pytest tests/e2e/test_phase8_voice_interview_e2e.py -v

# Full regression
pytest -q

# Lint and types
ruff check .
pyright
```

Expected: every test passes; `-q` shows the same total as before SRA-801 landed, plus the new Phase 8 tests. Note the Phase 5 caplog isolation quirk — those two tests only pass via the explicit file list, so the unmapped `pytest -q` needs care. See [`tests/README.md`](../tests/README.md).

## Manual verification

### Web loop

Drive `start` → 3 answers → `next` → 1 answer → `end`, then `GET /interview`:

```bash
TOKEN=$(...) ; QUIZ_SET_ID=$(...)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"mode":"interview"}' \
  "http://127.0.0.1:8000/api/v1/quiz/${QUIZ_SET_ID}/attempts" | python3 -m json.tool
# {"attempt_id": 12, "question_text": "...", "transcript_so_far": [...]}
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"answer","answer_text":"..."}' \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/12/interview-turn" | python3 -m json.tool
# ... (repeat for the 4 user turns)
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"end"}' \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/12/interview-turn" | python3 -m json.tool
# {"finished": true, "summary": "...", "score": N, ...}
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://127.0.0.1:8000/api/v1/quiz/attempts/12/interview" | python3 -m json.tool
# {"plan": {...}, "turns": [...], "summary": {...}, "attempt": {...}}
```

- [ ] `summary.answered_count` matches the number of `user` turns, `summary.remaining_count` matches the unconsumed outline items, and `summary.score` matches the mean of the `user`-turn scores.

### Voice loop

```bash
# With the menu-bar app running and a real OpenAI key configured
python3 -m sra.capture.voice_quiz_client
# (select the interview quiz set, answer two questions out loud, end the interview)
# Expected: the menu-bar status shows the saved score and the remaining count.
```

### Resume and recovery

- [ ] **Resume:** close the tab after the 2nd user turn, reopen `/dashboard/interview?attempt_id=12`, confirm the transcript renders, the input area is enabled, and the 3rd turn posts correctly.
- [ ] **Recovery:** kill and restart the API, then `GET …/interview` returns `{"plan": {...}, "turns": [...], "summary": null, "attempt": {...}}`. Confirm the transcript hash matches the pre-restart hash and the attempt is still open.

> **Note:** the verification record for this ticket is entirely unfilled — no actual run has been recorded, so no results are claimed here.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).