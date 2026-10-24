"""SRA-1203 real FastAPI/repository/SQLite entry mutation flow."""

from datetime import UTC, date, datetime

from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizAttempt,
    QuizQuestion,
    QuizSet,
    ReferenceMaterial,
    ReviewSchedule,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import add_reference, create_quiz_set
from sra.db.scheduler import compute_review_dates, schedule_reviews_for_entry


def _entry(session, user_id: int, *, captured_at: datetime) -> StudyEntry:
    row = StudyEntry.model_validate(
        StudyEntryCreate(
            topic="Check", summary="Crawler notes", raw_transcript="crawler source"
        ),
        update={"user_id": user_id, "created_at": captured_at},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    schedule_reviews_for_entry(row.id, row.created_at.date(), session)
    return row


def test_edit_date_reschedules_pending_reviews_and_refreshes_search(
    client, session, seeded_user
):
    entry = _entry(
        session, seeded_user.id, captured_at=datetime(2026, 10, 1, 9, tzinfo=UTC)
    )
    assert entry.id is not None

    response = client.patch(
        f"/api/v1/entries/{entry.id}",
        json={
            "topic": "Figure out job crawling",
            "created_at": "2026-10-20T15:30:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json()["topic"] == "Figure out job crawling"
    assert datetime.fromisoformat(response.json()["created_at"]).date() == date(2026, 10, 20)
    reviews = session.exec(
        select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
    ).all()
    assert {row.interval_label: row.scheduled_date for row in reviews} == dict(
        compute_review_dates(date(2026, 10, 20))
    )
    found = client.get("/api/v1/search?q=job%20crawling").json()
    assert [row["id"] for row in found] == [entry.id]


def test_delete_entry_removes_dependent_study_artifacts(
    client, session, seeded_user
):
    entry = _entry(
        session, seeded_user.id, captured_at=datetime(2026, 10, 1, 9, tzinfo=UTC)
    )
    assert entry.id is not None
    add_reference(
        entry_id=entry.id,
        kind="paste",
        title="Crawler reference",
        pasted_text="robots and queues",
        extracted_text="robots and queues",
        fetch_status="ready",
        session=session,
    )
    quiz_set = create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date(2026, 10, 24),
        question_count=1,
        option_count=2,
        drafts=[
            MCQDraft(
                question_text="What should a crawler obey?",
                options=["robots.txt", "Nothing"],
                correct_option_index=0,
                explanation="Respect site policy.",
            )
        ],
    )
    assert quiz_set.id is not None
    attempt = QuizAttempt(
        user_id=seeded_user.id,
        quiz_set_id=quiz_set.id,
        mode="web",
        total_questions=1,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    response = client.delete(f"/api/v1/entries/{entry.id}")

    assert response.status_code == 204
    assert response.content == b""
    session.expire_all()
    assert session.get(StudyEntry, entry.id) is None
    assert session.exec(
        select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)
    ).all() == []
    assert session.exec(
        select(ReferenceMaterial).where(ReferenceMaterial.entry_id == entry.id)
    ).all() == []
    assert session.get(QuizSet, quiz_set.id) is None
    assert session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all() == []
    assert session.get(QuizAttempt, attempt.id) is None
    assert client.get("/api/v1/search?q=Check").json() == []
