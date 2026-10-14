"""SRA-402 MCQ generation graph contracts."""

from datetime import UTC, datetime

import pytest
from sqlmodel import select
from sra.agents.quiz_graph import (
    MCQState,
    build_quiz_graph,
    generate_and_persist_questions,
    generate_questions,
    persist_quiz_set,
)

from sra.agents.schemas import MCQDraft, MCQDrafts
from sra.db.models import QuizQuestion, QuizSet, StudyEntry

pytestmark = pytest.mark.phase4


class FakeStructuredChatModel:
    """A stand-in for ChatOpenAI that returns an MCQDrafts on invoke."""

    def __init__(self, drafts: list[MCQDraft] | None = None, raise_error: bool = False):
        self._drafts = drafts or []
        self._raise_error = raise_error
        self.seen_messages: list = []

    def with_structured_output(self, schema, **kwargs):
        assert schema is MCQDrafts
        return self

    def invoke(self, messages, **kwargs):
        self.seen_messages.extend(messages)
        if self._raise_error:
            raise RuntimeError("simulated LLM failure")
        return MCQDrafts(drafts=list(self._drafts))


def _make_entry(session, seeded_user) -> StudyEntry:
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Photosynthesis",
        summary="Light and dark reactions in chloroplasts.",
        tags=["biology", "plants"],
        raw_transcript="today I studied photosynthesis",
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def test_generate_questions_includes_topic_and_summary_in_prompt(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    fake = FakeStructuredChatModel(
        drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
    )
    monkeypatch.setattr("sra.agents.quiz_graph._get_chat_model", lambda: fake)

    state: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 1,
        "option_count": 2,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }

    result = generate_questions(state)

    assert result["error"] is None
    assert len(fake.seen_messages) >= 1
    prompt_text = " ".join(
        getattr(message, "content", "") if not isinstance(message, tuple) else message[1]
        for message in fake.seen_messages
    )
    assert entry.topic in prompt_text
    assert entry.summary in prompt_text


def test_generate_questions_returns_expected_drafts(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    drafts_in = [
        MCQDraft(question_text="q1", options=["a", "b", "c"], correct_option_index=0, explanation=""),
        MCQDraft(question_text="q2", options=["a", "b", "c"], correct_option_index=2, explanation=""),
        MCQDraft(question_text="q3", options=["a", "b", "c"], correct_option_index=1, explanation=""),
    ]
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(drafts=drafts_in),
    )

    state: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 3,
        "option_count": 3,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }

    result = generate_questions(state)

    assert result["error"] is None
    assert len(result["drafts"]) == 3
    for draft in result["drafts"]:
        assert len(draft.options) == 3
        assert 0 <= draft.correct_option_index < 3


def test_generate_questions_drops_drafts_with_out_of_range_index(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    drafts_in = [
        MCQDraft(question_text="valid", options=["a", "b"], correct_option_index=0, explanation=""),
        MCQDraft(question_text="bad-index", options=["a", "b"], correct_option_index=5, explanation=""),
        MCQDraft(question_text="bad-len", options=["a", "b", "c"], correct_option_index=0, explanation=""),
    ]
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(drafts=drafts_in),
    )

    state: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 3,
        "option_count": 2,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }

    result = generate_questions(state)

    assert len(result["drafts"]) == 1
    assert result["drafts"][0].question_text == "valid"
    assert result["error"] is None


def test_generate_questions_sets_error_on_llm_failure(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(raise_error=True),
    )

    state: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 2,
        "option_count": 3,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }

    result = generate_questions(state)

    assert result["error"] is not None
    assert result["drafts"] == []


def test_persist_quiz_set_snapshots_counts_and_persists_questions(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    drafts = [
        MCQDraft(question_text="q1", options=["a", "b", "c"], correct_option_index=0, explanation=""),
        MCQDraft(question_text="q2", options=["a", "b", "c"], correct_option_index=1, explanation=""),
    ]

    state: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 2,
        "option_count": 3,
        "drafts": drafts,
        "quiz_set": None,
        "error": None,
    }

    result = persist_quiz_set(state)

    quiz_set = result["quiz_set"]
    assert quiz_set is not None
    assert quiz_set.id is not None
    assert quiz_set.question_count == 2
    assert quiz_set.option_count == 3
    assert quiz_set.entry_id == entry.id
    assert quiz_set.user_id == seeded_user.id
    assert quiz_set.generated_on == datetime.now(UTC).date()

    rows = session.exec(select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)).all()
    assert len(rows) == 2
    for index, row in enumerate(rows):
        assert row.order_index == index
        assert len(row.options) == 3


def test_build_quiz_graph_compiles_and_persists(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(
            drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
        ),
    )

    graph = build_quiz_graph()

    initial: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 1,
        "option_count": 2,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }
    result = graph.invoke(initial)

    assert result["quiz_set"] is not None
    assert result["quiz_set"].entry_id == entry.id
    assert session.exec(select(QuizSet).where(QuizSet.entry_id == entry.id)).all() != []


def test_build_quiz_graph_persists_error_path_without_raising(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(raise_error=True),
    )

    graph = build_quiz_graph()

    initial: MCQState = {
        "entry": entry,
        "user_id": seeded_user.id,
        "session": session,
        "question_count": 1,
        "option_count": 2,
        "drafts": [],
        "quiz_set": None,
        "error": None,
    }

    result = graph.invoke(initial)

    assert result["error"] is not None
    assert session.exec(select(QuizSet).where(QuizSet.entry_id == entry.id)).all() == []


def test_generate_and_persist_questions_returns_quiz_set(monkeypatch, session, seeded_user):
    entry = _make_entry(session, seeded_user)
    monkeypatch.setattr(
        "sra.agents.quiz_graph._get_chat_model",
        lambda: FakeStructuredChatModel(
            drafts=[MCQDraft(question_text="q", options=["a", "b"], correct_option_index=0, explanation="")]
        ),
    )

    quiz_set = generate_and_persist_questions(
        entry=entry,
        user_id=seeded_user.id,
        session=session,
        question_count=1,
        option_count=2,
    )

    assert quiz_set.entry_id == entry.id
    assert quiz_set.user_id == seeded_user.id
    assert quiz_set.question_count == 1
    assert quiz_set.option_count == 2