"""SRA-403 nightly quiz pregeneration job: idempotency, per-user scope, error containment."""

from datetime import date, timedelta

import pytest
from sqlmodel import select

from sra.agents.quiz_graph import generate_and_persist_questions
from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.db.models import QuizSet, ReviewSchedule, StudyEntry, StudyEntryCreate
from sra.db.repository import get_due_reviews
from sra.db.scheduler import compute_review_dates
from sra.jobs.quiz_pregeneration import (
    pregenerate_quizzes_for_day,
    pregenerate_quizzes_for_user,
)

pytestmark = pytest.mark.phase4


class FakeChatModel:
    def __init__(self, drafts: list[MCQDraft] | None = None, raise_error: bool = False):
        self._drafts = drafts or []
        self._raise_error = raise_error

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, *args, **kwargs):
        if self._raise_error:
            raise RuntimeError("simulated LLM failure")
        return MCQDrafts(drafts=self._drafts)


def _seed_entry_with_six_reviews(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    schedule_dates = compute_review_dates(entry.created_at.date())
    for label, scheduled in schedule_dates:
        session.add(
            ReviewSchedule(entry_id=entry.id, interval_label=label, scheduled_date=scheduled, status="pending")
        )
    session.commit()
    return entry


def _due_review_today(session, entry_id: int, today: date) -> ReviewSchedule:
    review = ReviewSchedule(entry_id=entry_id, interval_label="1d", scheduled_date=today, status="pending")
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def _due_review_in_future(session, entry_id: int, today: date, days: int = 3) -> ReviewSchedule:
    review = ReviewSchedule(
        entry_id=entry_id,
        interval_label="3d",
        scheduled_date=today + timedelta(days=days),
        status="pending",
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def _patch_generate(monkeypatch, *, drafts: list[MCQDraft] | None = None, raise_error: bool = False):
    fake = FakeChatModel(drafts=drafts, raise_error=raise_error)
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: fake)
    return fake


def _drafts(n: int = 2, option_count: int = 4) -> list[MCQDraft]:
    return [
        MCQDraft(
            question_text=f"q{i}",
            options=[f"o{j}" for j in range(option_count)],
            correct_option_index=i % option_count,
            explanation=f"e{i}",
        )
        for i in range(n)
    ]


def test_pregenerate_creates_one_quiz_set_per_due_entry_for_user(monkeypatch, session, seeded_user):
    today = date.today()
    entry_a = _seed_entry_with_six_reviews(session, seeded_user.id)
    entry_b = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry_a.id is not None and entry_b.id is not None
    _due_review_today(session, entry_a.id, today)
    _due_review_today(session, entry_b.id, today)
    _patch_generate(monkeypatch, drafts=_drafts(n=2))

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["entries_seen"] == 2
    assert summary["sets_created"] == 2
    assert summary["sets_skipped"] == 0
    assert summary["errors"] == 0

    sets = session.exec(select(QuizSet).where(QuizSet.user_id == seeded_user.id)).all()
    assert {s.entry_id for s in sets} == {entry_a.id, entry_b.id}


def test_pregenerate_skips_entries_already_seeded_today(monkeypatch, session, seeded_user):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    first = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)
    second = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert first["sets_created"] == 1
    assert first["sets_skipped"] == 0
    assert second["sets_created"] == 0
    assert second["sets_skipped"] == 1


def test_pregenerate_ignores_entries_not_due_today(monkeypatch, session, seeded_user):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_in_future(session, entry.id, today, days=3)
    _patch_generate(monkeypatch, drafts=_drafts())

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["entries_seen"] == 0
    assert summary["sets_created"] == 0
    assert summary["sets_skipped"] == 0


def test_pregenerate_contains_one_entry_error_and_continues(monkeypatch, session, seeded_user):
    today = date.today()
    entry_a = _seed_entry_with_six_reviews(session, seeded_user.id)
    entry_b = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry_a.id is not None and entry_b.id is not None
    _due_review_today(session, entry_a.id, today)
    _due_review_today(session, entry_b.id, today)

    fake = FakeChatModel()
    original = fake.invoke

    calls = {"count": 0}

    def invoke_then_fail_first(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("first entry fails")
        return original(*args, **kwargs)

    fake.invoke = invoke_then_fail_first
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: fake)

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["entries_seen"] == 2
    assert summary["sets_created"] == 1
    assert summary["errors"] == 1
    sets = session.exec(select(QuizSet).where(QuizSet.user_id == seeded_user.id)).all()
    assert len(sets) == 1
    assert sets[0].entry_id == entry_b.id


def test_pregenerate_quizzes_for_day_walks_every_user_and_aggregates(monkeypatch, session, seeded_user, other_user):
    today = date.today()
    entry_a1 = _seed_entry_with_six_reviews(session, seeded_user.id)
    entry_a2 = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry_a1.id is not None and entry_a2.id is not None
    _due_review_today(session, entry_a1.id, today)
    _due_review_today(session, entry_a2.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    summary = pregenerate_quizzes_for_day(today=today, session=session)

    assert summary["users"] >= 2
    assert summary["sets_created"] >= 2


def test_pregenerate_quizzes_for_day_is_idempotent_across_runs(monkeypatch, session, seeded_user, other_user):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    first = pregenerate_quizzes_for_day(today=today, session=session)
    second = pregenerate_quizzes_for_day(today=today, session=session)

    assert first["sets_created"] >= 1
    assert second["sets_created"] == 0
    assert second["sets_skipped"] >= 1


def test_pregenerate_quizzes_for_day_skips_runs_when_no_users_have_due_entries(monkeypatch, session):
    today = date.today()
    _patch_generate(monkeypatch, drafts=_drafts())

    summary = pregenerate_quizzes_for_day(today=today, session=session)

    assert summary["users"] == 0
    assert summary["sets_created"] == 0
    assert summary["sets_skipped"] == 0
    assert summary["errors"] == 0


def test_pregenerate_quizzes_for_user_uses_due_reviews_helper(session, seeded_user, monkeypatch):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    observed_calls: list[tuple[int, date]] = []

    original = get_due_reviews

    def spy(user_id, as_of, session_):
        observed_calls.append((user_id, as_of))
        return original(user_id, as_of, session_)

    monkeypatch.setattr("sra.jobs.quiz_pregeneration.repository.get_due_reviews", spy)

    pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert observed_calls == [(seeded_user.id, today)]


def test_pregenerate_quizzes_for_day_returns_typed_summary_keys(monkeypatch, session, seeded_user):
    today = date.today()
    _patch_generate(monkeypatch, drafts=_drafts())

    summary = pregenerate_quizzes_for_day(today=today, session=session)

    for key in ("users", "entries_seen", "sets_created", "sets_skipped", "errors"):
        assert key in summary
        assert isinstance(summary[key], int)


def test_pregenerate_uses_default_question_and_option_counts(monkeypatch, session, seeded_user):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)

    captured: list[tuple[int, int]] = []

    def spy(*, entry, user_id, session, question_count, option_count):
        captured.append((question_count, option_count))
        return generate_and_persist_questions(
            entry=entry,
            user_id=user_id,
            session=session,
            question_count=question_count,
            option_count=option_count,
        )

    fake = FakeChatModel(drafts=_drafts(n=10, option_count=4))
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: fake)
    monkeypatch.setattr("sra.jobs.quiz_pregeneration.generate_and_persist_questions", spy)

    pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert captured, "pregeneration did not invoke the graph entry point"
    question_count, option_count = captured[0]
    assert question_count == 10
    assert option_count == 4


def test_pregenerate_distinguishes_entries_with_multiple_due_reviews(monkeypatch, session, seeded_user):
    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["entries_seen"] == 1
    assert summary["sets_created"] == 1


def test_pregenerate_isolates_users_with_owns(monkeypatch, session, seeded_user, other_user):
    today = date.today()
    entry_a = _seed_entry_with_six_reviews(session, seeded_user.id)
    entry_b = _seed_entry_with_six_reviews(session, other_user.id)
    assert entry_a.id is not None and entry_b.id is not None
    _due_review_today(session, entry_a.id, today)
    _due_review_today(session, entry_b.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    pregenerate_quizzes_for_day(today=today, session=session)

    sets = session.exec(select(QuizSet)).all()
    owners = {s.user_id for s in sets}
    assert seeded_user.id in owners
    assert other_user.id in owners
    assert len(sets) == 2