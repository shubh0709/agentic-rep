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


def test_list_upcoming_reviews_returns_only_pending_in_window(session, seeded_user):
    entry = StudyEntry(user_id=seeded_user.id, topic="T", summary="s", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    today = date.today()
    rows = [
        # Outside the window: before from_date.
        ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=today, status="pending"),
        # Inside the window.
        ReviewSchedule(entry_id=entry.id, interval_label="3d", scheduled_date=today + timedelta(days=1), status="pending"),
        ReviewSchedule(entry_id=entry.id, interval_label="7d", scheduled_date=today + timedelta(days=5), status="pending"),
        # Outside the window: after to_date.
        ReviewSchedule(entry_id=entry.id, interval_label="1mo", scheduled_date=today + timedelta(days=60), status="pending"),
        # Inside the window but already completed.
        ReviewSchedule(entry_id=entry.id, interval_label="3mo", scheduled_date=today + timedelta(days=20), status="done"),
    ]
    for row in rows:
        session.add(row)
    session.commit()

    upcoming = repository.list_upcoming_reviews(
        user_id=seeded_user.id,
        from_date=today + timedelta(days=1),
        to_date=today + timedelta(days=30),
        session=session,
    )

    assert [r.interval_label for r in upcoming] == ["3d", "7d"]


def test_list_upcoming_reviews_excludes_other_users_rows(session, seeded_user, other_user):
    mine = StudyEntry(user_id=seeded_user.id, topic="Mine", summary="s", raw_transcript="t")
    theirs = StudyEntry(user_id=other_user.id, topic="Theirs", summary="s", raw_transcript="t")
    session.add(mine)
    session.add(theirs)
    session.commit()
    session.refresh(mine)
    session.refresh(theirs)
    assert mine.id is not None and theirs.id is not None

    today = date.today()
    mine_review = ReviewSchedule(
        entry_id=mine.id, interval_label="3d",
        scheduled_date=today + timedelta(days=1), status="pending",
    )
    their_review = ReviewSchedule(
        entry_id=theirs.id, interval_label="3d",
        scheduled_date=today + timedelta(days=1), status="pending",
    )
    session.add(mine_review)
    session.add(their_review)
    session.commit()

    upcoming = repository.list_upcoming_reviews(
        user_id=seeded_user.id,
        from_date=today,
        to_date=today + timedelta(days=30),
        session=session,
    )

    assert [r.interval_label for r in upcoming] == ["3d"]
    assert all(r.entry_id == mine.id for r in upcoming)


def test_count_user_entries_returns_zero_for_new_user(session, seeded_user):
    assert repository.count_user_entries(seeded_user.id, session) == 0


def test_count_user_entries_reflects_persisted_rows(session, seeded_user):
    for i in range(3):
        entry = StudyEntry(
            user_id=seeded_user.id,
            topic=f"T{i}",
            summary="s",
            raw_transcript="t",
        )
        session.add(entry)
    session.commit()

    assert repository.count_user_entries(seeded_user.id, session) == 3


def test_count_user_entries_is_scoped_per_user(session, seeded_user, other_user):
    session.add(StudyEntry(user_id=seeded_user.id, topic="mine", summary="s", raw_transcript="t"))
    session.add(StudyEntry(user_id=other_user.id, topic="theirs", summary="s", raw_transcript="t"))
    session.commit()

    assert repository.count_user_entries(seeded_user.id, session) == 1
    assert repository.count_user_entries(other_user.id, session) == 1
