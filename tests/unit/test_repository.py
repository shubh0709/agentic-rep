from datetime import date, timedelta

import pytest
from sqlmodel import select

from sra.db import repository
from sra.db.models import ReviewSchedule, StudyEntry, StudyEntryCreate


def test_create_entry_persists_and_schedules_reviews(session, seeded_user):
    entry = repository.create_entry(
        entry_create=StudyEntryCreate(
            topic="Binary Search Trees",
            summary="O(log n) lookup on a balanced BST",
            raw_transcript="today I studied binary search trees",
        ),
        user_id=seeded_user.id,
        session=session,
    )

    assert entry.id is not None
    assert entry.user_id == seeded_user.id

    schedules = session.exec(
        select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
    ).all()
    assert len(schedules) == 6


def test_get_due_reviews_excludes_future_and_completed(session, seeded_user):
    entry = StudyEntry(user_id=seeded_user.id, topic="T", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    today = date.today()
    due = ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=today, status="pending")
    future = ReviewSchedule(entry_id=entry.id, interval_label="3d", scheduled_date=today + timedelta(days=3), status="pending")
    done = ReviewSchedule(entry_id=entry.id, interval_label="7d", scheduled_date=today - timedelta(days=1), status="done")
    session.add_all([due, future, done])
    session.commit()

    result = repository.get_due_reviews(seeded_user.id, today, session)

    result_ids = {r.id for r in result}
    assert due.id in result_ids
    assert future.id not in result_ids
    assert done.id not in result_ids


def test_get_due_reviews_is_scoped_to_user(session, seeded_user, other_user):
    my_entry = StudyEntry(user_id=seeded_user.id, topic="Mine", summary="s", raw_transcript="t")
    their_entry = StudyEntry(user_id=other_user.id, topic="Theirs", summary="s", raw_transcript="t")
    session.add_all([my_entry, their_entry])
    session.commit()
    session.refresh(my_entry)
    session.refresh(their_entry)
    assert my_entry.id is not None
    assert their_entry.id is not None

    today = date.today()
    session.add(ReviewSchedule(entry_id=my_entry.id, interval_label="1d", scheduled_date=today, status="pending"))
    session.add(ReviewSchedule(entry_id=their_entry.id, interval_label="1d", scheduled_date=today, status="pending"))
    session.commit()

    result = repository.get_due_reviews(seeded_user.id, today, session)

    assert all(r.entry_id == my_entry.id for r in result)


def test_search_entries_matches_case_insensitive_substring(session, seeded_user):
    session.add(StudyEntry(user_id=seeded_user.id, topic="Photosynthesis", summary="plants make food", raw_transcript="t"))
    session.add(StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="cell division", raw_transcript="t"))
    session.commit()

    result = repository.search_entries(seeded_user.id, "photo", session)

    assert len(result) == 1
    assert result[0].topic == "Photosynthesis"


def test_search_entries_empty_query_returns_all_of_users_entries(session, seeded_user, other_user):
    session.add(StudyEntry(user_id=seeded_user.id, topic="A", summary="s", raw_transcript="t"))
    session.add(StudyEntry(user_id=seeded_user.id, topic="B", summary="s", raw_transcript="t"))
    session.add(StudyEntry(user_id=other_user.id, topic="C", summary="s", raw_transcript="t"))
    session.commit()

    result = repository.search_entries(seeded_user.id, "", session)

    assert len(result) == 2
    assert all(e.user_id == seeded_user.id for e in result)


def test_mark_review_done_stamps_status_and_completed_at(session, seeded_user):
    entry = StudyEntry(user_id=seeded_user.id, topic="T", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    review = ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=date.today(), status="pending")
    session.add(review)
    session.commit()
    session.refresh(review)
    assert review.id is not None

    updated = repository.mark_review_done(seeded_user.id, review.id, "done", session)

    assert updated.status == "done"
    assert updated.completed_at is not None


def test_mark_review_done_rejects_another_users_review(session, seeded_user, other_user):
    entry = StudyEntry(user_id=other_user.id, topic="T", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    review = ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=date.today(), status="pending")
    session.add(review)
    session.commit()
    session.refresh(review)
    assert review.id is not None

    with pytest.raises(ValueError):
        repository.mark_review_done(seeded_user.id, review.id, "done", session)
