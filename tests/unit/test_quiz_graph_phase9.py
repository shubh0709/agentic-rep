# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — MCQ generation prompt uplift, verify pass, Bloom distribution.

Every test in this module is expected to fail today because the
``sra.agents.quiz_graph`` rewrite has not landed yet:

  * ``_build_messages`` does not accept ``references`` -- imports will
    raise ``ImportError`` or ``AttributeError``.
  * The system-prompt constants (``PROMPT_ROLE_PREFIX``,
    ``PROMPT_SOURCE_FIRST_RULE``, ``PROMPT_CITATION_REQUIREMENT``,
    ``PROMPT_BLOOMS_DISTRIBUTION_RULE``, ``PROMPT_DISTRACTOR_RUBRIC``,
    ``PROMPT_FEW_SHOT_EXAMPLE``, ``PROMPT_BLOOM_LEVELS``) are not yet
    declared on the module.
  * ``verify_draft`` does not exist; the second-pass verifier LLM seam
    is not wired.
  * ``Settings.openai_verify_mcqs`` is not declared, so the
    ``test_settings_*`` tests fail with ``AttributeError``.

The tests pin a *pure* prompt-renderer signature
(``_build_messages(entry, references, *, transcript_slice, question_count) -> tuple[SystemMessage, HumanMessage]``).
The truncation marker format -- ``[... truncated at N chars ...]`` --
matches the SRA-901 ``get_reference_text_bundle`` helper so the
implementer can use the helper's output as-is.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlmodel import select

from sra.config import get_settings

pytestmark = pytest.mark.phase9


# ---------------------------------------------------------------------------
# Constants the implementer is expected to declare on sra.agents.quiz_graph.
# The truncation marker format intentionally matches
# repository.get_reference_text_bundle so the implementer can use the helper
# output verbatim.
# ---------------------------------------------------------------------------

EXPECTED_SYSTEM_PROMPT_FRAGMENTS = {
    "PROMPT_ROLE_PREFIX": [
        "expert quiz author",
    ],
    "PROMPT_SOURCE_FIRST_RULE": [
        "supply"
    ],
    "PROMPT_CITATION_REQUIREMENT": [
        "source_passage_quote",
    ],
    "PROMPT_BLOOMS_DISTRIBUTION_RULE": [
        "bloom",
    ],
    "PROMPT_DISTRACTOR_RUBRIC": [
        "distractor",
        "misconception",
    ],
    "PROMPT_FEW_SHOT_EXAMPLE": [
        "source_passage_quote",
        "bloom_level",
        "distractor_misconception",
    ],
}

ALL_BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")


# ---------------------------------------------------------------------------
# Helpers and a tiny in-memory fake chat model used by every test.
# Mirrors the FakeStructuredChatModel idiom from tests/unit/test_quiz_graph.py
# so the LLM seam stays a single monkeypatched target.
# ---------------------------------------------------------------------------


def _import_quiz_graph() -> Any:
    from sra.agents import quiz_graph

    return quiz_graph


def _make_entry(session, seeded_user) -> Any:
    from sra.db.models import StudyEntry

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
    return entry


class _AlwaysYesVerifyModel:
    """Fake verify-LLM that always returns verdict='yes'."""

    def __init__(self) -> None:
        self.call_count = 0

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        self.call_count += 1
        return {"verdict": "yes", "reason": "supported"}


class _RecordingDraftModel:
    """Return one draft while retaining the exact prompt sent by the graph."""

    def __init__(self, draft) -> None:
        self._draft = draft
        self.seen_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        from sra.agents.schemas import MCQDrafts

        self.seen_messages.extend(messages)
        return MCQDrafts(drafts=[self._draft])


class _RecordingVerifyModel:
    """Accept drafts while retaining the source passage sent for verification."""

    def __init__(self) -> None:
        self.source_passages: list[str] = []

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        human = messages[-1].content
        prefix = "Source passage:\n"
        suffix = "\n\nQuestion:"
        assert human.startswith(prefix)
        self.source_passages.append(human[len(prefix) : human.index(suffix)])
        return {"verdict": "yes", "reason": "recorded"}


def _generate_with_recorded_evidence(
    monkeypatch, session, seeded_user, references, *, transcript=None
):
    """Drive the real graph node with only its two inference seams replaced."""
    entry = _make_entry(session, seeded_user)
    if transcript is not None:
        entry.raw_transcript = transcript
        session.add(entry)
        session.commit()
    draft = _make_draft(
        bloom_level="understand",
        source_passage_quote="spaced repetition strengthens memory by scheduling retrieval practice",
        distractor_misconception="Confusing retrieval practice with passive review",
    )
    draft_model = _RecordingDraftModel(draft)
    verify_model = _RecordingVerifyModel()
    monkeypatch.setattr(
        "sra.agents.quiz_graph._load_references", lambda entry, session: references
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: draft_model
    )
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_verify_model", lambda: verify_model
    )

    quiz_graph = _import_quiz_graph()
    result = quiz_graph.generate_questions(
        {
            "entry": entry,
            "user_id": seeded_user.id,
            "session": session,
            "question_count": 1,
            "option_count": 4,
            "drafts": [],
            "quiz_set": None,
            "error": None,
            "discarded_for_evidence": [],
        }
    )
    assert result["error"] is None
    return entry, draft_model, verify_model


# ---------------------------------------------------------------------------
# _build_messages — pure-function contract
# ---------------------------------------------------------------------------


def _build_messages(entry, references, *, transcript_slice, question_count):
    quiz_graph = _import_quiz_graph()
    return quiz_graph._build_messages(
        entry,
        references,
        transcript_slice=transcript_slice,
        question_count=question_count,
    )


def test_rendered_system_prompt_contains_role_prefix(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=4)
    assert "expert quiz author" in system.content.lower()


def test_rendered_system_prompt_contains_source_first_rule(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=4)
    # The source-first rule blocks external knowledge; tests pin a
    # distinctive phrase so a rewrite that drops the rule fails here.
    lowered = system.content.lower()
    assert "source" in lowered
    assert "external" in lowered or "no external" in lowered or "do not use" in lowered


def test_rendered_system_prompt_contains_citation_requirement(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=4)
    # The plan §E pins the literal field name "source_passage_quote" of 8-30
    # words; the prompt must surface it so the LLM emits it.
    assert "source_passage_quote" in system.content
    assert "8" in system.content and "30" in system.content


def test_rendered_system_prompt_contains_blooms_distribution_rule(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=10)
    # The Plan §E Bloom distribution rule spans 6 levels; the prompt text
    # must reference the "bloom" word and the concept of distribution.
    assert "bloom" in system.content.lower()
    assert "distribute" in system.content.lower() or "distribution" in system.content.lower()


def test_rendered_system_prompt_contains_distractor_rubric(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=4)
    lowered = system.content.lower()
    # Plan §E step 5 pins three properties: plausibility, named misconception,
    # length/format match. The tests cover the latter two distinctively.
    assert "distractor" in lowered
    assert "misconception" in lowered


def test_rendered_system_prompt_contains_few_shot_example(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=4)
    # A single high-quality exemplar must include the three new fields so
    # the LLM learns the shape by example. The exemplar must be structurally
    # complete (question_text + options + citation + bloom + misconception).
    content = system.content
    assert "source_passage_quote" in content
    assert "bloom_level" in content
    assert "distractor_misconception" in content
    assert "question_text" in content


def test_rendered_system_prompt_lists_bloom_levels(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    system, _ = _build_messages(entry, [], transcript_slice="", question_count=10)
    for level in ALL_BLOOM_LEVELS:
        assert level in system.content, f"missing Bloom level {level!r}"


# ---------------------------------------------------------------------------
# Human-message rendering
# ---------------------------------------------------------------------------


def test_rendered_human_message_includes_reference_excerpts(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    references = [("Lecture notes", "...scaled dot-product attention …")]
    _, human = _build_messages(
        entry, references, transcript_slice="", question_count=4
    )
    assert "Lecture notes" in human.content
    assert "scaled dot-product attention" in human.content


def test_rendered_human_message_truncates_per_reference_with_marker(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    long_text = "x" * 7000  # > max_chars_per_ref (6000)
    references = [("Lecture notes", long_text)]

    _, human = _build_messages(
        entry, references, transcript_slice="", question_count=4
    )

    # The marker format mirrors the SRA-901 repository helper.
    assert "[... truncated at 6000 chars ...]" in human.content


def test_rendered_human_message_totals_caps_at_max_chars_total(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    big_text_a = "a" * 11_000
    big_text_b = "b" * 11_000
    references = [("Ref A", big_text_a), ("Ref B", big_text_b)]

    _, human = _build_messages(
        entry, references, transcript_slice="", question_count=4
    )

    # The total has to fit within ~max_chars_total = 12 000; one of the
    # references will have been capped and must surface the marker.
    lowered = human.content
    assert (
        "[... truncated at 12000 chars ...]" in lowered
        or "[... truncated at 6000 chars ...]" in lowered
    )


def test_rendered_human_message_includes_original_transcript_slice_when_present(
    session, seeded_user
):
    entry = _make_entry(session, seeded_user)
    slice_text = "I originally heard the professor say multi-head attention …"
    _, human = _build_messages(
        entry, [], transcript_slice=slice_text, question_count=4
    )
    assert slice_text in human.content


def test_rendered_human_message_omits_transcript_when_absent(session, seeded_user):
    entry = _make_entry(session, seeded_user)
    _, human = _build_messages(entry, [], transcript_slice="", question_count=4)
    # A blank transcript must NOT render an empty "Transcript:" block that
    # confuses the LLM; the human-message text should be free of any
    # transcript placeholder.
    assert "transcript" not in human.content.lower() or "(none)" in human.content.lower() or human.content.strip() == "" or "no transcript" in human.content.lower()
    # But the references/metadata block must still appear.
    assert entry.topic in human.content


# ---------------------------------------------------------------------------
# SRA-908 verifier evidence selection
# ---------------------------------------------------------------------------


def test_reference_backed_entry_verifies_against_matching_reference(
    monkeypatch, session, seeded_user
):
    matching_excerpt = (
        "The notes explain that spaced repetition strengthens memory by "
        "scheduling retrieval practice across expanding intervals."
    )
    _entry, _draft_model, verify_model = _generate_with_recorded_evidence(
        monkeypatch,
        session,
        seeded_user,
        [
            ("Unrelated notes", "This passage discusses transformer attention."),
            ("Memory notes", matching_excerpt),
        ],
        transcript="transcript fallback must not replace a usable reference",
    )

    assert verify_model.source_passages == [matching_excerpt]


def test_no_usable_references_verifies_against_same_bounded_transcript_as_draft(
    monkeypatch, session, seeded_user
):
    transcript = (
        "spaced repetition strengthens memory by scheduling retrieval practice. "
        + "x" * 1_600
        + " verifier must never receive this suffix"
    )
    _entry, draft_model, verify_model = _generate_with_recorded_evidence(
        monkeypatch,
        session,
        seeded_user,
        [("Whitespace only", "   "), ("Empty", "")],
        transcript=transcript,
    )

    expected_slice = transcript[:1_500]
    draft_human_message = draft_model.seen_messages[-1].content
    assert verify_model.source_passages[-1] == expected_slice
    assert expected_slice in draft_human_message
    assert "verifier must never receive this suffix" not in draft_human_message


def test_blank_references_and_blank_transcript_keep_verifier_evidence_empty(
    monkeypatch, session, seeded_user
):
    _entry, _draft_model, verify_model = _generate_with_recorded_evidence(
        monkeypatch,
        session,
        seeded_user,
        [("Whitespace only", "   "), ("Empty", "")],
        transcript="",
    )

    assert verify_model.source_passages[-1] == ""


# ---------------------------------------------------------------------------
# verify_draft — pure function; returns MCQVerifyResult
# ---------------------------------------------------------------------------


def _make_draft(**overrides):
    from sra.agents.schemas import MCQDraft

    base = dict(
        question_text="What does d_k = d_model/h achieve?",
        options=[
            "Per-head compute stays constant as h grows.",
            "Each head sees the full d_model width.",
            "It matches the residual stream width.",
            "It is a theoretical optimum proved in Vaswani et al. 2017.",
        ],
        correct_option_index=0,
        explanation="Distractor targets the 'heads must each see d_model' confusion.",
    )
    base.update(overrides)
    return MCQDraft(**base)


def _verify_draft(draft, passage, *, llm=None):
    quiz_graph = _import_quiz_graph()
    return quiz_graph.verify_draft(draft, passage, llm=llm)


def test_verify_draft_returns_yes_when_correct_option_supported_by_passage(
    monkeypatch, session, seeded_user
):
    _make_entry(session, seeded_user)
    draft = _make_draft()
    passage = (
        "In scaled dot-product attention we project the queries and keys to "
        "d_k = d_model / h so each head computes its attention at a fraction "
        "of the cost and the total work stays roughly d_model^2."
    )

    class _YesModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return {"verdict": "yes", "reason": "the passage supports option 0"}

    result = _verify_draft(draft, passage, llm=_YesModel())

    assert result.verdict == "yes"


def test_verify_draft_returns_no_when_correct_option_unsupported_by_passage(
    monkeypatch, session, seeded_user
):
    """Planted-bad-MCQ test — the verifier must reject drafts whose correct
    option is not supported by the cited passage. Drives the dropped-from-
    state assertion that follows.
    """
    _make_entry(session, seeded_user)
    draft = _make_draft(
        question_text="Why does the optimizer use a learning-rate warmup?",
        options=[
            "Gravity pulls the loss surface toward the warmup point.",
            "It avoids exploding gradients in the early steps.",
            "It matches the residual stream width.",
            "It is required by SGD.",
        ],
        correct_option_index=0,
    )
    passage = (
        "We compute attention as softmax(QK^T / sqrt(d_k)) V. "
        "Multi-head attention concatenates h heads so the model can attend "
        "to information from different representation subspaces."
    )

    class _NoModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return {"verdict": "no", "reason": "passage says nothing about warmup"}

    result = _verify_draft(draft, passage, llm=_NoModel())
    assert result.verdict == "no"


def test_verify_draft_returns_no_when_source_passage_quote_missing(
    monkeypatch, session, seeded_user
):
    """Citation requirement: if the draft cannot cite a passage, the verifier rejects."""
    _make_entry(session, seeded_user)
    draft = _make_draft(source_passage_quote="")
    passage = (
        "In scaled dot-product attention we project to d_k = d_model / h so "
        "the per-head compute stays a fraction of d_model^2."
    )

    class _YesModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return {"verdict": "yes", "reason": "supported"}

    result = _verify_draft(draft, passage, llm=_YesModel())
    assert result.verdict == "no"


# ---------------------------------------------------------------------------
# Bloom distribution
# ---------------------------------------------------------------------------


def test_bloom_distribution_in_output_approximates_requested_split(
    monkeypatch, session, seeded_user
):
    """For 10 questions the prompt asks for 3 remember/understand, 4 apply,
    2 analyze, 1 evaluate/create. The accepted breakdown tolerates ±1 per level.
    """
    from sra.agents.schemas import MCQDraft, MCQDrafts

    entry = _make_entry(session, seeded_user)

    # Spread 10 drafts across the 6 levels using the requested split.
    distribution = (
        ["remember"] * 1
        + ["understand"] * 2
        + ["apply"] * 4
        + ["analyze"] * 2
        + ["evaluate"] * 1
    )
    drafts_in = [
        MCQDraft(
            question_text=f"q{i}-{lvl}",
            options=["a", "b", "c", "d"],
            correct_option_index=i % 4,
            explanation="",
            bloom_level=lvl,
            source_passage_quote="quote eight words long",
            distractor_misconception=f"target-{lvl}",
        )
        for i, lvl in enumerate(distribution)
    ]

    class _FakeModel:
        def __init__(self) -> None:
            self.seen_messages: list = []

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            self.seen_messages.extend(messages)
            return MCQDrafts(drafts=list(drafts_in))

    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_draft_model", lambda: _FakeModel()
    )

    quiz_graph = _import_quiz_graph()
    state: dict = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 10,
        "option_count": 4,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }
    result = quiz_graph.generate_questions(state)

    assert result.get("error") is None
    out = result.get("drafts") or []
    by_level: dict[str, int] = {}
    for draft in out:
        by_level[draft.bloom_level] = by_level.get(draft.bloom_level, 0) + 1

    # Strict ±1 per level. The split sums to 10 (3 + 4 + 2 + 1 = 10).
    assert by_level.get("remember", 0) in {0, 1, 2, 3}
    assert by_level.get("understand", 0) in {1, 2, 3, 4}
    assert by_level.get("apply", 0) in {3, 4, 5}
    assert by_level.get("analyze", 0) in {1, 2, 3}
    total_split = (
        by_level.get("evaluate", 0) + by_level.get("create", 0)
    )
    assert total_split in {1, 2}


# ---------------------------------------------------------------------------
# Settings — openai_verify_mcqs is a new gate the user can disable.
# ---------------------------------------------------------------------------


def test_settings_exposes_openai_verify_mcqs_default_true():
    settings = get_settings()
    assert settings.openai_verify_mcqs is True


def test_verify_pass_skipped_when_openai_verify_mcqs_false(
    monkeypatch, session, seeded_user
):
    """When the setting is False, the second-pass verify call is NOT invoked."""
    entry = _make_entry(session, seeded_user)
    from sra.agents.schemas import MCQDrafts

    drafts_in = [_make_draft()]

    class _DraftModel:
        def __init__(self):
            self.call_count = 0

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            self.call_count += 1
            return MCQDrafts(drafts=list(drafts_in))

    verify = _AlwaysYesVerifyModel()

    monkeypatch.setattr("sra.agents.quiz_graph._get_draft_model", lambda: _DraftModel())
    monkeypatch.setattr("sra.agents.quiz_graph._get_verify_model", lambda: verify)
    monkeypatch.setattr("sra.config.settings.openai_verify_mcqs", False, raising=False)
    # When Settings is a pydantic model, monkeypatch the attribute on the class.
    from sra.config import Settings as _Settings

    monkeypatch.setattr(_Settings, "openai_verify_mcqs", False)

    quiz_graph = _import_quiz_graph()
    state: dict = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 1,
        "option_count": 4,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }
    result = quiz_graph.generate_questions(state)

    assert result.get("error") is None
    assert verify.call_count == 0


# ---------------------------------------------------------------------------
# Discard transparency — QuizSet.discarded_for_evidence surfaces reasons
# ---------------------------------------------------------------------------


def test_discarded_for_evidence_persisted_on_quiz_set(
    monkeypatch, session, seeded_user
):
    from sra.agents.quiz_graph import generate_and_persist_questions
    from sra.agents.schemas import MCQDrafts

    entry = _make_entry(session, seeded_user)

    drafts = [_make_draft()]

    class _DraftModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return MCQDrafts(drafts=list(drafts))

    class _NoVerifyModel:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            return {"verdict": "no", "reason": "passage does not support option 0"}

    monkeypatch.setattr("sra.agents.quiz_graph._get_draft_model", lambda: _DraftModel())
    monkeypatch.setattr("sra.agents.quiz_graph._get_verify_model", lambda: _NoVerifyModel())

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=4,
    )

    # Either the QuizQuestion was dropped (zero rows) or the QuizSet
    # surfaces the discard reason.
    rows = session.exec(
        select(__import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion).where(
            __import__("sra.db.models", fromlist=["QuizQuestion"]).QuizQuestion.quiz_set_id
            == quiz_set.id
        )
    ).all()
    assert len(rows) == 0
    assert isinstance(getattr(quiz_set, "discarded_for_evidence", None), list)
    assert any("passage" in reason.lower() for reason in quiz_set.discarded_for_evidence)
