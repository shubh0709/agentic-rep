# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — Production prompt snapshot.

Asserts the rendered system prompt contains every one of the six section
headings ("Role", "Source-First", "Citation", "Bloom", "Distractor",
"Few-shot") AND every Bloom literal string. Markers use the *exact*
fragments the production prompt carries.

This test is expected to fail today because ``_build_messages`` is not
yet a pure function exported by ``sra.agents.quiz_graph``.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import SystemMessage

pytestmark = pytest.mark.phase9


SIX_SECTION_HEADINGS = ("Role", "Source-First", "Citation", "Bloom", "Distractor", "Few-shot")

ALL_BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")


def _entry():
    """A minimal entry-like object so the render function works without DB."""
    return type(
        "_Entry",
        (),
        {
            "topic": "Transformer attention",
            "subtopic": "multi-head",
            "tags": ["deep-learning"],
            "summary": "how attention is computed and why multi-head works",
            "raw_transcript": "today I read about transformers",
        },
    )()


def _render() -> SystemMessage:
    from sra.agents.quiz_graph import _build_messages

    sys_msg, _ = _build_messages(
        _entry(),
        [],
        transcript_slice="",
        question_count=10,
    )
    return sys_msg


def test_system_prompt_contains_role_heading():
    sys_msg = _render()
    assert "Role" in sys_msg.content


def test_system_prompt_contains_source_first_heading():
    sys_msg = _render()
    assert "Source-First" in sys_msg.content


def test_system_prompt_contains_citation_heading():
    sys_msg = _render()
    assert "Citation" in sys_msg.content


def test_system_prompt_contains_bloom_heading():
    sys_msg = _render()
    assert "Bloom" in sys_msg.content


def test_system_prompt_contains_distractor_heading():
    sys_msg = _render()
    assert "Distractor" in sys_msg.content


def test_system_prompt_contains_few_shot_heading():
    sys_msg = _render()
    assert "Few-shot" in sys_msg.content


@pytest.mark.parametrize("level", ALL_BLOOM_LEVELS)
def test_system_prompt_contains_each_bloom_literal(level):
    sys_msg = _render()
    assert level in sys_msg.content, f"missing Bloom literal {level!r}"


def test_system_prompt_contains_complete_six_section_set():
    sys_msg = _render()
    content = sys_msg.content
    for heading in SIX_SECTION_HEADINGS:
        assert heading in content, f"missing section heading {heading!r}"


def test_system_prompt_references_distractor_misconception_field():
    sys_msg = _render()
    assert "distractor_misconception" in sys_msg.content


def test_system_prompt_references_source_passage_quote_field():
    sys_msg = _render()
    assert "source_passage_quote" in sys_msg.content
