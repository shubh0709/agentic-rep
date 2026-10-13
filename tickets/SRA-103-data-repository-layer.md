# SRA-103 — Data repository layer

**Type:** Task
**Phase:** 1 — Core loop
**Priority:** High
**Blocked by:** SRA-102 (`create_entry` calls `schedule_reviews_for_entry`)
**Blocks:** SRA-104, SRA-105, SRA-106

## Summary

Implement the four data-access functions in `src/sra/db/repository.py`. This is the only layer allowed to touch `StudyEntry`/`ReviewSchedule` rows directly — the agents and API routes all go through it. Every function is scoped by `user_id` and must never leak another user's data, even though Phase 1 only ever has one local user.

## Files

- `src/sra/db/repository.py`

## What to implement

- `create_entry(*, entry_create: StudyEntryCreate, user_id, session) -> StudyEntry` — insert the row via `StudyEntry.model_validate(entry_create, update={"user_id": user_id})` (never re-list fields as constructor kwargs — see the convention note atop `db/models.py`), then call `schedule_reviews_for_entry` (SRA-102) for it.
- `get_due_reviews(user_id, as_of, session) -> list[ReviewSchedule]` — this user's pending reviews with `scheduled_date <= as_of`.
- `search_entries(user_id, query, session, date_from=None, date_to=None) -> list[StudyEntry]` — case-insensitive substring match over topic/subtopic/tags/summary, this user's entries only, newest first.
- `mark_review_done(user_id, review_id, status, session) -> ReviewSchedule` — flip status, stamp `completed_at`. Use `datetime.now(timezone.utc)`, not `datetime.utcnow()` — see the note in the docstring about why (the DB layer rejects naive datetimes).

## Where to look

- Docstrings in `repository.py` — each one states the exact behavior and the SRA-102 dependency where relevant.
- `src/sra/db/models.py` for the exact field names on `StudyEntry`/`ReviewSchedule`.
- `docs/design/technical-design.md` §3.5 has the same signatures if you want the reference-doc version alongside the in-code docstrings.

## Acceptance criteria

- [ ] `create_entry` persists the entry AND its 6 review rows (via SRA-102) in one call
- [ ] `get_due_reviews` excludes future-dated and already-completed (`status != "pending"`) reviews
- [ ] `get_due_reviews` / `search_entries` never return another user's rows, even when both users have entries on the same dates
- [ ] `search_entries` is case-insensitive and matches topic/subtopic/tags/summary; empty query returns all of that user's entries
- [ ] `mark_review_done` raises `ValueError` (not a silent no-op) if `review_id` doesn't belong to `user_id`

## How to test this ticket alone

```bash
source .venv/bin/activate
pytest tests/unit/test_repository.py -v
```

Expected once implemented (and SRA-102 done): 7 passed.

## Manual verification

```bash
python3 -c "
from datetime import date
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

    due = repository.get_due_reviews(user.id, date.today() + __import__('datetime').timedelta(days=1), session)
    print('due tomorrow-or-earlier:', [(r.interval_label, r.scheduled_date) for r in due])
"
```

## Definition of Done

- [ ] All acceptance criteria met
- [ ] `pytest tests/unit/test_repository.py -v` — 7 passed
- [ ] Full suite: `pytest -q` (SRA-104/SRA-105's tests that call into the repository should now get further)
