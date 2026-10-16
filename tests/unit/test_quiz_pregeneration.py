
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
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


# ---------------------------------------------------------------------------
# SRA-802 — Pregen also produces one ``InterviewPlan`` per new QuizSet.
# Each pregen summary exposes ``interview_plans_created / skipped / errored``.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 4 marker


def _patch_interview_planner(monkeypatch, *, items_per_call=2, raise_on_invoke=False, fail_count=0):
    """Patch ``_get_chat_model`` so each invocation returns a deterministic ``InterviewOutline``.

    When ``raise_on_invoke`` is true, ``invoke`` raises a ``RuntimeError`` for
    the first ``fail_count`` calls and then returns the outline. Setting
    ``fail_count`` lower than the number of planner invocations in the test
    exercises the planner's per-entry error containment (calls past
    ``fail_count`` must succeed).
    """
    from sra.agents import interview_planner as _ip

    state = {"calls": 0}

    class _FakeInterviewPlanner:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            state["calls"] += 1
            if raise_on_invoke and state["calls"] <= fail_count:
                raise RuntimeError(
                    f"simulated interview planner LLM failure (call {state['calls']})"
                )
            from sra.agents.interview_planner import (
                InterviewOutline,
                InterviewOutlineItem,
            )

            return InterviewOutline(
                items=[
                    InterviewOutlineItem(
                        subtopic=f"subtopic-{i}",
                        difficulty="medium",
                        focus_prompt=f"focus-{i}",
                    )
                    for i in range(items_per_call)
                ]
            )

    monkeypatch.setattr(
        _ip, "_get_chat_model", lambda: _FakeInterviewPlanner()
    )
    return _FakeInterviewPlanner()


def _outline_for_entry(target: int):
    from sra.agents.interview_planner import (
        InterviewOutline,
        InterviewOutlineItem,
    )

    return InterviewOutline(
        items=[
            InterviewOutlineItem(
                subtopic=f"sub{i}",
                difficulty="medium",
                focus_prompt=f"focus-{i}",
            )
            for i in range(target)
        ]
    )


@phase8
def test_pregenerate_creates_quiz_set_and_interview_plan_per_entry(
    monkeypatch, session, seeded_user
):
    """Each new QuizSet is paired with one InterviewPlan on the same day."""
    from sra.agents import interview_planner as _ip
    from sra.db.models import InterviewPlan

    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())
    # Planner returns 2 items; target_questions is the user's preference (default 6)
    # so we ask it to return 6 items to match the design.
    class _PlanOfSix:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return _outline_for_entry(6)

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _PlanOfSix())

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["sets_created"] == 1
    assert summary["interview_plans_created"] == 1
    assert summary["interview_plans_skipped"] == 0
    assert summary["interview_plans_errored"] == 0

    plans = session.exec(select(InterviewPlan)).all()
    assert len(plans) == 1
    quiz_set = session.exec(select(QuizSet).where(QuizSet.entry_id == entry.id)).one()
    assert plans[0].quiz_set_id == quiz_set.id


@phase8
def test_pregenerate_same_day_does_not_duplicate_interview_plan(
    monkeypatch, session, seeded_user
):
    from sra.agents import interview_planner as _ip
    from sra.db.models import InterviewPlan

    today = date.today()
    entry = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry.id is not None
    _due_review_today(session, entry.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    class _PlanOfSix:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return _outline_for_entry(6)

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _PlanOfSix())

    first = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)
    second = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert first["interview_plans_created"] == 1
    assert second["interview_plans_skipped"] == 1
    assert second["interview_plans_created"] == 0
    plans = session.exec(select(InterviewPlan)).all()
    assert len(plans) == 1


@phase8
def test_pregenerate_increments_interview_plans_errored_on_planner_failure(
    monkeypatch, session, seeded_user
):

    today = date.today()
    entry_a = _seed_entry_with_six_reviews(session, seeded_user.id)
    entry_b = _seed_entry_with_six_reviews(session, seeded_user.id)
    assert entry_a.id is not None and entry_b.id is not None
    _due_review_today(session, entry_a.id, today)
    _due_review_today(session, entry_b.id, today)
    _patch_generate(monkeypatch, drafts=_drafts())

    _patch_interview_planner(monkeypatch, raise_on_invoke=True, fail_count=1)

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    assert summary["interview_plans_created"] == 1
    assert summary["interview_plans_errored"] == 1


@phase8
def test_pregenerate_quizzes_for_day_summary_keys_include_interview_counters(
    monkeypatch, session, seeded_user
):
    today = date.today()
    _patch_generate(monkeypatch, drafts=_drafts())
    _patch_interview_planner(monkeypatch, items_per_call=6)

    summary = pregenerate_quizzes_for_day(today=today, session=session)

    for key in (
        "interview_plans_created",
        "interview_plans_skipped",
        "interview_plans_errored",
    ):
        assert key in summary
        assert isinstance(summary[key], int)