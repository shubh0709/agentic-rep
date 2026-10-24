"""SRA-802 / SRA-904 — Interview planner agent.

Turns one ``StudyEntry`` into a structured ``InterviewOutline`` of N items
the SRA-803 conversation graph can expand into individual interview
questions. One LLM call per ``(entry, day)``; the per-turn expansion
itself lives inside the conversation graph and never calls this planner.

Mirrors the SRA-402 ``quiz_graph`` shape:

    [_call_planner] -> [_validate] -> END

Tests patch ``_get_chat_model`` at this module's symbol so a scripted
fake returns a deterministic ``InterviewOutline`` and no real OpenAI
request fires during ``pytest``.

SRA-904 extends the prompt to a six-section rubric mirroring SRA-903
(Role, Source-First, Citation, Bloom, Rubric, Few-shot) and adds three
optional fields to ``InterviewOutlineItem`` --
``source_passage_quote``, ``ideal_answer_rubric``, ``bloom_level`` -- so
the SRA-803 grader can anchor scoring to the same source material the
planner used.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, field_validator

from sra.config import get_settings
from sra.db.models import StudyEntry

logger = logging.getLogger(__name__)

_TRANSCRIPT_LIMIT = 1500

_FALLBACK_SUBTOPIC = "general recap"
_FALLBACK_DIFFICULTY: Literal["medium"] = "medium"
_FALLBACK_FOCUS_PROMPT = "general recap of earlier material"

_FALLBACK_SOURCE_PASSAGE_QUOTE = (
    "the general recap pulls together earlier material into a single "
    "summary that the learner can rehearse"
)
_FALLBACK_IDEAL_ANSWER_RUBRIC = (
    "A correct answer summarises the earlier material in plain language."
)
_FALLBACK_BLOOM_LEVEL: Literal["understand"] = "understand"


# ---------------------------------------------------------------------------
# SRA-904 prompt constants. Each block is a top-level section heading the
# test suite asserts is present in the rendered system prompt. The six
# headings MUST each appear once and MUST appear in the canonical order
# below. The "Rubric" heading replaces the MCQ-only "Distractor" block
# from SRA-903 because interviews have no multiple-choice options --
# the equivalent anti-hallucination device is the ``ideal_answer_rubric``
# the planner writes into each item.
# ---------------------------------------------------------------------------


PROMPT_ROLE_PREFIX = (
    "Role\n"
    "You are an expert study interviewee who designs open-ended interview "
    "questions for spaced-repetition study sessions. Your job is to "
    "produce assessment material whose correctness can be verified "
    "against the supplied source passages."
)


PROMPT_SOURCE_FIRST_RULE = (
    "Source-First\n"
    "Every question must be answerable using only information explicitly "
    "present in the supplied source passages and the entry summary. Do "
    "not use any external knowledge. If a question cannot be made "
    "answerable from the supplied material, omit it from the list rather "
    "than invent information."
)


PROMPT_CITATION_REQUIREMENT = (
    "Citation\n"
    "Each item must include a ``source_passage_quote`` of 8 to 30 words "
    "drawn verbatim from a source passage, so the learner can jump to the "
    "source. The per-turn grader uses this excerpt to confirm the "
    "answer is grounded in the cited material."
)


PROMPT_BLOOMS_DISTRIBUTION_RULE = (
    "Bloom\n"
    "Tag each item with one of these six Bloom levels, drawing from all "
    "six over the whole outline: remember, understand, apply, analyze, "
    "evaluate, create. As a default difficulty distribution for the "
    "interview, aim for 1/3 easy, 1/3 medium, 1/3 hard -- which maps to "
    "remember-or-understand, apply-or-analyze, evaluate-or-create "
    "respectively; scale the split proportionally for other counts and "
    "keep the LLM free to rebalance when the entry demands it."
)


PROMPT_RUBRIC_RULE = (
    "Rubric\n"
    "Each item must include an ``ideal_answer_rubric`` describing the "
    "canonical answer in 1 to 2 sentences (1 to 200 characters after "
    "stripping). The per-turn grader uses this rubric to anchor the "
    "user's free-form answer against the planner's expected answer -- "
    "not against an LLM-invented rubric. The rubric must be specific "
    "enough that a correct answer is recognisable."
)


INTERVIEW_FEW_SHOT_EXAMPLE = (
    "Few-shot\n"
    "One worked example to anchor the shape -- your output must mirror "
    "every field listed here, including source_passage_quote, "
    "ideal_answer_rubric, and bloom_level:\n"
    "    subtopic: Scaled dot-product attention\n"
    "    difficulty: medium\n"
    "    focus_prompt: Walk me through why we divide by sqrt(d_k).\n"
    "    source_passage_quote: We scale by 1/sqrt(d_k) to keep the "
    "variance of QK^T stable as d_k grows\n"
    "    ideal_answer_rubric: A correct answer states that without "
    "scaling, large d_k pushes softmax into saturated regions; "
    "sqrt(d_k) keeps dot products in a low-variance range.\n"
    "    bloom_level: apply"
)


SYSTEM_PROMPT = "\n\n".join(
    [
        PROMPT_ROLE_PREFIX,
        PROMPT_SOURCE_FIRST_RULE,
        PROMPT_CITATION_REQUIREMENT,
        PROMPT_BLOOMS_DISTRIBUTION_RULE,
        PROMPT_RUBRIC_RULE,
        INTERVIEW_FEW_SHOT_EXAMPLE,
    ]
)


REFERENCE_MARKER = "[... truncated at {n} chars ...]"
MAX_REFERENCE_TOTAL_CHARS = 12_000
MAX_REFERENCE_PER_REF_CHARS = 6_000


class InterviewOutlineItem(BaseModel):
    """One item of the interview outline.

    ``subtopic`` and ``focus_prompt`` are stripped of whitespace before
    validation; an empty string after stripping is rejected so an LLM
    that emits a blank prompt can never silently produce a degenerate
    outline.

    SRA-904 adds three optional fields -- ``source_passage_quote``,
    ``ideal_answer_rubric``, ``bloom_level`` -- that the per-turn grader
    uses to anchor scoring. All three default to ``None`` so legacy
    callers and pre-Phase-9 plans keep parsing.
    """

    subtopic: str = Field(min_length=1, max_length=80)
    difficulty: Literal["easy", "medium", "hard"]
    focus_prompt: str = Field(min_length=1, max_length=240)
    source_passage_quote: str | None = None
    ideal_answer_rubric: str | None = None
    bloom_level: (
        Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
        | None
    ) = None

    @field_validator("subtopic", "focus_prompt", mode="before")
    @classmethod
    def _strip_and_require_nonempty(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValueError("must not be empty after stripping")
            return stripped
        return value

    @field_validator("source_passage_quote", mode="before")
    @classmethod
    def _validate_source_passage_quote(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("source_passage_quote must be a string or None")
        stripped = value.strip()
        if not stripped:
            raise ValueError("source_passage_quote must not be empty after stripping")
        word_count = len(stripped.split())
        if word_count < 8 or word_count > 30:
            raise ValueError(
                f"source_passage_quote must be 8 to 30 words; got {word_count}"
            )
        return stripped

    @field_validator("ideal_answer_rubric", mode="before")
    @classmethod
    def _validate_ideal_answer_rubric(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("ideal_answer_rubric must be a string or None")
        stripped = value.strip()
        if not stripped:
            raise ValueError("ideal_answer_rubric must not be empty after stripping")
        if not 1 <= len(stripped) <= 200:
            raise ValueError(
                f"ideal_answer_rubric must be 1 to 200 chars; got {len(stripped)}"
            )
        return stripped

    @field_validator("bloom_level", mode="before")
    @classmethod
    def _validate_bloom_level(cls, value: object) -> object:
        if value is None:
            return None
        allowed = ("remember", "understand", "apply", "analyze", "evaluate", "create")
        if value not in allowed:
            raise ValueError(
                f"bloom_level must be one of {allowed}; got {value!r}"
            )
        return value


class InterviewOutline(BaseModel):
    """Container the planner LLM emits and the conversation graph consumes."""

    items: list[InterviewOutlineItem] = Field(default_factory=list)


class InterviewPlannerState(TypedDict):
    entry: StudyEntry
    target_questions: int
    references: list[tuple[str, str]]
    focus_topics: list[str]
    outline: InterviewOutline
    error: str | None


def _get_chat_model() -> ChatOpenAI:
    """Construct the OpenAI model used by ``generate_interview_outline``.

    Mirrors ``quiz_graph._get_chat_model`` so tests can monkey-patch
    ``sra.agents.interview_planner._get_chat_model`` with a scripted fake
    and exercise the planner without any live OpenAI call. The
    ``max_completion_tokens=1024`` / ``timeout=20`` / ``max_retries=1``
    knobs are deliberately smaller than ``quiz_graph`` because the
    outline payload is bounded and one retry is enough to recover from a
    transient API hiccup.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=1024,
        timeout=20,
        max_retries=1,
    )


def _coerce_item(raw: Any) -> InterviewOutlineItem:
    """Return an :class:`InterviewOutlineItem` for a dict-shaped or already-typed item.

    The ``with_structured_output(InterviewOutline)`` binding already gives
    us a Pydantic container, but some LangChain backends still return
    plain dicts; this helper re-parses them so the ``_validate`` node can
    uniformly rescale the result to ``target_questions``.
    """
    if isinstance(raw, InterviewOutlineItem):
        return raw
    if isinstance(raw, dict):
        return InterviewOutlineItem(**raw)
    raise ValueError(
        f"Unsupported outline item type: {type(raw).__name__}"
    )


def _resize_outline(
    items: list[InterviewOutlineItem],
    target: int,
) -> list[InterviewOutlineItem]:
    """Trim or pad ``items`` so ``len(items) == target``.

    The planner's contract is one ``InterviewOutlineItem`` per requested
    question. Trimming from the end is the safest "the LLM said more
    than asked" behavior; padding uses a deterministic ``general recap``
    fallback so a short outline still yields a valid ``target_questions``
    sized list rather than failing the whole batch. SRA-904 also
    populates the three new rubric fields on the padding items so the
    per-turn grader never sees a ``None``-valued rubric from a
    planner-padded slot.
    """
    if len(items) > target:
        return items[:target]
    while len(items) < target:
        items.append(
            InterviewOutlineItem(
                subtopic=f"{_FALLBACK_SUBTOPIC} {len(items) + 1}",
                difficulty=_FALLBACK_DIFFICULTY,
                focus_prompt=_FALLBACK_FOCUS_PROMPT,
                source_passage_quote=_FALLBACK_SOURCE_PASSAGE_QUOTE,
                ideal_answer_rubric=_FALLBACK_IDEAL_ANSWER_RUBRIC,
                bloom_level=_FALLBACK_BLOOM_LEVEL,
            )
        )
    return items


def _build_messages(
    entry: StudyEntry,
    references: list[tuple[str, str]],
    *,
    transcript_slice: str,
    target_questions: int,
    focus_topics: list[str] | None = None,
) -> tuple[SystemMessage, HumanMessage]:
    """Render the SRA-904 six-section system prompt + a references /
    transcript / metadata human message.

    The six system-prompt section headings ("Role", "Source-First",
    "Citation", "Bloom", "Rubric", "Few-shot") appear once each, in that
    order. The function is pure -- the only side effect is reading the
    entry / references passed in -- so the Phase 9 test suite can import
    it directly and call it without monkey-patching.

    ``references`` is the per-entry ``(title, excerpt)`` list already
    shaped by ``repository.get_reference_text_bundle``; total characters
    across all excerpts are capped at ``MAX_REFERENCE_TOTAL_CHARS`` and
    each excerpt is capped at ``MAX_REFERENCE_PER_REF_CHARS``. When the
    total cap is exhausted, later references are dropped on the floor
    so the prompt never overflows the model's context window.

    ``transcript_slice`` is the pre-truncated raw transcript (or empty
    string when the entry has none); the caller controls the cap so the
    SRA-804 repository helpers can reuse the same truncation logic.
    """
    system = SystemMessage(content=SYSTEM_PROMPT)

    lines: list[str] = []
    lines.append("Entry metadata:")
    lines.append(f"  Topic: {entry.topic}")
    if entry.subtopic:
        lines.append(f"  Subtopic: {entry.subtopic}")
    if entry.tags:
        lines.append(f"  Tags: {', '.join(entry.tags)}")
    lines.append(f"  Summary: {entry.summary}")
    if focus_topics:
        lines.append(f"  Required focus topics: {', '.join(focus_topics)}")
    lines.append("")
    lines.append(
        f"Generate exactly {target_questions} open-ended interview "
        f"questions. Each question must include subtopic, difficulty, "
        f"focus_prompt, source_passage_quote, ideal_answer_rubric, and "
        f"bloom_level."
    )
    lines.append("")

    lines.append("References:")
    if not references:
        lines.append("  (none attached)")
    else:
        remaining = MAX_REFERENCE_TOTAL_CHARS
        for title, excerpt in references:
            if remaining <= 0:
                break
            excerpt = excerpt or ""
            if len(excerpt) > MAX_REFERENCE_PER_REF_CHARS:
                excerpt = (
                    excerpt[:MAX_REFERENCE_PER_REF_CHARS]
                    + REFERENCE_MARKER.format(n=MAX_REFERENCE_PER_REF_CHARS)
                )
            if len(excerpt) > remaining:
                excerpt = (
                    excerpt[:remaining] + REFERENCE_MARKER.format(n=remaining)
                )
                remaining = 0
            else:
                remaining -= len(excerpt)
            lines.append(f"  Reference: {title}")
            lines.append(f"    {excerpt}")
    lines.append("")

    if transcript_slice:
        lines.append("Transcript:")
        lines.append(f"  {transcript_slice}")

    human = HumanMessage(content="\n".join(lines))
    return system, human


def _call_planner(state: InterviewPlannerState) -> dict[str, Any]:
    """Invoke the LLM and shape the result into an ``InterviewOutline``."""
    entry: StudyEntry = state["entry"]
    target_questions: int = state["target_questions"]
    references: list[tuple[str, str]] = state.get("references") or []
    transcript_slice = entry.raw_transcript[:_TRANSCRIPT_LIMIT]
    messages = _build_messages(
        entry,
        references,
        transcript_slice=transcript_slice,
        target_questions=target_questions,
        focus_topics=state.get("focus_topics"),
    )
    try:
        model = _get_chat_model()
        structured_model = model.with_structured_output(
            InterviewOutline, method="function_calling"
        )
        result = structured_model.invoke(messages)
        if not isinstance(result, InterviewOutline):
            raise ValueError(
                f"Planner returned {type(result).__name__}, expected InterviewOutline"
            )
        items = [_coerce_item(item) for item in result.items]
        outline = InterviewOutline(items=_resize_outline(items, target_questions))
        return {**state, "outline": outline, "error": None}
    except Exception as exc:
        from sra.agents.quiz_graph import QuizGenerationError

        logger.exception("Interview planner failed for entry_id=%s", getattr(entry, "id", None))
        QuizGenerationError(f"Interview planner failed: {exc}")
        return {**state, "outline": InterviewOutline(items=[]), "error": str(exc)}


def _validate_node(state: InterviewPlannerState) -> dict[str, Any]:
    """No-op validation node — keeps the LangGraph shape symmetric with ``quiz_graph``."""
    return state  # pyright: ignore[reportReturnType]


def _route_after_call(state: InterviewPlannerState) -> str:
    """Skip the validate node when the LLM step produced no usable outline."""
    if state.get("error") or not state.get("outline").items:
        return END
    return "validate"


def build_interview_planner_graph():
    """Compile the planner LangGraph.

    Shape: ``START -> _call_planner -> _validate -> END`` with a
    conditional edge after ``_call_planner`` that short-circuits to
    ``END`` on an empty outline or a recorded error. The graph is
    optional — ``generate_interview_outline`` drives the same nodes
    directly when an ``llm`` override is supplied so unit tests can
    bypass LangGraph invocation.
    """
    graph = StateGraph(InterviewPlannerState)
    graph.add_node("call_planner", _call_planner)
    graph.add_node("validate", _validate_node)
    graph.add_edge(START, "call_planner")
    graph.add_conditional_edges(
        "call_planner",
        _route_after_call,
        {"validate": "validate", END: END},
    )
    graph.add_edge("validate", END)
    return graph.compile(name="sra-interview-planner")


def generate_interview_outline(
    entry: StudyEntry,
    target_questions: int,
    *,
    llm: Any | None = None,
    references: list[tuple[str, str]] | None = None,
    focus_topics: list[str] | None = None,
) -> InterviewOutline:
    """Return a validated ``InterviewOutline`` of exactly ``target_questions`` items.

    When ``llm`` is supplied the planner drives that model directly (so
    unit tests can substitute a fake without invoking the LangGraph).
    Without an override the planner delegates to
    :func:`build_interview_planner_graph` — the ``_call_planner`` node
    reads from ``_get_chat_model`` via the same seam ``quiz_graph`` uses.

    ``references`` is the SRA-901 ``(title, excerpt)`` bundle from
    ``repository.get_reference_text_bundle``; pass ``None`` or ``[]`` to
    skip source-grounding (the system prompt still carries the six
    sections, the human message just omits the references block).

    On any LLM or validation failure this function raises
    ``QuizGenerationError`` so callers can distinguish "LLM down" from
    "LLM succeeded but returned an empty list" (SRA-809 contract). The
    pregeneration job is the owner of the error counter -- the planner
    never silently returns an empty outline.
    """
    refs = references if references is not None else []
    transcript_slice = entry.raw_transcript[:_TRANSCRIPT_LIMIT]
    messages = _build_messages(
        entry,
        refs,
        transcript_slice=transcript_slice,
        target_questions=target_questions,
        focus_topics=focus_topics,
    )
    try:
        model = llm if llm is not None else _get_chat_model()
        structured_model = model.with_structured_output(
            InterviewOutline, method="function_calling"
        )
        result = structured_model.invoke(messages)
        if not isinstance(result, InterviewOutline):
            raise ValueError(
                f"Planner returned {type(result).__name__}, expected InterviewOutline"
            )
        items = [_coerce_item(item) for item in result.items]
        return InterviewOutline(items=_resize_outline(items, target_questions))
    except Exception as exc:
        from sra.agents.quiz_graph import QuizGenerationError

        logger.exception(
            "Interview planner failed for entry_id=%s", getattr(entry, "id", None)
        )
        raise QuizGenerationError(f"Interview planner failed: {exc}") from exc


__all__ = [
    "InterviewOutline",
    "InterviewOutlineItem",
    "INTERVIEW_FEW_SHOT_EXAMPLE",
    "SYSTEM_PROMPT",
    "build_interview_planner_graph",
    "generate_interview_outline",
]
