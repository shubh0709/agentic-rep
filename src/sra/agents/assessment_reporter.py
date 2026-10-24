"""Deterministic, persistable assessment result aggregation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AssessmentResultItem(BaseModel):
    kind: Literal["mcq", "interview"]
    topic: str
    question: str
    answer: str
    expected_answer: str
    score: int = Field(ge=0, le=100)
    is_correct: bool
    feedback: str
    rationale: str
    evidence_excerpt: str


class AssessmentReport(BaseModel):
    version: int = 1
    mode: Literal["mcq", "interview", "both"]
    overall_score: int = Field(ge=0, le=100)
    items: list[AssessmentResultItem]
    strengths: list[str]
    weaknesses: list[str]
    deep_dive_topics: list[str]
    summary: str


def build_assessment_report(
    *, mode: Literal["mcq", "interview", "both"], items: list[AssessmentResultItem]
) -> AssessmentReport:
    """Aggregate already-persisted grading without another inference call."""
    overall = sum(item.score for item in items) // len(items) if items else 0
    strong_topics = list(dict.fromkeys(item.topic for item in items if item.score >= 70))
    weak_topics = list(dict.fromkeys(item.topic for item in items if item.score < 70))
    strengths = [f"{topic}: demonstrated a solid grounded understanding." for topic in strong_topics]
    weaknesses = [f"{topic}: needs a more complete or precise explanation." for topic in weak_topics]
    deep_dives = [f"Deep dive on {topic} using the cited source evidence." for topic in weak_topics]
    return AssessmentReport(
        mode=mode,
        overall_score=overall,
        items=items,
        strengths=strengths,
        weaknesses=weaknesses,
        deep_dive_topics=deep_dives,
        summary=(
            f"Completed {len(items)} graded item(s) with an overall score of {overall}. "
            f"Review {len(weak_topics)} weaker topic(s) next."
        ),
    )


__all__ = ["AssessmentReport", "AssessmentResultItem", "build_assessment_report"]
