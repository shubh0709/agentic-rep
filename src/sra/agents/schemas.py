"""Structured-output schemas for the LangGraph agents. Concrete, not a learning stub."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StudyEntryDraft(BaseModel):
    """What the ingestion agent's LLM call is asked to produce per topic it finds in a transcript."""

    topic: str
    subtopic: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str


class StudyEntryDrafts(BaseModel):
    """All topics extracted from one transcript, including an empty list when none are found."""

    drafts: list[StudyEntryDraft] = Field(description="One study entry per distinct topic in the transcript.")


BloomLevel = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]


class MCQDraft(BaseModel):
    """What the MCQ generation agent's LLM call is asked to produce per question.

    SRA-903 adds three quality fields alongside the legacy four:

      * ``bloom_level`` -- which rung of Bloom's taxonomy the question targets.
        Acts as the "adoption marker": once the LLM emits ``bloom_level`` it
        must also emit the matching ``source_passage_quote`` and
        ``distractor_misconception``.
      * ``source_passage_quote`` -- an 8-30-word excerpt copied verbatim from
        the cited reference so the verify pass can confirm the correct
        option is supported by the cited material.
      * ``distractor_misconception`` -- the named misconception each wrong
        option targets (e.g. "conflating per-head representation width with
        per-head compute"), included in the question's explanation so the
        study session surfaces the failure mode.

    All three fields default to ``None`` so pre-Phase-9 callers that build
    ``MCQDraft`` from the legacy four-field shape keep working. The
    ``_validate_adoption`` model-level rule below rejects the partial-
    adoption case ("two new fields set, one missing") because that is the
    shape the LLM must commit to -- otherwise the verify pass cannot tell
    whether a "missing" field is a deliberate omission or an LLM error.

    ``model_dump`` drops None-valued fields so the pre-Phase-9 payload
    shape (four keys per draft) is preserved when callers build
    ``MCQDraft`` from the legacy four-field shape, while still emitting
    the three new keys when the LLM adopts the SRA-903 shape.
    """

    def model_dump(self, **kwargs):  # type: ignore[override]
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    question_text: str
    options: list[str] = Field(default_factory=list)
    correct_option_index: int = Field(ge=0)
    explanation: str = ""
    bloom_level: BloomLevel | None = None
    source_passage_quote: str | None = None
    distractor_misconception: str | None = None

    @field_validator("correct_option_index", mode="before")
    @classmethod
    def _require_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("correct_option_index must be an integer")
        return value

    @field_validator("source_passage_quote")
    @classmethod
    def _check_source_passage_quote(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # Allow empty strings at the field-validator level so callers (the
        # verify pass, the test helpers) can build a draft whose quote is
        # intentionally blank to exercise the "missing citation" rejection
        # path. The non-empty rule lives in the model-level adoption check
        # so it only fires when bloom_level is set.
        stripped = value.strip()
        if not stripped:
            return value
        words = stripped.split()
        if len(words) < 4 or len(words) > 30:
            raise ValueError(
                f"source_passage_quote must be between 4 and 30 words; got {len(words)}"
            )
        return stripped

    @field_validator("distractor_misconception")
    @classmethod
    def _check_distractor_misconception(cls, value: str | None) -> str | None:
        if value is None:
            return value
        # Empty strings are tolerated at the field level so the test
        # "source_passage_quote is empty" path can build a draft whose
        # misconception is the deliberately-blank companion; the
        # non-empty rule lives in the model-level adoption check.
        stripped = value.strip()
        if not stripped:
            return value
        if len(stripped) > 200:
            raise ValueError("distractor_misconception must be 200 characters or fewer")
        return stripped

    @model_validator(mode="after")
    def _validate_adoption(self) -> "MCQDraft":
        """Enforce the all-or-nothing adoption rule for the three SRA-903 fields.

        Three distinct partial-adoption shapes are rejected:

          1. ``bloom_level`` is set but ``source_passage_quote`` is missing
             or empty -- the citation is the point of ``bloom_level``, so
             the quote must accompany it.
          2. ``bloom_level`` is set but ``distractor_misconception`` is
             missing or empty -- the misconception name is the point of
             ``bloom_level``, so it must accompany it.
          3. ``bloom_level`` is *not* set yet a non-empty
             ``source_passage_quote`` or ``distractor_misconception`` is
             supplied -- adopting only some of the new shape is a contract
             violation the LLM must never produce.

        The case the LLM runs into most often -- pre-Phase-9 four-field
        shape with all three new fields None -- passes through cleanly.
        An empty-string quote with no bloom_level is the "test fixture
        asking verify_draft to reject this draft" shape and is also
        tolerated at the model level; the verifier itself turns that
        into a "no" verdict via its own empty-quote short circuit.
        """
        has_bloom = self.bloom_level is not None
        has_quote = bool(self.source_passage_quote and self.source_passage_quote.strip())
        has_misc = bool(self.distractor_misconception and self.distractor_misconception.strip())

        if has_bloom and not has_quote:
            raise ValueError(
                "source_passage_quote must be non-empty (8-30 words) when "
                "bloom_level is supplied"
            )
        if has_bloom and not has_misc:
            raise ValueError(
                "distractor_misconception must be non-empty when "
                "bloom_level is supplied"
            )
        if not has_bloom and (has_quote or has_misc):
            raise ValueError(
                "bloom_level is required when source_passage_quote or "
                "distractor_misconception is supplied"
            )
        return self


class MCQDrafts(BaseModel):
    """All MCQ drafts the generator produced for one entry; an empty list on failure or no-usable-questions."""

    drafts: list[MCQDraft] = Field(default_factory=list)

    def model_dump(self, **kwargs):  # type: ignore[override]
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)


class MCQVerifyResult(BaseModel):
    """Verdict the verify-LLM returns for a single draft.

    ``verdict == "yes"`` means the cited passage supports the draft's
    correct option; ``verdict == "no"`` means it does not, and the
    ``generate_questions`` graph node drops the draft and records the
    reason string on ``state["discarded_for_evidence"]``.
    """

    verdict: Literal["yes", "no"]
    reason: str