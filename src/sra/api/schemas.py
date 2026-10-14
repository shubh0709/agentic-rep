"""API request/response DTOs. Concrete, not a learning stub."""

from datetime import date, datetime
from typing import Literal

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


class QuizQuestionOut(BaseModel):
    id: int
    question_text: str
    options: list[str]
    order_index: int


class QuizSetOut(BaseModel):
    id: int
    entry: EntryOut
    generated_on: date
    questions: list[QuizQuestionOut]


class StartAttemptRequest(BaseModel):
    mode: Literal["web", "voice"]


class SubmitAnswerRequest(BaseModel):
    question_id: int
    selected_option_index: int


class AnswerResult(BaseModel):
    is_correct: bool
    correct_option_index: int
    explanation: str | None


class QuizAttemptOut(BaseModel):
    id: int
    quiz_set_id: int
    mode: str
    started_at: datetime
    completed_at: datetime | None
    score: int | None
    total_questions: int