"""Structured-output schemas for the LangGraph agents. Concrete, not a learning stub."""

from pydantic import BaseModel, Field, field_validator


class StudyEntryDraft(BaseModel):
    """What the ingestion agent's LLM call is asked to produce per topic it finds in a transcript."""

    topic: str
    subtopic: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str


class StudyEntryDrafts(BaseModel):
    """All topics extracted from one transcript, including an empty list when none are found."""

    drafts: list[StudyEntryDraft] = Field(description="One study entry per distinct topic in the transcript.")


class MCQDraft(BaseModel):
    """What the MCQ generation agent's LLM call is asked to produce per question."""

    question_text: str
    options: list[str] = Field(default_factory=list)
    correct_option_index: int = Field(ge=0)
    explanation: str = ""

    @field_validator("correct_option_index", mode="before")
    @classmethod
    def _require_int(cls, value: object) -> object:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("correct_option_index must be an integer")
        return value


class MCQDrafts(BaseModel):
    """All MCQ drafts the generator produced for one entry; an empty list on failure or no-usable-questions."""

    drafts: list[MCQDraft] = Field(default_factory=list)
