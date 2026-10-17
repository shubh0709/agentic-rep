# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-904 — End-to-end ``generate_interview_outline`` with references.

Seeds an entry plus two reference rows, drives the planner with a fake
LLM that returns scripted items carrying the three new fields, and
asserts every persisted ``InterviewPlanItem`` carries non-empty
``source_passage_quote`` and ``ideal_answer_rubric``.

Today this test fails because ``generate_interview_outline`` does not
yet pass ``references=`` through to ``_build_messages`` and the
``InterviewOutlineItem`` schema does not declare the new fields.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

pytestmark = pytest.mark.phase9


def _import_planner() -> Any:
    from sra.agents import interview_planner

    return interview_planner


def _seed_entry_with_references(session, user_id: int):
    """Seed a StudyEntry plus two ReferenceMaterial rows."""
    from sra.db.models import ReferenceMaterial, StudyEntry, StudyEntryCreate

    entry = StudyEntry.model_validate(
        StudyEntryCreate(
            topic="Transformer attention",
            subtopic="multi-head",
            tags=["deep-learning"],
            summary="how attention is computed and why multi-head works",
            raw_transcript="today I read about transformers",
        ),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None

    refs = [
        ReferenceMaterial.model_validate(
            {
                "kind": "paste",
                "title": "Lecture notes",
                "pasted_text": (
                    "We compute attention as softmax(QK^T / sqrt(d_k)) V. "
                    "Multi-head attention concatenates h heads so the model can "
                    "attend to information from different representation subspaces."
                ),
            },
            update={
                "entry_id": entry.id,
                "extracted_text": None,
                "fetch_status": "fetched",
            },
        ),
        ReferenceMaterial.model_validate(
            {
                "kind": "paste",
                "title": "Illustrated transformer",
                "pasted_text": (
                    "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable "
                    "as d_k grows; without it, large d_k pushes softmax into saturated "
                    "regions."
                ),
            },
            update={
                "entry_id": entry.id,
                "extracted_text": None,
                "fetch_status": "fetched",
            },
        ),
    ]
    for ref in refs:
        session.add(ref)
    session.commit()
    return entry


class _FakePlannerModel:
    """Fake LLM that returns a scripted outline carrying the new fields."""

    def __init__(self, items):
        self._items = items
        self.seen_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        planner = _import_planner()
        assert schema is planner.InterviewOutline
        return self

    def invoke(self, messages, **kwargs):
        planner = _import_planner()
        self.seen_messages.extend(messages)
        return planner.InterviewOutline(
            items=[planner.InterviewOutlineItem(**i) for i in self._items]
        )


def test_generate_interview_outline_carries_source_passage_quote_for_each_item(
    monkeypatch, session, seeded_user
):
    planner = _import_planner()
    entry = _seed_entry_with_references(session, seeded_user.id)

    items = [
        {
            "subtopic": "scaled dot-product attention",
            "difficulty": "medium",
            "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
            "source_passage_quote": "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
            "ideal_answer_rubric": "sqrt(d_k) keeps dot products in a low-variance range.",
            "bloom_level": "apply",
        },
        {
            "subtopic": "multi-head concatenation",
            "difficulty": "hard",
            "focus_prompt": "Why do we concatenate the heads?",
            "source_passage_quote": "Multi-head attention concatenates h heads so the model can attend",
            "ideal_answer_rubric": "Concatenation lets each head learn a distinct subspace.",
            "bloom_level": "analyze",
        },
    ]
    fake = _FakePlannerModel(items)
    monkeypatch.setattr(planner, "_get_chat_model", lambda: fake)

    outline = planner.generate_interview_outline(
        entry=entry, target_questions=2, llm=fake
    )

    assert len(outline.items) == 2
    for item in outline.items:
        assert item.source_passage_quote, (
            f"item missing source_passage_quote: {item!r}"
        )
        assert item.ideal_answer_rubric, (
            f"item missing ideal_answer_rubric: {item!r}"
        )
        assert item.bloom_level in (
            "remember",
            "understand",
            "apply",
            "analyze",
            "evaluate",
            "create",
        ), f"item has invalid bloom_level: {item.bloom_level!r}"


def test_generate_interview_outline_persists_persisted_plan_with_new_fields(
    monkeypatch, session, seeded_user
):
    """Round-trip: planner -> repository.create_interview_plan -> DB row.

    The persisted outline carries the new fields in the JSON column.
    """
    from sra.db.models import InterviewPlan
    from sra.db.repository import create_interview_plan

    planner = _import_planner()
    entry = _seed_entry_with_references(session, seeded_user.id)

    # Seed a QuizSet so create_interview_plan accepts the outline.
    from datetime import date

    from sra.agents.schemas import MCQDraft
    from sra.db.repository import create_quiz_set

    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""
        )
    ]
    quiz_set = create_quiz_set(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )

    items = [
        {
            "subtopic": "scaled dot-product attention",
            "difficulty": "medium",
            "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
            "source_passage_quote": "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable",
            "ideal_answer_rubric": "sqrt(d_k) keeps dot products in a low-variance range.",
            "bloom_level": "apply",
        },
        {
            "subtopic": "multi-head concatenation",
            "difficulty": "hard",
            "focus_prompt": "Why do we concatenate the heads?",
            "source_passage_quote": "Multi-head attention concatenates h heads so the model can attend",
            "ideal_answer_rubric": "Concatenation lets each head learn a distinct subspace.",
            "bloom_level": "analyze",
        },
    ]
    fake = _FakePlannerModel(items)
    monkeypatch.setattr(planner, "_get_chat_model", lambda: fake)

    outline = planner.generate_interview_outline(
        entry=entry, target_questions=2, llm=fake
    )
    plan = create_interview_plan(
        quiz_set=quiz_set,
        outline=outline,
        target_questions=2,
        session=session,
    )
    assert plan is not None

    db_plan = session.exec(select(InterviewPlan)).one()
    assert len(db_plan.outline) == 2
    for item in db_plan.outline:
        assert item.get("source_passage_quote"), (
            f"persisted item missing source_passage_quote: {item!r}"
        )
        assert item.get("ideal_answer_rubric"), (
            f"persisted item missing ideal_answer_rubric: {item!r}"
        )
        assert item.get("bloom_level") in (
            "remember",
            "understand",
            "apply",
            "analyze",
            "evaluate",
            "create",
        )