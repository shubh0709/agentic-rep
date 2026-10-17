# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-904 — Interview planner prompt uplift + outline-item extension.

Every test in this module is expected to fail today because the
``sra.agents.interview_planner`` rewrite has not landed yet:

  * ``_build_messages`` does not accept ``references`` /
    ``transcript_slice`` keyword arguments; imports of the new signature
    raise ``TypeError: unexpected keyword argument``.
  * ``InterviewOutlineItem`` does not declare ``source_passage_quote``,
    ``ideal_answer_rubric``, or ``bloom_level``; assertions on those
    attributes raise ``AttributeError``.
  * ``_resize_outline`` padding items do not carry the new fields.

The tests pin a *pure* prompt-renderer signature
(``_build_messages(entry, references, *, transcript_slice, target_questions) -> tuple[SystemMessage, HumanMessage]``)
mirroring the SRA-903 contract for ``sra.agents.quiz_graph``. The
truncation marker format -- ``[... truncated at N chars ...]`` --
matches the SRA-901 ``repository.get_reference_text_bundle`` helper so
the implementer can reuse that helper's output.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Reference shape: list[(title, text)] mirrors the bundle returned by
# ``repository.get_reference_text_bundle``. Keeping the public shape lets
# the planner stay DB-agnostic at the prompt-renderer layer.
# ---------------------------------------------------------------------------


ALL_BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")


def _import_planner() -> Any:
    from sra.agents import interview_planner

    return interview_planner


def _entry():
    """A minimal entry-like duck-typed object so the render function works without DB."""

    class _Entry:
        topic = "Transformer attention"
        subtopic = "multi-head"
        tags = ["deep-learning"]
        summary = "how attention is computed and why multi-head works"
        raw_transcript = "today I read about transformers"

    return _Entry()


def _build_messages(entry, references, *, transcript_slice, target_questions):
    """Pure renderer call (new signature).

    Today this raises ``TypeError`` because ``_build_messages`` takes
    ``(entry, target_questions)`` -- the new keyword arguments
    ``references`` and ``transcript_slice`` don't fit the old signature.
    """
    planner = _import_planner()
    return planner._build_messages(
        entry,
        references,
        transcript_slice=transcript_slice,
        target_questions=target_questions,
    )


# ---------------------------------------------------------------------------
# System-prompt rendering
# ---------------------------------------------------------------------------


def test_rendered_system_prompt_contains_role_prefix():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=4
    )
    # "expert study interviewee" is the exact phrasing the plan pins.
    assert "expert study interviewee" in system.content.lower()


def test_rendered_system_prompt_contains_source_first_rule():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=4
    )
    # The source-first rule blocks external knowledge; tests pin a
    # distinctive phrase so a rewrite that drops the rule fails here.
    lowered = system.content.lower()
    assert "source" in lowered
    assert (
        "external" in lowered
        or "no external" in lowered
        or "do not use" in lowered
        or "must not" in lowered
    )


def test_rendered_system_prompt_contains_citation_requirement():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=4
    )
    # The plan §E pins the literal field name ``source_passage_quote`` of
    # 8-30 words; the prompt must surface it so the LLM emits it.
    assert "source_passage_quote" in system.content
    assert "8" in system.content and "30" in system.content


def test_rendered_system_prompt_contains_rubric_for_ideal_answer():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=4
    )
    # The interview-planner prompt carries an "ideal_answer_rubric" rule
    # describing the canonical answer in 1-2 sentences.
    assert "ideal_answer_rubric" in system.content


def test_rendered_system_prompt_contains_blooms_distribution():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=10
    )
    content = system.content
    # The plan §E Bloom distribution spans 6 levels; tests pin the
    # literal strings so the implementer can't drop a level.
    for level in ALL_BLOOM_LEVELS:
        assert level in content, f"missing Bloom level {level!r}"
    assert "bloom" in content.lower()


def test_rendered_system_prompt_contains_few_shot_exemplar():
    system, _ = _build_messages(
        _entry(), [], transcript_slice="", target_questions=4
    )
    # A single high-quality exemplar must include the three new fields so
    # the LLM learns the shape by example.
    content = system.content
    assert "source_passage_quote" in content
    assert "ideal_answer_rubric" in content
    assert "bloom_level" in content


# ---------------------------------------------------------------------------
# Human-message rendering
# ---------------------------------------------------------------------------


def test_rendered_human_message_includes_reference_excerpts():
    references = [("Lecture notes", "...scaled dot-product attention …")]
    _, human = _build_messages(
        _entry(), references, transcript_slice="", target_questions=4
    )
    assert "Lecture notes" in human.content
    assert "scaled dot-product attention" in human.content


def test_rendered_human_message_truncates_with_marker():
    long_text = "x" * 7000  # > max_chars_per_ref (6000)
    references = [("Lecture notes", long_text)]
    _, human = _build_messages(
        _entry(), references, transcript_slice="", target_questions=4
    )
    # The marker format mirrors the SRA-901 repository helper.
    assert "[... truncated at 6000 chars ...]" in human.content


def test_rendered_human_message_totals_capped():
    big_text_a = "a" * 11_000
    big_text_b = "b" * 11_000
    references = [("Ref A", big_text_a), ("Ref B", big_text_b)]
    _, human = _build_messages(
        _entry(), references, transcript_slice="", target_questions=4
    )
    # The total has to fit within ~max_chars_total = 12_000; one of the
    # references will have been capped and must surface the marker.
    lowered = human.content
    assert (
        "[... truncated at 12000 chars ...]" in lowered
        or "[... truncated at 6000 chars ...]" in lowered
    )


def test_rendered_human_message_includes_original_transcript_slice_when_present():
    slice_text = "I originally heard the professor say multi-head attention …"
    _, human = _build_messages(
        _entry(), [], transcript_slice=slice_text, target_questions=4
    )
    assert slice_text in human.content


# ---------------------------------------------------------------------------
# InterviewOutlineItem extension
# ---------------------------------------------------------------------------


def _make_item(**overrides):
    planner = _import_planner()
    base = dict(
        subtopic="scaled dot-product attention",
        difficulty="medium",
        focus_prompt="Walk me through why we divide by sqrt(d_k).",
    )
    base.update(overrides)
    return planner.InterviewOutlineItem(**base)


def test_planner_outline_item_carries_source_passage_quote_when_present():
    item = _make_item(
        source_passage_quote="We scale by 1/sqrt(d_k) to keep the variance of QK^T stable"
    )
    assert item.source_passage_quote == (
        "We scale by 1/sqrt(d_k) to keep the variance of QK^T stable"
    )


def test_planner_outline_item_carries_ideal_answer_rubric_when_present():
    item = _make_item(
        ideal_answer_rubric=(
            "A correct answer states that without scaling, large d_k pushes softmax "
            "into saturated regions; sqrt(d_k) keeps dot products in a low-variance range."
        )
    )
    assert item.ideal_answer_rubric is not None
    assert "softmax" in item.ideal_answer_rubric


def test_planner_outline_item_carries_bloom_level_when_present():
    item = _make_item(bloom_level="apply")
    assert item.bloom_level == "apply"


def test_planner_outline_item_validates_source_passage_quote_word_count_8_to_30():
    """Quotes shorter than 8 words or longer than 30 must be rejected."""
    from pydantic import ValidationError

    # 7 words: too short
    with pytest.raises(ValidationError):
        _make_item(source_passage_quote="too short a quote here apparently")
    # 31 words: too long
    too_long = " ".join(["word"] * 31)
    with pytest.raises(ValidationError):
        _make_item(source_passage_quote=too_long)


def test_planner_outline_item_validates_ideal_answer_rubric_1_to_200_chars():
    """Rubrics must be 1-200 chars after stripping whitespace."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_item(ideal_answer_rubric="")
    long_rubric = "x" * 201
    with pytest.raises(ValidationError):
        _make_item(ideal_answer_rubric=long_rubric)


def test_planner_outline_item_bloom_level_rejects_invalid_literal():
    """Bloom levels are pinned to the six interview levels."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _make_item(bloom_level="expert")


# ---------------------------------------------------------------------------
# _resize_outline fallback
# ---------------------------------------------------------------------------


def test_resize_outline_pads_with_general_recap_item_that_carries_new_fields():
    """_resize_outline padding items also carry the new fields.

    Each filler must include ``source_passage_quote``,
    ``ideal_answer_rubric``, and ``bloom_level`` so the conversation
    graph never grades against an item that lacks the rubric.
    """
    planner = _import_planner()
    short = [
        planner.InterviewOutlineItem(
            subtopic="a",
            difficulty="easy",
            focus_prompt="x",
        )
    ]
    resized = planner._resize_outline(short, target=3)
    assert len(resized) == 3
    for filler in resized[1:]:
        # Padding items also carry the new fields so the grader never
        # sees a None-valued rubric.
        assert filler.source_passage_quote, (
            f"filler item missing source_passage_quote: {filler!r}"
        )
        assert filler.ideal_answer_rubric, (
            f"filler item missing ideal_answer_rubric: {filler!r}"
        )
        assert filler.bloom_level in ALL_BLOOM_LEVELS, (
            f"filler item has invalid bloom_level: {filler.bloom_level!r}"
        )