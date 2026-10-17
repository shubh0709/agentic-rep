# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — ``repository.create_quiz_set`` writes the three Phase 9
columns + ``QuizSet.discarded_for_evidence`` persists reasons.

Every test is expected to fail today:

  * ``create_quiz_set`` does not write the three new columns.
  * ``QuizSet.discarded_for_evidence`` is not declared.
  * ``MCQDraft.source_passage_quote`` / ``bloom_level`` /
    ``distractor_misconception`` are not declared.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlmodel import select

from sra.db import repository
from sra.db.models import (
    QuizQuestion,
    StudyEntry,
)

pytestmark = pytest.mark.phase9


def _make_entry(session, seeded_user) -> Any:
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Transformer attention",
        summary="how attention is computed and why multi-head works",
        raw_transcript="today I read about transformers",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def _make_draft_payload() -> dict:
    return dict(
        question_text="What does d_k = d_model/h achieve?",
        options=["Per-head compute is constant as h grows.", "Each head sees d_model.", "Matches residual stream.", "Theorem."],
        correct_option_index=0,
        explanation="Distractor targets per-head width confusion.",
        bloom_level="apply",
        source_passage_quote="we project to d_k = d_model/h so each head computes attention at a fraction of the cost",
        distractor_misconception="Conflating per-head representation width with per-head compute",
    )


# ---------------------------------------------------------------------------
# create_quiz_set writes the new columns per draft
# ---------------------------------------------------------------------------


def test_create_quiz_set_writes_new_columns_per_draft(session, seeded_user):
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, seeded_user)
    drafts = [
        MCQDraft(**_make_draft_payload()),
        MCQDraft(
            question_text="q2",
            options=["a", "b", "c", "d"],
            correct_option_index=1,
            explanation="e",
            bloom_level="analyze",
            source_passage_quote="verbatim eight to thirty word passage",
            distractor_misconception="named misconception",
        ),
    ]
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )
    rows = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()
    assert len(rows) == 2
    for row, draft in zip(rows, drafts, strict=True):
        assert getattr(row, "bloom_level", None) == draft.bloom_level
        assert getattr(row, "source_passage_quote", None) == draft.source_passage_quote
        assert getattr(row, "distractor_misconception", None) == draft.distractor_misconception


# ---------------------------------------------------------------------------
# discarded_for_evidence is persisted with the verify-pass reasons
# ---------------------------------------------------------------------------


def test_create_quiz_set_persists_discarded_for_evidence(session, seeded_user):
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, seeded_user)
    drafts = [MCQDraft(**_make_draft_payload())]
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
        discarded_for_evidence=["draft 0: passage does not support option 0"],
    )
    fetched = repository.get_quiz_set(seeded_user.id, quiz_set.id if quiz_set.id is not None else 0, session)
    assert fetched is not None
    assert isinstance(getattr(fetched, "discarded_for_evidence", None), list)
    assert any(
        "passage" in reason.lower()
        for reason in fetched.discarded_for_evidence
    )


# ---------------------------------------------------------------------------
# get_quiz_set returns the row with the new fields populated
# ---------------------------------------------------------------------------


def test_get_quiz_set_returns_new_fields_populated(session, seeded_user):
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, seeded_user)
    drafts = [MCQDraft(**_make_draft_payload())]
    quiz_set = repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )

    fetched = repository.get_quiz_set(
        seeded_user.id, quiz_set.id if quiz_set.id is not None else 0, session
    )
    assert fetched is not None
    questions = list(
        session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == fetched.id)
        )
    )
    assert len(questions) == 1
    q = questions[0]
    assert getattr(q, "bloom_level", None) == "apply"
    assert (
        getattr(q, "source_passage_quote", None)
        == "we project to d_k = d_model/h so each head computes attention at a fraction of the cost"
    )
    assert (
        getattr(q, "distractor_misconception", None)
        == "Conflating per-head representation width with per-head compute"
    )


def test_list_today_quiz_sets_returns_new_fields_populated(session, seeded_user):
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, seeded_user)
    drafts = [MCQDraft(**_make_draft_payload())]
    repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )

    sets_today = repository.list_today_quiz_sets(
        seeded_user.id, date.today(), session
    )
    assert len(sets_today) == 1
    qs = sets_today[0]
    questions = list(
        session.exec(
            select(QuizQuestion).where(QuizQuestion.quiz_set_id == qs.id)
        )
    )
    assert questions
    q = questions[0]
    assert getattr(q, "bloom_level", None) == "apply"


def test_has_quiz_set_for_entry_today_still_works_with_new_columns(session, seeded_user):
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, seeded_user)
    assert (
        repository.has_quiz_set_for_entry_today(
            seeded_user.id, entry.id if entry.id is not None else 0, date.today(), session
        )
        is False
    )
    drafts = [MCQDraft(**_make_draft_payload())]
    repository.create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )
    assert (
        repository.has_quiz_set_for_entry_today(
            seeded_user.id, entry.id if entry.id is not None else 0, date.today(), session
        )
        is True
    )
