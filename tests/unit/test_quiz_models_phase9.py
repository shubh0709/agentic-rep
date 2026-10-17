# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — Phase 9 additive ``MCQDraft`` / ``QuizQuestion`` schema fields.

Every test in this module is expected to fail today:

  * ``MCQDraft`` lacks ``bloom_level`` / ``source_passage_quote`` /
    ``distractor_misconception`` -> accessing them raises
    ``AttributeError`` or fails Pydantic validation.
  * ``QuizQuestion`` ORM lacks the three new nullable columns ->
    accessing them raises ``AttributeError``.
  * ``MCQVerifyResult`` does not exist on ``sra.agents.schemas``.

Phase 4 regression safety: existing ``MCQDraft`` rows (without the new
fields) must still validate against the model with the new fields
defaulting to ``None`` -- the additive constraint is the integration
boundary the implementer has to satisfy.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizQuestion,
    QuizQuestionCreate,
    QuizSet,
    QuizSetCreate,
    StudyEntry,
    User,
)

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Imports the implementer must provide.
# ---------------------------------------------------------------------------


def _schemas() -> Any:
    from sra.agents import schemas

    return schemas


# ---------------------------------------------------------------------------
# MCQDraft.bloom_level
# ---------------------------------------------------------------------------


BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")


@pytest.mark.parametrize("level", BLOOM_LEVELS)
def test_mcq_draft_bloom_level_accepts_each_literal(level):
    MCQDraft(
        question_text="x",
        options=["a", "b"],
        correct_option_index=0,
        bloom_level=level,
        source_passage_quote="eight word passage quote here now please keep",
        distractor_misconception="misconception",
    )


def test_mcq_draft_bloom_level_rejects_unknown_value():
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="synthesis",  # not in the Bloom literal
            source_passage_quote="eight word passage quote here now please keep",
            distractor_misconception="misconception",
        )


def test_mcq_draft_bloom_level_required_on_adopt():
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            source_passage_quote="eight word passage quote here now please keep",
            distractor_misconception="misconception",
        )


# ---------------------------------------------------------------------------
# MCQDraft.source_passage_quote  (8-30 words, non-empty after stripping)
# ---------------------------------------------------------------------------


def test_mcq_draft_source_passage_quote_rejects_empty_string():
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="remember",
            source_passage_quote="",
            distractor_misconception="m",
        )


def test_mcq_draft_source_passage_quote_rejects_over_30_words():
    too_long = " ".join(["w"] * 31)
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="remember",
            source_passage_quote=too_long,
            distractor_misconception="m",
        )


def test_mcq_draft_source_passage_quote_rejects_under_8_words():
    too_short = "too short here"
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="remember",
            source_passage_quote=too_short,
            distractor_misconception="m",
        )


def test_mcq_draft_source_passage_quote_accepts_30_words():
    just_right = " ".join(["w"] * 30)
    MCQDraft(
        question_text="x",
        options=["a", "b"],
        correct_option_index=0,
        bloom_level="remember",
        source_passage_quote=just_right,
        distractor_misconception="m",
    )


# ---------------------------------------------------------------------------
# MCQDraft.distractor_misconception  (non-empty, <= 200 chars)
# ---------------------------------------------------------------------------


def test_mcq_draft_distractor_misconception_rejects_empty_string():
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="remember",
            source_passage_quote="eight word passage quote here now please keep",
            distractor_misconception="",
        )


def test_mcq_draft_distractor_misconception_rejects_over_200_chars():
    too_long = "m" * 201
    with pytest.raises(ValidationError):
        MCQDraft(
            question_text="x",
            options=["a", "b"],
            correct_option_index=0,
            bloom_level="remember",
            source_passage_quote="eight word passage quote here now please keep",
            distractor_misconception=too_long,
        )


def test_mcq_draft_distractor_misconception_accepts_200_chars():
    MCQDraft(
        question_text="x",
        options=["a", "b"],
        correct_option_index=0,
        bloom_level="remember",
        source_passage_quote="eight word passage quote here now please keep",
        distractor_misconception="m" * 200,
    )


# ---------------------------------------------------------------------------
# QuizQuestion ORM — additive nullable columns
# ---------------------------------------------------------------------------


def _seed_quiz_question_with_no_new_columns(session: Session, user: User) -> int:
    """Insert a QuizQuestion row using the pre-Phase-9 shape (the three new
    columns stay NULL). When the new columns are *not yet* declared on the
    model this is the only valid path; once they exist with ``nullable=True``
    this helper still writes a row with them NULL.
    """
    assert user.id is not None
    entry = StudyEntry(
        user_id=user.id, topic="Mitosis", summary="s", raw_transcript="t"
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    quiz_set = QuizSet.model_validate(
        QuizSetCreate(entry_id=entry.id),
        update={
            "user_id": user.id,
            "question_count": 1,
            "option_count": 4,
            "generated_on": __import__("datetime").datetime.now(__import__("datetime").UTC).date(),
        },
    )
    session.add(quiz_set)
    session.commit()
    session.refresh(quiz_set)
    question = QuizQuestion.model_validate(
        QuizQuestionCreate(
            quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
            question_text="legacy q",
            options=["a", "b", "c", "d"],
            correct_option_index=0,
            order_index=0,
        )
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    assert question.id is not None
    return question.id


def test_quiz_question_accepts_legacy_row_with_new_columns_null(session, seeded_user):
    """Legacy rows (Phase 4-8 quiz rows) must load with the three new columns NULL."""
    qid = _seed_quiz_question_with_no_new_columns(session, seeded_user)
    reloaded = session.exec(select(QuizQuestion).where(QuizQuestion.id == qid)).one()
    assert getattr(reloaded, "bloom_level", None) is None
    assert getattr(reloaded, "source_passage_quote", None) is None
    assert getattr(reloaded, "distractor_misconception", None) is None


def test_quiz_question_persists_new_columns_when_set(session, seeded_user):
    qid = _seed_quiz_question_with_no_new_columns(session, seeded_user)
    reloaded = session.exec(select(QuizQuestion).where(QuizQuestion.id == qid)).one()

    reloaded.bloom_level = "apply"  # type: ignore[attr-defined]
    reloaded.source_passage_quote = "verbatim eight to thirty word passage"  # type: ignore[attr-defined]
    reloaded.distractor_misconception = "named misconception"  # type: ignore[attr-defined]
    session.add(reloaded)
    session.commit()
    session.refresh(reloaded)

    fresh = session.exec(select(QuizQuestion).where(QuizQuestion.id == qid)).one()
    assert fresh.bloom_level == "apply"  # type: ignore[attr-defined]
    assert fresh.source_passage_quote == "verbatim eight to thirty word passage"  # type: ignore[attr-defined]
    assert fresh.distractor_misconception == "named misconception"  # type: ignore[attr-defined]


def test_quiz_question_additive_columns_nullable_no_check_constraint(session, seeded_user):
    """The migration is additive: pre-Phase-9 quiz rows survive both directions."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        u = User(email="p9q@example.com", display_name="p9q", google_sub="p9q-sub")
        s.add(u)
        s.commit()
        s.refresh(u)
        qid = _seed_quiz_question_with_no_new_columns(s, u)
        # Setting the new columns to NULL must NOT violate a NOT NULL constraint.
        row = s.exec(select(QuizQuestion).where(QuizQuestion.id == qid)).one()
        assert getattr(row, "bloom_level", "missing") in (None, "missing")
        try:
            row.bloom_level = None  # type: ignore[attr-defined]
            s.add(row)
            s.commit()
        except IntegrityError as exc:  # pragma: no cover -- only hit if NOT NULL regression
            pytest.fail(f"NULL write on bloom_level violated NOT NULL: {exc}")


def test_phase9_columns_appear_in_quiz_question_model_fields():
    for name in ("bloom_level", "source_passage_quote", "distractor_misconception"):
        assert name in QuizQuestion.model_fields, f"QuizQuestion missing column {name!r}"


# ---------------------------------------------------------------------------
# MCQVerifyResult
# ---------------------------------------------------------------------------


def test_mcq_verify_result_verdict_accepts_yes_and_no():
    schemas = _schemas()
    MCQVerifyResult = schemas.MCQVerifyResult
    MCQVerifyResult(verdict="yes", reason="ok")
    MCQVerifyResult(verdict="no", reason="unsupported")


def test_mcq_verify_result_verdict_rejects_other_values():
    from pydantic import ValidationError as _VE

    schemas = _schemas()
    MCQVerifyResult = schemas.MCQVerifyResult
    with pytest.raises(_VE):
        MCQVerifyResult(verdict="maybe", reason="unsupported")
