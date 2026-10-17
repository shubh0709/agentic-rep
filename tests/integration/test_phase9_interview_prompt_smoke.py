# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-904 — Production prompt snapshot for the interview planner + grader.

Mirrors the SRA-903 ``test_phase9_quiz_prompt_smoke.py`` strategy: pin
the section headings the production prompt must carry, plus the exact
fragments ``ideal_answer_rubric``, ``source_passage_quote``,
``bloom_level``, and the per-turn grading rubric block.

Today this test fails because ``sra.agents.interview_planner._build_messages``
does not yet accept ``references=`` / ``transcript_slice=`` keyword
arguments and ``sra.agents.interview_conversation_graph._grading_prompt``
does not yet exist.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Section headings the production prompt must carry. The interviewer-side
# equivalent of the MCQ headings in SRA-903, minus "Distractor" (MCQ-only)
# and plus "Rubric" (interview-only).
# ---------------------------------------------------------------------------


PLANNER_SECTION_HEADINGS = (
    "Role",
    "Source-First",
    "Citation",
    "Rubric",
    "Bloom",
    "Few-shot",
)

ALL_BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")


# ---------------------------------------------------------------------------
# Planner render — pure prompt-renderer call (mirrors the SRA-903 smoke
# test idiom). The SRA-903 smoke test also exercises only the system
# prompt, so we do the same here.
# ---------------------------------------------------------------------------


def _entry():
    class _Entry:
        topic = "Transformer attention"
        subtopic = "multi-head"
        tags = ["deep-learning"]
        summary = "how attention is computed and why multi-head works"
        raw_transcript = "today I read about transformers"

    return _Entry()


def _render_planner() -> SystemMessage:
    from sra.agents.interview_planner import _build_messages

    sys_msg, _ = _build_messages(
        _entry(),
        [],
        transcript_slice="",
        target_questions=6,
    )
    return sys_msg


def test_planner_system_prompt_contains_role_heading():
    sys_msg = _render_planner()
    assert "Role" in sys_msg.content


def test_planner_system_prompt_contains_source_first_heading():
    sys_msg = _render_planner()
    assert "Source-First" in sys_msg.content


def test_planner_system_prompt_contains_citation_heading():
    sys_msg = _render_planner()
    assert "Citation" in sys_msg.content


def test_planner_system_prompt_contains_rubric_heading():
    sys_msg = _render_planner()
    assert "Rubric" in sys_msg.content


def test_planner_system_prompt_contains_bloom_heading():
    sys_msg = _render_planner()
    assert "Bloom" in sys_msg.content


def test_planner_system_prompt_contains_few_shot_heading():
    sys_msg = _render_planner()
    assert "Few-shot" in sys_msg.content


def test_planner_system_prompt_contains_complete_six_section_set():
    sys_msg = _render_planner()
    content = sys_msg.content
    for heading in PLANNER_SECTION_HEADINGS:
        assert heading in content, f"missing section heading {heading!r}"


def test_planner_system_prompt_references_source_passage_quote_field():
    sys_msg = _render_planner()
    assert "source_passage_quote" in sys_msg.content


def test_planner_system_prompt_references_ideal_answer_rubric_field():
    sys_msg = _render_planner()
    assert "ideal_answer_rubric" in sys_msg.content


def test_planner_system_prompt_references_bloom_level_field():
    sys_msg = _render_planner()
    assert "bloom_level" in sys_msg.content


@pytest.mark.parametrize("level", ALL_BLOOM_LEVELS)
def test_planner_system_prompt_contains_each_bloom_literal(level):
    sys_msg = _render_planner()
    assert level in sys_msg.content, f"missing Bloom literal {level!r}"


# ---------------------------------------------------------------------------
# Grader render — the per-turn grading seam carries the rubric block too.
# ---------------------------------------------------------------------------


def _render_grader() -> HumanMessage:
    from sra.agents.interview_conversation_graph import _grading_prompt

    plan_item = {
        "subtopic": "scaled dot-product attention",
        "difficulty": "medium",
        "focus_prompt": "Walk me through why we divide by sqrt(d_k).",
    }
    _, human = _grading_prompt(
        plan_item,
        [],
        "we divide by sqrt(d_k)",
        source_passage_quote=(
            "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable"
        ),
        ideal_answer_rubric=(
            "sqrt(d_k) keeps dot products in a low-variance range."
        ),
        bloom_level="apply",
    )
    return human


def test_grader_human_message_contains_source_passage_quote():
    human = _render_grader()
    assert "We scale by 1/sqrt(d_k)" in human.content


def test_grader_human_message_contains_ideal_answer_rubric():
    human = _render_grader()
    assert (
        "sqrt(d_k) keeps dot products in a low-variance range." in human.content
    )


def test_grader_human_message_contains_bloom_level():
    human = _render_grader()
    assert "apply" in human.content.lower()