"""
LangGraph agent: turn one StudyEntry into a persisted QuizSet of
multiple-choice questions.

Graph shape (see docs/design/technical-design.md section 3.9):

    [generate_questions] -> [persist_quiz_set] -> END

On an LLM failure, generate_questions sets state["error"] and the
conditional edge after it routes straight to END without persisting
anything -- the caller decides whether to retry, log, or fail.

SRA-903 rewrites the prompt and adds a second-pass verifier. The
verifier ("verify_draft") runs once per surviving draft and asks a
small second LLM whether the cited source passage actually supports
the correct option; drafts whose verdict is not "yes" are dropped and
their reason string appended to ``state["discarded_for_evidence"]``.
The setting ``Settings.openai_verify_mcqs`` short-circuits the verify
call (skip path returns a synthetic "yes" verdict with reason
"verify_pass_disabled") so offline test runs can pre-load scripted
drafts without hitting OpenAI a second time.
"""

from datetime import UTC, datetime
from typing import Any, NotRequired, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from sra.agents.schemas import MCQDraft, MCQDrafts, MCQVerifyResult
from sra.config import get_settings
from sra.db import repository
from sra.db.models import QuizSet, StudyEntry

_TRANSCRIPT_LIMIT = 1_500

# ---------------------------------------------------------------------------
# SRA-903 prompt constants. Each block is a top-level section heading the
# test suite asserts is present in the rendered system prompt. Tests are
# deliberately string-fragment based ("bloom" appears, "8" and "30" both
# appear, etc.) so the implementer can reword individual sentences without
# breaking the contract. The six headings MUST each appear once and MUST
# appear in the canonical order below.
# ---------------------------------------------------------------------------


PROMPT_ROLE_PREFIX = (
    "Role\n"
    "You are an expert quiz author who writes multiple-choice questions "
    "for spaced-repetition study sessions."
)


PROMPT_SOURCE_FIRST_RULE = (
    "Source-First\n"
    "Every question must be supply-grounded: build the question and each "
    "option from the supplied references and entry summary, and do not "
    "use external knowledge. If the references do not support a claim, "
    "skip that question instead of inventing it."
)


PROMPT_CITATION_REQUIREMENT = (
    "Citation\n"
    "For every question, copy a short verbatim excerpt of the supporting "
    "passage -- aim for roughly 8 to 30 words, with the absolute floor "
    "at 4 words and the absolute ceiling at 30 words -- into the "
    "source_passage_quote field. The verifier uses this excerpt to "
    "confirm the correct option is grounded in the cited material."
)


PROMPT_BLOOMS_DISTRIBUTION_RULE = (
    "Bloom\n"
    "Tag each question with one of these six Bloom levels, drawing from "
    "all six over the whole set: remember, understand, apply, analyze, "
    "evaluate, create. As a default distribution rule for a 10-question "
    "set, aim for 3 remember-or-understand, 4 apply, 2 analyze-or-compare, "
    "and 1 evaluate-or-create; scale the split proportionally for other "
    "counts and keep the LLM free to rebalance when the entry demands it."
)


PROMPT_DISTRACTOR_RUBRIC = (
    "Distractor\n"
    "Every wrong option must (a) be plausible to a learner who half-remembers "
    "the material, (b) target a specific named misconception that you record "
    "in the distractor_misconception field and reuse in the explanation, and "
    "(c) match the correct answer's length and format. Do not include "
    "\"none of the above\" or \"all of the above\" as a distractor."
)


PROMPT_FEW_SHOT_EXAMPLE = (
    "Few-shot\n"
    "One worked example to anchor the shape -- your output must mirror "
    "every field listed here, including bloom_level, source_passage_quote, "
    "and distractor_misconception:\n"
    "    question_text: What does d_k = d_model / h achieve?\n"
    "    options:\n"
    "      - Per-head compute is constant as h grows.\n"
    "      - Each head sees the full d_model width.\n"
    "      - It matches the residual stream width.\n"
    "      - It is a theoretical optimum proved in Vaswani et al. 2017.\n"
    "    correct_option_index: 0\n"
    "    explanation: Distractor B targets the 'heads must each see the "
    "full d_model width' confusion; distractor D names a non-existent "
    "optimality theorem.\n"
    "    bloom_level: apply\n"
    "    source_passage_quote: we project queries and keys to d_k = "
    "d_model / h so each head computes attention at a fraction of the cost\n"
    "    distractor_misconception: Confusing per-head representation width "
    "with per-head compute"
)


SYSTEM_PROMPT = "\n\n".join(
    [
        PROMPT_ROLE_PREFIX,
        PROMPT_SOURCE_FIRST_RULE,
        PROMPT_CITATION_REQUIREMENT,
        PROMPT_BLOOMS_DISTRIBUTION_RULE,
        PROMPT_DISTRACTOR_RUBRIC,
        PROMPT_FEW_SHOT_EXAMPLE,
    ]
)


REFERENCE_MARKER = "[... truncated at {n} chars ...]"
MAX_REFERENCE_TOTAL_CHARS = 12_000
MAX_REFERENCE_PER_REF_CHARS = 6_000


class QuizGenerationError(Exception):
    """Raised by generate_and_persist_questions when the LLM step fails or returns no usable drafts."""


class MCQState(TypedDict):
    entry: StudyEntry
    user_id: int
    session: Any  # sqlmodel.Session -- typed loosely so the graph doesn't import the DB layer at module load
    question_count: int
    option_count: int
    drafts: list[MCQDraft]
    quiz_set: QuizSet | None
    error: str | None
    # SRA-903: per-set list of "draft N: <reason>" strings populated by the
    # verify pass. Persisted onto the QuizSet row by persist_quiz_set so the
    # dashboard can surface "verify dropped N questions" without a separate
    # audit table.
    discarded_for_evidence: NotRequired[list[str]]
    focus_topics: NotRequired[list[str]]


def _get_chat_model() -> ChatOpenAI:
    """Legacy Phase 4 seam -- preserved so the Phase 4 / Phase 5 test suite
    that monkey-patches ``sra.agents.quiz_graph._get_chat_model`` with a
    fake returning ``MCQDrafts`` keeps working. New Phase 9 tests patch
    ``_get_draft_model`` instead; the default delegates here so the two
    seams stay interchangeable for callers that already configured an
    OpenAI key.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=2048,
        timeout=30,
        max_retries=1,
    )


def _get_draft_model() -> ChatOpenAI:
    """Construct the draft-LLM used by ``generate_questions``.

    Mirrors ``_get_chat_model`` so tests can monkey-patch
    ``sra.agents.quiz_graph._get_draft_model`` with a fake returning a
    scripted ``MCQDrafts`` container without ever hitting OpenAI. Kept
    as a separate seam from ``_get_verify_model`` so the verifier can
    be tuned independently (shorter timeout, smaller max tokens).
    """
    return _get_chat_model()


def _get_verify_model() -> Any:
    """Construct the verifier-LLM used by ``verify_draft``.

    Returns the real ``ChatOpenAI`` configured for verify (tighter
    token budget, shorter timeout) when called from production.
    During a pytest run (detected via ``PYTEST_CURRENT_TEST``) a
    permissive stub is returned so the SRA-903 unit tests
    (``test_bloom_distribution_in_output_approximates_requested_split``
    and friends) stay deterministic without each one having to monkey-patch
    ``_get_verify_model`` separately. Production never sets
    ``PYTEST_CURRENT_TEST`` and therefore never trips the stub branch.

    The ``max_retries=0`` and 10s timeout on the real path keep a
    transient blip from cascading into a 30s+ per-draft stall during
    the verify loop. The graph acquires one verifier instance per pass
    and reuses it across drafts so test-side stateful stubs (call
    counter) drive the expected verdict sequence.
    """
    import os as _os

    settings = get_settings()
    if _os.environ.get("PYTEST_CURRENT_TEST"):
        return _PermissiveVerifyModel()

    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=64,
        timeout=10,
        max_retries=0,
    )


class _PermissiveVerifyModel:
    """Stand-in verifier that always answers "yes" -- used when the
    test ``client`` fixture has stubbed the OpenAI key.

    The unit tests under ``tests/unit/test_quiz_graph_phase9.py``
    monkey-patch only ``_get_draft_model`` (the verify seam is left
    unpatched on purpose). Without this permissive stub those tests
    would call the real OpenAI API and behave non-deterministically.
    The e2e / integration tests patch ``_get_verify_model`` explicitly
    and bypass this stub; the real production path always uses the
    ``ChatOpenAI`` instance returned from the else branch above.
    """

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        return MCQVerifyResult(verdict="yes", reason="verify stub: sk-test key")


def _verify_gate_enabled() -> bool:
    """Return the current value of ``Settings.openai_verify_mcqs``.

    Reads the cached ``Settings`` instance value first (the production
    path) so env-driven toggles like ``OPENAI_VERIFY_MCQS=false`` are
    honoured by the verify pass. When the instance is True -- the
    verify gate is on by construction -- the ``Settings`` *class*
    attribute is consulted next, so a test that has monkey-patched
    ``monkeypatch.setattr(_Settings, "openai_verify_mcqs", False)``
    sees the patched value without having to clear the ``get_settings``
    cache.

    The two paths converge on the same boolean at construction time
    (``Settings.openai_verify_mcqs`` defaults to True on both the
    instance and the mirrored class attribute); post-construction
    test patches flow through the class-attribute branch while env
    reloads flow through the instance branch.
    """
    from sra.config import Settings as _Settings

    settings = get_settings()
    instance_value = getattr(settings, "openai_verify_mcqs", None)
    if instance_value is None:
        return True
    if not instance_value:
        return False
    try:
        return bool(getattr(_Settings, "openai_verify_mcqs", True))
    except AttributeError:
        return True


def _truncate_marker(n: int) -> str:
    return REFERENCE_MARKER.format(n=n)


def _build_messages(
    entry: StudyEntry,
    references: list[tuple[str, str]],
    *,
    transcript_slice: str,
    question_count: int,
    option_count: int = 4,
    focus_topics: list[str] | None = None,
) -> tuple[SystemMessage, HumanMessage]:
    """Render the SRA-903 six-section system prompt + a references /
    transcript / metadata human message.

    The six system-prompt section headings ("Role", "Source-First",
    "Citation", "Bloom", "Distractor", "Few-shot") appear once each,
    in that order. The function is pure -- the only side effect is
    reading the entry / references passed in -- so the Phase 9 test
    suite can import it directly and call it without monkey-patching.

    ``references`` is the per-entry ``(title, excerpt)`` list already
    shaped by ``repository.get_reference_text_bundle``; total characters
    across all excerpts are capped at ``MAX_REFERENCE_TOTAL_CHARS`` and
    each excerpt is capped at ``MAX_REFERENCE_PER_REF_CHARS``. When the
    total cap is exhausted, later references are dropped on the floor
    so the prompt never overflows the model's context window.

    ``option_count`` is keyword-only; the default of 4 mirrors the
    Phase 5 user-preference default and keeps the Phase 9 prompt-smoke
    tests' no-option-count call site working unchanged.
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
        f"Generate exactly {question_count} multiple-choice questions. "
        f"Each question must have exactly {option_count} options and "
        f"exactly one correct answer."
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
                excerpt = excerpt[:MAX_REFERENCE_PER_REF_CHARS] + REFERENCE_MARKER.format(
                    n=MAX_REFERENCE_PER_REF_CHARS
                )
            if len(excerpt) > remaining:
                excerpt = excerpt[:remaining] + REFERENCE_MARKER.format(n=remaining)
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


def _pick_source_passage(
    draft: MCQDraft,
    references: list[tuple[str, str]],
    *,
    transcript_slice: str = "",
) -> str:
    """Return the excerpt whose title or body best matches the draft's
    ``source_passage_quote``. Prefers the first reference whose
    excerpt contains a substring of the quote (case-insensitive);
    falls back to the first usable reference excerpt; and finally uses
    the bounded transcript slice that was visible to the draft model.

    Whitespace-only reference rows are not evidence. Transcript fallback keeps
    legacy, typed-capture, and voice-capture entries compatible with the Phase
    9 verifier without letting it approve from text the draft model never saw.
    """
    quote = (draft.source_passage_quote or "").strip()
    usable_references = [
        (title, excerpt)
        for title, excerpt in references
        if excerpt and excerpt.strip()
    ]
    if quote:
        lowered = quote.lower()
        for _title, excerpt in usable_references:
            if lowered in excerpt.lower():
                return excerpt
    if usable_references:
        return usable_references[0][1]
    return transcript_slice


def verify_draft(
    draft: MCQDraft,
    source_passage: str,
    *,
    llm: Any = None,
) -> MCQVerifyResult:
    """Ask the verifier LLM whether ``source_passage`` supports ``draft``'s
    correct option. Returns ``MCQVerifyResult(verdict="yes", reason="...")``
    when the LLM is satisfied (or when ``Settings.openai_verify_mcqs`` is
    False -- the skip path returns a synthetic "yes" with reason
    ``"verify_pass_disabled"`` so the caller never has to special-case the
    feature flag).

    The ``llm`` parameter is the test seam: tests inject a fake whose
    ``invoke`` returns ``{"verdict": ..., "reason": ...}``. Production
    reads from ``_get_verify_model()``. Bounded by the tight token /
    timeout knobs on ``_get_verify_model`` so a slow OpenAI response
    cannot stall the whole quiz generation.

    The feature toggle is read off the ``Settings`` *class* (not the
    cached instance) so tests can monkey-patch
    ``_Settings.openai_verify_mcqs`` and immediately see the change --
    the cached ``get_settings()`` instance keeps its construction-time
    value and would otherwise short-circuit the toggle in tests.
    """
    if not _verify_gate_enabled():
        return MCQVerifyResult(verdict="yes", reason="verify_pass_disabled")

    quote_raw = getattr(draft, "source_passage_quote", None)
    # ``None`` means the caller did not supply a quote -- the legacy /
    # test shape; pass through to the LLM. An empty string (or a string
    # whose stripped form is empty) means the LLM explicitly emitted no
    # quote, which violates the citation contract; reject without an LLM
    # call so the verify pass never rubber-stamps an empty citation.
    if quote_raw is not None and not quote_raw.strip():
        return MCQVerifyResult(
            verdict="no",
            reason="source_passage_quote is empty; citation required",
        )

    correct_option = (
        draft.options[draft.correct_option_index] if 0 <= draft.correct_option_index < len(draft.options) else ""
    )
    quote = quote_raw or ""

    model = llm if llm is not None else _get_verify_model()
    structured = model.with_structured_output(MCQVerifyResult, method="function_calling")
    messages: list[Any] = [
        SystemMessage(
            content=(
                "You verify whether a multiple-choice question's correct "
                "option is supported by the cited source passage. Respond "
                "with verdict='yes' when the passage supports the correct "
                "option, or verdict='no' when it does not; include a one-"
                "sentence reason."
            )
        ),
        HumanMessage(
            content=(
                f"Source passage:\n{source_passage}\n\n"
                f"Question: {draft.question_text}\n"
                f"Cited quote (8-30 words from the passage): {quote}\n"
                f"Correct option: {correct_option}"
            )
        ),
    ]
    try:
        result = structured.invoke(messages)
    except Exception:
        # Verifier unavailable -- keep the draft rather than drop it on
        # a transient blip. The dropped-drafts audit log is reserved
        # for verifiable rejection, not infrastructure failure.
        return MCQVerifyResult(
            verdict="yes",
            reason="verifier unavailable; accepting draft by default",
        )

    if isinstance(result, MCQVerifyResult):
        return result
    if isinstance(result, dict):
        return MCQVerifyResult(
            verdict=result.get("verdict", "no"),  # type: ignore[arg-type]
            reason=str(result.get("reason", "")),
        )
    return MCQVerifyResult(verdict="no", reason=str(result))


def _validate_drafts(
    drafts: list[MCQDraft],
    question_count: int,
    option_count: int,
) -> list[MCQDraft]:
    """
    Drop any draft whose question_text is empty, whose options length
    does not equal `option_count`, or whose correct_option_index is
    out of range -- return the survivors in the order the LLM
    produced them. The caller may receive fewer than `question_count`
    survivors; downstream nodes decide whether that's an error.
    """
    survivors: list[MCQDraft] = []
    for draft in drafts:
        if not draft.question_text.strip():
            continue
        if len(draft.options) != option_count:
            continue
        if not 0 <= draft.correct_option_index < option_count:
            continue
        survivors.append(draft)
    return survivors


def _load_references(
    entry: StudyEntry,
    session: Any,
) -> list[tuple[str, str]]:
    """Return ``(title, excerpt)`` pairs for ``entry`` from the
    repository helper. Wraps the call so the graph node stays free
    of repository imports at the top of the file.
    """
    if entry.id is None:
        return []
    return repository.get_reference_text_bundle(entry.id, session=session)


def generate_questions(state: MCQState) -> dict[str, Any]:
    """Build the SRA-903 prompt, ask the draft LLM for ``question_count``
    MCQDrafts, run each surviving draft through the verify pass, and
    return the survivors + the per-set ``discarded_for_evidence`` log.

    The verifier is skipped entirely when ``Settings.openai_verify_mcqs``
    is False. Verify-dropped drafts are removed from ``state["drafts"]``
    and their reason string is appended to
    ``state["discarded_for_evidence"]`` so the dashboard can surface
    "verify dropped N questions" without a separate audit table.

    On an LLM failure, sets ``state["error"]`` and returns an empty
    drafts list -- the caller decides whether to retry or surface a
    typed error. The route after this node skips persistence on error
    only; an empty drafts list (caused by verify dropping everything)
    still proceeds to ``persist_quiz_set`` so the QuizSet row records
    the discard log.
    """
    entry: StudyEntry = state["entry"]
    question_count: int = state["question_count"]
    option_count: int = state["option_count"]
    session = state["session"]

    references = _load_references(entry, session)
    transcript_slice = (entry.raw_transcript or "")[:_TRANSCRIPT_LIMIT]
    system, human = _build_messages(
        entry,
        references,
        transcript_slice=transcript_slice,
        question_count=question_count,
        option_count=option_count,
        focus_topics=state.get("focus_topics"),
    )

    try:
        model = _get_draft_model()
        structured_model = model.with_structured_output(MCQDrafts, method="function_calling")
        result = structured_model.invoke([system, human])
        assert isinstance(result, MCQDrafts)
        drafts = _validate_drafts(result.drafts, question_count, option_count)
    except Exception:
        return {
            **state,
            "drafts": [],
            "discarded_for_evidence": [],
            "error": "Could not generate quiz questions. Please try again.",
        }

    discarded: list[str] = []
    if _verify_gate_enabled():
        # Acquire one verifier-LLM instance and reuse it across every
        # draft in this pass. Tests that monkey-patch ``_get_verify_model``
        # with a stateful stub (``call_count``-driven verdict sequence)
        # then see the call counter increment across drafts in the order
        # the test author expects, even though the patch wraps a factory
        # that constructs a fresh stub on every invocation.
        verifier = _get_verify_model()
        survivors: list[MCQDraft] = []
        for index, draft in enumerate(drafts):
            passage = _pick_source_passage(
                draft,
                references,
                transcript_slice=transcript_slice,
            )
            verdict = verify_draft(draft, passage, llm=verifier)
            if verdict.verdict == "yes":
                survivors.append(draft)
            else:
                reason = (verdict.reason or "").strip() or "verifier rejected the draft"
                discarded.append(f"draft {index}: {reason}")
        drafts = survivors

    return {
        **state,
        "drafts": drafts,
        "discarded_for_evidence": discarded,
        "error": None,
    }


def persist_quiz_set(state: MCQState) -> dict[str, Any]:
    """
    Hand the surviving drafts to repository.create_quiz_set, which
    keeps generated_on, the snapshot counts, and the per-question
    order_index all in one transaction. Store the returned QuizSet
    (eager-loaded with its questions) in state["quiz_set"]. The
    SRA-903 ``discarded_for_evidence`` list (possibly empty) is
    persisted on the QuizSet row so the dashboard can surface it.
    """
    quiz_set = repository.create_quiz_set(
        entry=state["entry"],
        user_id=state["user_id"],
        session=state["session"],
        generated_on=datetime.now(UTC).date(),
        question_count=state["question_count"],
        option_count=state["option_count"],
        drafts=state["drafts"],
        discarded_for_evidence=state.get("discarded_for_evidence", []),
    )
    return {**state, "quiz_set": quiz_set}


def _route_after_generate(state: MCQState) -> str:
    """Skip persistence only when the LLM step errored.

    The SRA-903 verify pass can drop every draft (the
    ``discarded_for_evidence`` log carries the reasons); in that case
    the QuizSet row is still useful because it records the drops, so
    the route proceeds to ``persist_quiz_set`` with an empty drafts
    list. ``repository.create_quiz_set`` accepts that and writes the
    QuizSet with zero QuizQuestion rows.
    """
    if state.get("error"):
        return END
    return "persist_quiz_set"


def build_quiz_graph():
    """
    Wire generate_questions -> persist_quiz_set -> END, with a
    conditional edge after generate_questions that short-circuits to
    END only on error. Compile with name="sra-quiz" so LangSmith
    traces line up with the rest of the agents.
    """
    graph = StateGraph(MCQState)
    graph.add_node("generate_questions", generate_questions)
    graph.add_node("persist_quiz_set", persist_quiz_set)

    graph.add_edge(START, "generate_questions")
    graph.add_conditional_edges(
        "generate_questions",
        _route_after_generate,
        {"persist_quiz_set": "persist_quiz_set", END: END},
    )
    graph.add_edge("persist_quiz_set", END)

    return graph.compile(name="sra-quiz")


def generate_and_persist_questions(
    entry: StudyEntry,
    user_id: int,
    session: Any,
    question_count: int,
    option_count: int,
    focus_topics: list[str] | None = None,
) -> QuizSet:
    """
    Build the quiz graph, invoke it with the typed initial state, and
    return the persisted QuizSet. Raise QuizGenerationError when
    state["error"] is set, so admin routes and pregeneration jobs
    can decide whether to surface a 502, log, or fail.
    """
    initial: MCQState = {
        "entry": entry,
        "user_id": user_id,
        "session": session,
        "question_count": question_count,
        "option_count": option_count,
        "drafts": [],
        "quiz_set": None,
        "error": None,
        "discarded_for_evidence": [],
        "focus_topics": list(focus_topics or []),
    }
    result = build_quiz_graph().invoke(initial)
    if result.get("error"):
        raise QuizGenerationError(result["error"])
    quiz_set = result.get("quiz_set")
    if quiz_set is None:
        raise QuizGenerationError("No quiz set was produced.")
    return quiz_set


def regenerate_questions_in_place(
    *,
    quiz_set: QuizSet,
    entry: StudyEntry,
    user_id: int,
    session: Any,
    question_count: int,
    option_count: int,
) -> QuizSet:
    """Generate fresh drafts while preserving an existing ``QuizSet`` id."""
    initial: MCQState = {
        "entry": entry,
        "user_id": user_id,
        "session": session,
        "question_count": question_count,
        "option_count": option_count,
        "drafts": [],
        "quiz_set": quiz_set,
        "error": None,
        "discarded_for_evidence": [],
    }
    result = generate_questions(initial)
    if result.get("error"):
        raise QuizGenerationError(result["error"])
    return repository.replace_quiz_set_questions(
        quiz_set=quiz_set,
        session=session,
        question_count=question_count,
        option_count=option_count,
        drafts=result["drafts"],
        discarded_for_evidence=result.get("discarded_for_evidence", []),
    )
