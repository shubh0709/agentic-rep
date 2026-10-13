"""Structured-output schemas for the LangGraph agents. Concrete, not a learning stub."""

from pydantic import BaseModel, Field


class StudyEntryDraft(BaseModel):
    """What the ingestion agent's LLM call is asked to produce per topic it finds in a transcript."""

    topic: str
    subtopic: str | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str


class StudyEntryDrafts(BaseModel):
    """All topics extracted from one transcript, including an empty list when none are found."""

    drafts: list[StudyEntryDraft] = Field(description="One study entry per distinct topic in the transcript.")
