# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — End-to-end ``generate_and_persist_questions`` + two references.

Seeds one entry + two ``ReferenceMaterial`` rows, drives
``generate_and_persist_questions`` end-to-end through a fake LLM seam,
and asserts that:

  * every persisted ``QuizQuestion`` row has the three new fields
    populated,
  * ``OPENAI_VERIFY_MCQS=false`` short-circuits the verify LLM call,
  * ``OPENAI_VERIFY_MCQS=true`` + a planted bad MCQ removes the
    question from the persisted set and stores the reason on
    ``QuizSet.discarded_for_evidence``.

The LLM seam is the same ``_get_chat_model`` monkeypatch target used by
``test_quiz_graph.py`` -- the SRA-903 rewrite provides
``_get_draft_model`` / ``_get_verify_model`` lazy seams, but the same
monkeypatch still works because the implementer still has to call
``_get_chat_model`` somewhere.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from sra.config import get_settings
from sra.db import repository
from sra.db.models import (
    QuizQuestion,
    StudyEntry,
)

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_entry_and_references(session, seeded_user) -> tuple[Any, list[Any]]:
    """Insert one StudyEntry with two Phase 9 ReferenceMaterial rows."""
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
            "In multi-head attention we project queries and keys to a "
            "dimension d_k = d_model / h so each head computes attention "
            "at a fraction of the total cost. The total compute stays "
            "roughly d_model^2 regardless of h."
        ),
        session=session,
    )
    repository.add_reference(
        entry_id=entry.id,
        kind="paste",
        title="Illustrated transformer blog",
        pasted_text=(
            "Scaled dot-product attention is the core operation. "
            "Applying softmax across the key dimension produces a "
            "distribution over keys for each query token."
        ),
        session=session,
    )

    return entry, repository.list_references_for_entry(entry.id, session=session)


def _good_drafts():
    from sra.agents.schemas import MCQDraft

    return [
        MCQDraft(
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
        ),
        MCQDraft(
            question_text="Which operation produces the attention distribution?",
            options=[
                "Softmax over the key dimension.",
                "Argmax over the keys.",
                "Mean-pool over the values.",
                "Layer norm over the queries.",
            ],
            correct_option_index=0,
            explanation="Softmax is the gate.",
            bloom_level="remember",
            source_passage_quote="softmax across the key dimension produces a distribution over keys for each query token",
            distractor_misconception="Confusing softmax with argmax / mean-pool",
        ),
    ]


class _DraftModel:
    """Fake for the structured-output call that returns scripted drafts."""

    def __init__(self, drafts):
        self._drafts = drafts
        self.call_count = 0

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        from sra.agents.schemas import MCQDrafts

        self.call_count += 1
        return MCQDrafts(drafts=list(self._drafts))


class _VerifyModel:
    """Fake verify-LLM that accepts a verdict map for each call."""

    def __init__(self, verdicts: list[str]):
        self._verdicts = list(verdicts)
        self.call_count = 0
        self.reasons: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        idx = self.call_count
        self.call_count += 1
        verdict = self._verdicts[idx] if idx < len(self._verdicts) else "yes"
        self.reasons.append(verdict)
        return {"verdict": verdict, "reason": f"verifier-call-{idx}"}


class _TranscriptEvidenceVerifyModel:
    """Accept only when the real graph passes the captured transcript."""

    def __init__(self, expected_transcript: str):
        self.expected_transcript = expected_transcript
        self.source_passages: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        human = messages[-1].content
        prefix = "Source passage:\n"
        suffix = "\n\nQuestion:"
        assert human.startswith(prefix)
        source_passage = human[len(prefix) : human.index(suffix)]
        self.source_passages.append(source_passage)
        if source_passage == self.expected_transcript:
            return {"verdict": "yes", "reason": "transcript supports the answer"}
        return {"verdict": "no", "reason": "transcript evidence was not supplied"}


# ---------------------------------------------------------------------------
# Real e2e: two references + every question row carries the new fields
# ---------------------------------------------------------------------------


def test_end_to_end_every_quiz_question_has_phase9_fields(
    monkeypatch, session, seeded_user
):
    from sra.agents.quiz_graph import generate_and_persist_questions

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "true")
    get_settings.cache_clear()

    entry, _references = _seed_entry_and_references(session, seeded_user)
    drafts = _good_drafts()

    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: _DraftModel(drafts)
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model",
        lambda: _VerifyModel(verdicts=["yes", "yes"]),
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=4,
    )

    rows = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()
    assert len(rows) == 2
    for row in rows:
        assert getattr(row, "bloom_level", None) in {
            "remember",
            "understand",
            "apply",
            "analyze",
            "evaluate",
            "create",
        }
        assert getattr(row, "source_passage_quote", None)
        assert getattr(row, "distractor_misconception", None)


# ---------------------------------------------------------------------------
# OPENAI_VERIFY_MCQS=false short-circuits the verify call
# ---------------------------------------------------------------------------


def test_openai_verify_mcqs_false_skips_the_verify_call(
    monkeypatch, session, seeded_user
):
    from sra.agents.quiz_graph import generate_and_persist_questions

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "false")
    get_settings.cache_clear()

    entry, _refs = _seed_entry_and_references(session, seeded_user)
    drafts = _good_drafts()

    draft_model = _DraftModel(drafts)
    verify_model = _VerifyModel(verdicts=["no", "no"])

    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: draft_model
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model", lambda: verify_model
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=4,
    )

    rows = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()
    assert len(rows) == 2
    assert verify_model.call_count == 0


# ---------------------------------------------------------------------------
# OPENAI_VERIFY_MCQS=true + planted bad MCQ -> row dropped + reason persisted
# ---------------------------------------------------------------------------


def test_openai_verify_mcqs_true_planted_bad_mcq_is_dropped(
    monkeypatch, session, seeded_user
):
    from sra.agents.quiz_graph import generate_and_persist_questions

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "true")
    get_settings.cache_clear()

    entry, _refs = _seed_entry_and_references(session, seeded_user)
    drafts = _good_drafts()

    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: _DraftModel(drafts)
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model",
        lambda: _VerifyModel(verdicts=["no", "yes"]),
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=2,
        option_count=4,
    )

    rows = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()
    # The first draft (planted as bad) was rejected by the verifier -> dropped.
    assert len(rows) == 1
    remaining = rows[0]
    assert getattr(remaining, "bloom_level", None) == "remember"

    reasons = getattr(quiz_set, "discarded_for_evidence", None) or []
    assert any("draft" in str(reason).lower() or "verifier" in str(reason).lower()
               for reason in reasons)


def test_transcript_only_entry_persists_questions_and_verifier_sees_transcript(
    monkeypatch, session, seeded_user
):
    """Run the real graph + repository with only OpenAI inference substituted."""
    from sra.agents.quiz_graph import generate_and_persist_questions

    transcript = (
        "Spaced repetition strengthens memory by scheduling retrieval practice "
        "across expanding intervals."
    )
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Spaced repetition",
        subtopic="retrieval schedules",
        tags=["learning"],
        summary="why scheduled retrieval improves memory",
        raw_transcript=transcript,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    draft = _good_drafts()[0].model_copy(
        update={
            "question_text": "Why does spaced repetition strengthen memory?",
            "options": [
                "It schedules retrieval practice across expanding intervals.",
                "It removes the need to retrieve information.",
                "It keeps every review interval identical.",
                "It replaces memory with passive rereading.",
            ],
            "source_passage_quote": (
                "Spaced repetition strengthens memory by scheduling retrieval "
                "practice across expanding intervals"
            ),
            "distractor_misconception": (
                "Confusing active retrieval schedules with passive rereading"
            ),
        }
    )
    draft_model = _DraftModel([draft])
    verify_model = _TranscriptEvidenceVerifyModel(transcript)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: draft_model
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model", lambda: verify_model
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=4,
    )

    rows = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).all()
    assert rows
    assert rows[0].question_text == draft.question_text
    assert verify_model.source_passages == [transcript]
