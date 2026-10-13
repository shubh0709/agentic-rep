# SRA-107 — Phase 1 integration verification

**Type:** Verification (no new code expected)
**Phase:** 1 — Core loop
**Priority:** Highest — this is the "Phase 1 is actually done" checkpoint
**Status:** Pending implementation
**Blocked by:** SRA-105, SRA-106 (transitively: all of SRA-101 through SRA-106)
**Blocks:** Phase 2 kickoff

## Summary

Not a new module — this ticket is closing out Phase 1. Run the full test suite and the manual end-to-end flow, and confirm the app genuinely works as a whole, not just module by module.

## Where things stand right now

By this point every piece has already been proven individually: config (101), scheduler (102), repository (103), a running server with digest/search (104), ingestion end to end (105), and chat end to end (106). Each ticket's own manual verification already showed its slice working. What hasn't been checked yet is all six pieces in one continuous flow, against one server, one request after another — which is a different (and weaker) claim than "each piece passed its own tests."

## What this ticket confirms

That the app is a coherent whole, not six modules that each work in isolation but were never actually exercised together. This is the last checkpoint before Phase 2 (voice capture) builds on top of this foundation.

## Acceptance criteria

- [ ] `pytest -q` — full suite green, 0 failed, 0 errors
- [ ] `pytest -q tests/e2e/` — all end-to-end tests green on their own
- [ ] The manual flow below succeeds start to finish against a running server
- [ ] `PROGRESS.md`'s Phase 1 section and this ticket board are updated to reflect Phase 1 as done

## Full test suite

```bash
source .venv/bin/activate
pytest -q
```

Expected: all tests pass (no `NotImplementedError`s left anywhere).

## End-to-end manual flow

```bash
uvicorn sra.api.app:create_app --factory --reload --port 8000
```

In a second terminal:

```bash
# 1. Ingest a multi-topic transcript
curl -s -X POST http://localhost:8000/api/v1/entries/ingest \
  -H "Content-Type: application/json" \
  -d '{"transcript": "Today I covered photosynthesis - light and dark reactions - and also mitosis, the phases of cell division."}' | python3 -m json.tool
# Expect: 2 entries created (Photosynthesis, Mitosis)

# 2. Check the digest (only pending reviews due today or earlier)
curl -s http://localhost:8000/api/v1/digest | python3 -m json.tool
# Expect: no new entries yet; existing overdue reviews may appear.
# Run again tomorrow: both new entries should have interval_label "1d".

# 3. Search for one of them
curl -s "http://localhost:8000/api/v1/search?q=mitosis" | python3 -m json.tool
# Expect: the new Mitosis entry (plus any pre-existing matching entries).

# 4. Ask the chat agent about it
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "what did I learn about mitosis?"}' | python3 -m json.tool
# Expect: an answer mentioning cell division

# 5. Ask about something never studied
curl -s -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "what did I learn about quantum computing?"}' | python3 -m json.tool
# Expect: an honest "nothing found" answer, not a fabricated one
```

Once these requests succeed and both first checkpoints appear when due, Phase 1 is functionally complete: capture (via pasted transcript) → structured storage → spaced-repetition scheduling → digest → search → conversational Q&A, all working together. The end-to-end digest test advances the date without waiting a day.

## Definition of Done

- [ ] Verify the full core loop and update the phase board.
