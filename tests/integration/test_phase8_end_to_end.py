"""SRA-802 — End-to-end pregen: summarize in-memory quizzes + interview plans."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date

import pytest
from sqlmodel import select

from sra.db.models import (
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.scheduler import compute_review_dates
from sra.jobs.quiz_pregeneration import (
    pregenerate_quizzes_for_day,
    pregenerate_quizzes_for_user,
)

# ``InterviewPlan`` is an SRA-801 model symbol imported inside each test so a
# missing symbol fails the test, not module-level collection.

pytestmark = pytest.mark.phase8


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    for label, scheduled in compute_review_dates(entry.created_at.date()):
        from sra.db.models import ReviewSchedule

        session.add(
            ReviewSchedule(
                entry_id=entry.id,
                interval_label=label,
                scheduled_date=scheduled,
                status="pending",
            )
        )
    from sra.db.models import ReviewSchedule

    session.add(
        ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=date.today(), status="pending")
    )
    session.commit()
    return entry


def _patch_mcq_and_interview(monkeypatch):
    from sra.agents import interview_planner as _ip
    from sra.agents import quiz_graph as _qg
    from sra.agents.schemas import MCQDraft, MCQDrafts

    class _FakeInterviewPlanner:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return _outline_for_entry(6)

    class _FakeMcqPlanner:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return MCQDrafts(
                drafts=[
                    MCQDraft(
                        question_text="q0",
                        options=["a", "b", "c", "d"],
                        correct_option_index=0,
                        explanation="",
                    )
                ]
            )

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _FakeInterviewPlanner())
    monkeypatch.setattr(_qg, "_get_chat_model", lambda: _FakeMcqPlanner())


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


def test_pregen_summary_includes_three_interview_counters(monkeypatch, session, seeded_user):
    today = date.today()
    entry = _seed_entry(session, seeded_user.id)
    assert entry.id is not None
    _patch_mcq_and_interview(monkeypatch)

    summary = pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    for key in (
        "interview_plans_created",
        "interview_plans_skipped",
        "interview_plans_errored",
    ):
        assert key in summary
        assert isinstance(summary[key], int)
    assert summary["interview_plans_created"] == 1  # pyright: ignore[reportGeneralTypeIssues]
    assert summary["interview_plans_errored"] == 0  # pyright: ignore[reportGeneralTypeIssues]


def test_pregen_round_trip_persists_interview_plan_with_outline_json(
    monkeypatch, session, seeded_user
):
    """After pregen, ``interview_plans`` has one row with outline JSON of length 6."""
    from sra.agents.interview_planner import (
        InterviewOutline,
        InterviewOutlineItem,
    )
    from sra.db.models import InterviewPlan

    today = date.today()
    entry = _seed_entry(session, seeded_user.id)
    assert entry.id is not None

    outline = InterviewOutline(
        items=[
            InterviewOutlineItem(
                subtopic=f"s{i}",
                difficulty="medium",
                focus_prompt=f"f{i}",
            )
            for i in range(6)
        ]
    )

    class _Planner:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return outline

    from sra.agents import interview_planner as _ip
    from sra.agents import quiz_graph as _qg
    from sra.agents.schemas import MCQDraft, MCQDrafts

    class _Mcq:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return MCQDrafts(
                drafts=[
                    MCQDraft(
                        question_text="q",
                        options=["a", "b", "c", "d"],
                        correct_option_index=0,
                        explanation="",
                    )
                ]
            )

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _Planner())
    monkeypatch.setattr(_qg, "_get_chat_model", lambda: _Mcq())

    pregenerate_quizzes_for_user(user=seeded_user, today=today, session=session)

    plan = session.exec(select(InterviewPlan)).one()
    assert len(plan.outline) == 6
    for item in plan.outline:
        assert set(item.keys()) >= {"subtopic", "difficulty", "focus_prompt"}


def test_pregen_quizzes_for_day_aggregates_interview_counters_across_users(
    monkeypatch, session, seeded_user, other_user
):
    today = date.today()
    _seed_entry(session, seeded_user.id)
    _seed_entry(session, other_user.id)
    _patch_mcq_and_interview(monkeypatch)

    summary = pregenerate_quizzes_for_day(today=today, session=session)

    for key in ("interview_plans_created", "interview_plans_skipped", "interview_plans_errored"):
        assert key in summary
