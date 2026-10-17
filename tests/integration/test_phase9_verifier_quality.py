# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — Planted-bad-MCQ integration via the verify LLM seam.

Drives ``generate_and_persist_questions`` end-to-end with a fake verify
LLM that returns ``verdict="no"`` for one specific planted question, and
asserts the persisted ``QuizQuestion`` set excludes the planted row.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from sra.config import get_settings
from sra.db import repository
from sra.db.models import StudyEntry

pytestmark = pytest.mark.phase9


def _seed_entry(session, seeded_user) -> Any:
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Transformer attention",
        subtopic="multi-head",
        tags=["deep-learning"],
        summary="how attention is computed and why multi-head works",
        raw_transcript="today I read about transformers",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    repository.add_reference(
        entry_id=entry.id,
        kind="paste",
        title="Lecture notes",
        pasted_text=(
            "In multi-head attention we project queries and keys to "
            "d_k = d_model / h so each head computes attention at a "
            "fraction of the total cost."
        ),
        session=session,
    )
    return entry


class _DraftModel:
    def __init__(self, drafts):
        self._drafts = drafts

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        from sra.agents.schemas import MCQDrafts

        return MCQDrafts(drafts=list(self._drafts))


class _PlantBadVerifyModel:
    """Fake verify-LLM that rejects the planted 'what does warmup do?' question.

    The planted question's correct option claims gravity drops the loss
    surface toward the warmup point; the verifier (simulated here as a
    rule-based stub) recognizes that no passage text supports that claim
    and returns ``verdict="no"``.
    """

    planted_keyword = "warmup"

    def __init__(self):
        self.call_count = 0
        self.reasons: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.call_count += 1
        text_blob = " ".join(
            getattr(m, "content", "") if hasattr(m, "content") else str(m)
            for m in messages
        )
        if self.planted_keyword in text_blob:
            self.reasons.append("planted: no passage support")
            return {"verdict": "no", "reason": "planted: no passage support"}
        self.reasons.append("ok")
        return {"verdict": "yes", "reason": "ok"}


def test_verify_rejection_drops_planted_question(monkeypatch, session, seeded_user):
    from sra.agents.quiz_graph import generate_and_persist_questions
    from sra.agents.schemas import MCQDraft

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "true")
    get_settings.cache_clear()

    entry = _seed_entry(session, seeded_user)

    planted = MCQDraft(
        question_text="Why does the optimizer use a learning-rate warmup?",
        options=[
            "Gravity drops the loss surface.",
            "It avoids exploding gradients.",
            "It matches the residual stream.",
            "It is required by SGD.",
        ],
        correct_option_index=0,
        explanation="Distractor targets the gravity confusion.",
        bloom_level="apply",
        source_passage_quote="we project to d_k = d_model/h so each head computes attention at a fraction of the cost",
        distractor_misconception="Conflating warmup with a physical gravity model",
    )
    real = MCQDraft(
        question_text="What does d_k = d_model/h achieve?",
        options=[
            "Per-head compute is constant as h grows.",
            "Each head sees the full d_model.",
            "Matches the residual stream.",
            "Theoretical optimum.",
        ],
        correct_option_index=0,
        explanation="Distractor targets per-head width confusion.",
        bloom_level="apply",
        source_passage_quote="we project to d_k = d_model/h so each head computes attention at a fraction of the cost",
        distractor_misconception="Conflating per-head representation width with per-head compute",
    )

    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model",
        lambda: _DraftModel([planted, real]),
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model", lambda: _PlantBadVerifyModel()
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=4,
    )

    rows = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id
            == quiz_set.id
        )
    ).all()

    assert len(rows) == 1
    assert rows[0].question_text == real.question_text
    discarded = getattr(quiz_set, "discarded_for_evidence", None) or []
    assert any("planted" in str(reason).lower() or "warmup" in str(reason).lower()
               for reason in discarded)
