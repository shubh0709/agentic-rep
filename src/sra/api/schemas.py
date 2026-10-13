"""API request/response DTOs. Concrete, not a learning stub."""

from datetime import date, datetime

from pydantic import BaseModel


class IngestRequest(BaseModel):
    transcript: str


class EntryOut(BaseModel):
    id: int
    topic: str
    subtopic: str | None
    tags: list[str]
    summary: str
    created_at: datetime


class ReviewOut(BaseModel):
    id: int
    entry: EntryOut
    interval_label: str
    scheduled_date: date
    status: str


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
