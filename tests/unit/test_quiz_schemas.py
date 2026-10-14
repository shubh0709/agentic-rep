"""SRA-402 MCQDraft / MCQDrafts schema contracts."""

import pytest
from pydantic import ValidationError

from sra.agents.schemas import MCQDraft, MCQDrafts

pytestmark = pytest.mark.phase4


def test_mcq_draft_round_trips_with_expected_fields():
    draft = MCQDraft(
        question_text="Which wavelength drives photosystem II?",
        options=["680 nm", "700 nm", "500 nm"],
        correct_option_index=0,
        explanation="Photosystem II absorbs light at 680 nm.",
    )

    assert draft.question_text == "Which wavelength drives photosystem II?"
    assert draft.options == ["680 nm", "700 nm", "500 nm"]
    assert draft.correct_option_index == 0
    assert draft.explanation == "Photosystem II absorbs light at 680 nm."


def test_mcq_draft_options_default_to_empty_list():
    draft = MCQDraft(question_text="x", correct_option_index=0, explanation="")

    assert draft.options == []


def test_mcq_draft_explanation_defaults_to_empty_string():
    draft = MCQDraft(question_text="x", options=["a"], correct_option_index=0)

    assert draft.explanation == ""


def test_mcq_draft_correct_option_index_must_be_non_negative_integer():
    with pytest.raises(ValidationError):
        MCQDraft(question_text="x", options=["a"], correct_option_index=-1)


def test_mcq_draft_correct_option_index_must_be_integer():
    with pytest.raises(ValidationError):
        MCQDraft(question_text="x", options=["a"], correct_option_index="0")


def test_mcq_drafts_carries_a_list_of_drafts():
    drafts = MCQDrafts(
        drafts=[
            MCQDraft(question_text="q1", options=["a", "b"], correct_option_index=0),
            MCQDraft(question_text="q2", options=["a", "b"], correct_option_index=1),
        ]
    )

    assert len(drafts.drafts) == 2
    assert all(isinstance(d, MCQDraft) for d in drafts.drafts)
    assert [d.question_text for d in drafts.drafts] == ["q1", "q2"]


def test_mcq_drafts_defaults_to_empty_list():
    container = MCQDrafts()

    assert container.drafts == []


def test_mcq_drafts_serializes_via_model_dump():
    container = MCQDrafts(
        drafts=[MCQDraft(question_text="q", options=["a"], correct_option_index=0, explanation="e")]
    )

    payload = container.model_dump()

    assert payload == {
        "drafts": [
            {
                "question_text": "q",
                "options": ["a"],
                "correct_option_index": 0,
                "explanation": "e",
            }
        ]
    }