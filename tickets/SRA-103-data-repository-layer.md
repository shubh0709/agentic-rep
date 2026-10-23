# SRA-103 — Data repository layer

**Phase:** 1 — Core loop · **Status:** implemented

## Summary

Four data-access functions in `src/sra/db/repository.py`. This is the only layer
allowed to touch `StudyEntry`/`ReviewSchedule` rows directly — the agents and API
routes all go through it. Every function is scoped by `user_id` and must never
leak another user's data, even though Phase 1 only ever has one local user; the
isolation is what SRA-302's authentication later depends on.

## Unlocks

- SRA-104's digest and search routes can call the repository instead of writing
  their own queries.
- SRA-105's `persist_entries` and SRA-106's query tools get user-scoped storage.

## Files

| File | Change | Why |
|---|---|---|
| [`src/sra/db/repository.py`](../src/sra/db/repository.py) | edit | `create_entry`, `get_due_reviews`, `search_entries`, `mark_review_done` — each docstring in the file states the exact behavior and the SRA-102 dependency. |
| [`src/sra/db/models.py`](../src/sra/db/models.py) | read | `StudyEntry`/`ReviewSchedule` field names. `create_entry` uses `StudyEntry.model_validate(entry_create, update={"user_id": user_id})` — never re-list fields as constructor kwargs; see the convention note atop the file. |
| [`tests/unit/test_repository.py`](../tests/unit/test_repository.py) | run | 7 scaffold-supplied tests covering isolation and empty results. |

## Acceptance criteria

- [ ] `create_entry` persists the entry AND its 6 review rows (via SRA-102) in
  one call
- [ ] `get_due_reviews` excludes future-dated and already-completed
  (`status != "pending"`) reviews
- [ ] `get_due_reviews` / `search_entries` never return another user's rows,
  even when both users have entries on the same dates
- [ ] `search_entries` is case-insensitive and matches
  topic/subtopic/tags/summary; empty query returns all of that user's entries
- [ ] `mark_review_done` raises `ValueError` (not a silent no-op) if `review_id`
  doesn't belong to `user_id`

## Tests

```bash
source .venv/bin/activate
pytest tests/unit/test_repository.py -v
```

Expected once implemented (and SRA-102 done): 7 passed.

## Manual verification

- [ ] Seeds one entry, then prints the due reviews — proves `create_entry`
      scheduled rows that `get_due_reviews` can read back:

      ```bash
      python3 -c "
      from datetime import date, timedelta
      from sqlmodel import Session, SQLModel, create_engine
      from sra.db.models import User, UserCreate, StudyEntryCreate
      from sra.db import repository

      engine = create_engine('sqlite://')
      SQLModel.metadata.create_all(engine)
      with Session(engine) as session:
          user = User.model_validate(UserCreate(email='me@local', display_name='Me'))
          session.add(user); session.commit(); session.refresh(user)

          entry_create = StudyEntryCreate(topic='Photosynthesis', summary='light + dark reactions', raw_transcript='today I studied photosynthesis')
          entry = repository.create_entry(entry_create=entry_create, user_id=user.id, session=session)
          print('created:', entry.id, entry.topic)

          due = repository.get_due_reviews(user.id, date.today() + timedelta(days=1), session)
          print('due tomorrow-or-earlier:', [(r.interval_label, r.scheduled_date) for r in due])
      "
      ```

- [ ] `pytest -q` — SRA-104/SRA-105's tests that call into the repository should
      now get further.

---

Design context: [`technical-design.md`](../docs/design/technical-design.md). Ticket
format rules: [`guides/ticket_creation.md`](../guides/ticket_creation.md).
Phase status: [`PROGRESS.md`](../PROGRESS.md).