# SRA-107 — Phase 1 integration verification

**Phase:** 1 — Core loop · **Status:** verified

## Summary

Not a new module — this ticket closes out Phase 1. Every piece has already been
proven individually: config (101), scheduler (102), repository (103), a running
server with digest/search (104), ingestion end to end (105), and chat end to end
(106). What hasn't been checked is all six in one continuous flow against one
server, one request after another — a different and weaker claim than "each piece
passed its own tests."

## Unlocks

- Phase 2 (voice capture) can build on a foundation whose whole loop is verified:
  capture (via pasted transcript) → structured storage → spaced-repetition
  scheduling → digest → search → conversational Q&A.

## Files

| File | Change | Why |
|---|---|---|
| [`PROGRESS.md`](../PROGRESS.md) | edit | Mark Phase 1 done. |
| [`tickets/README.md`](README.md) | edit | Mark this board's Phase 1 row done. |
| `sra.db` (local) | delete | Optional: reset the gitignored local database for a clean slate before Phase 2. |

## Acceptance criteria

- [x] `pytest -q` — full suite green, 0 failed, 0 errors
- [x] `pytest -q tests/e2e/` — all end-to-end tests green on their own
- [x] The manual flow below succeeds start to finish against a running server
- [x] `PROGRESS.md`'s Phase 1 section and this ticket board are updated to reflect
      Phase 1 as done

## Tests

```bash
source .venv/bin/activate
pytest -q
pytest -q tests/e2e/
```

Expected: all tests pass (no `NotImplementedError`s left anywhere). The end-to-end
digest test advances the date without waiting a day.

## Manual verification

- [ ] `uvicorn sra.api.app:create_app --factory --reload --port 8000`, then in a
      second terminal:

      ```bash
      # 1. Ingest a multi-topic transcript — expect 2 entries (Photosynthesis, Mitosis)
      curl -s -X POST http://localhost:8000/api/v1/entries/ingest \
        -H "Content-Type: application/json" \
        -d '{"transcript": "Today I covered photosynthesis - light and dark reactions - and also mitosis, the phases of cell division."}' | python3 -m json.tool
      # 2. Digest — no new entries yet; existing overdue reviews may appear.
      #    Run again tomorrow: both new entries should have interval_label "1d".
      curl -s http://localhost:8000/api/v1/digest | python3 -m json.tool
      # 3. Search — the new Mitosis entry (plus any pre-existing matches).
      curl -s "http://localhost:8000/api/v1/search?q=mitosis" | python3 -m json.tool
      # 4. Chat about it — an answer mentioning cell division
      curl -s -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
        -d '{"question": "what did I learn about mitosis?"}' | python3 -m json.tool
      # 5. Chat about something never studied — an honest "nothing found" answer
      curl -s -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
        -d '{"question": "what did I learn about quantum computing?"}' | python3 -m json.tool
      ```

- [ ] Mark Phase 1 done in `PROGRESS.md`; optionally delete the local `sra.db`
      for a clean slate (it's gitignored, so this is just local housekeeping).

### Verification record

- **2026-10-07, after switching to OpenAI:** 91 automated tests pass, including 3
  end-to-end tests also run independently. After replenishing API credits, the
  five live requests returned 201, 200, 200, 200, and 200.
- Ingestion created Photosynthesis (ID 2) and Mitosis (ID 3), with six pending
  checkpoints each and both first reviews scheduled for 2026-10-08. Today's digest
  returned only three overdue reviews for a pre-existing entry. A separate request
  through the real digest route with its date simulated as 2026-10-08 returned
  both new `1d` reviews without changing database dates. Search returned exactly
  Mitosis. The chat answers were:
  - Mitosis: "You studied the phases of cell division related to mitosis."
  - Quantum computing: "There are no records of your study about quantum computing."

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).