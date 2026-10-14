"""SRA-504 — Voice quiz conversation graph: per-node and end-to-end."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

from datetime import date
from typing import Any

import pytest
from sqlmodel import select

from sra.agents.schemas import MCQDraft
from sra.db.models import (
    QuizAnswer,
    QuizAttempt,
    QuizQuestion,
    QuizSet,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase5


@pytest.fixture
def quiz_conversation_graph():
    from sra.agents import quiz_conversation_graph as _qcg

    return _qcg


class _FakeChatModel:
    """Stub LLM that returns a scripted interpretation for every call."""

    def __init__(self, scripted_indices: list[int], error_message: str | None = None):
        self._scripted = list(scripted_indices)
        self._error_message = error_message
        self.calls: list[Any] = []

    def with_structured_output(self, schema, **kwargs):
        assert schema is quiz_conversation_graph.InterpretedAnswer
        return self

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        if self._error_message is not None:
            raise RuntimeError(self._error_message)
        if not self._scripted:
            raise AssertionError("no scripted interpretation left")
        return quiz_conversation_graph.InterpretedAnswer(selected_option_index=self._scripted.pop(0))


class _FakeStructuredChatModelBad:
    """Stub LLM that returns an invalid (out-of-range) index."""

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages, **kwargs):
        return quiz_conversation_graph.InterpretedAnswer(selected_option_index=99)


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int, drafts: list[MCQDraft] | None = None) -> QuizSet:
    entry = _make_entry(session, user_id)
    drafts = drafts or [
        MCQDraft(question_text="q0", options=["a", "b", "c", "d"], correct_option_index=0, explanation="ok"),
        MCQDraft(question_text="q1", options=["a", "b", "c", "d"], correct_option_index=1, explanation="ok"),
    ]
    return create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=4,
        drafts=drafts,
    )


def _start_voice_attempt(session, user_id: int, quiz_set: QuizSet) -> QuizAttempt:
    attempt = QuizAttempt(
        user_id=user_id,
        quiz_set_id=quiz_set.id if quiz_set.id is not None else 0,
        mode="voice",
        total_questions=quiz_set.question_count,
        cursor=0,
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return attempt


def test_interpret_answer_returns_scripted_index(monkeypatch, session, seeded_user, quiz_conversation_graph):
    fake = _FakeChatModel([2])
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: fake)

    idx = quiz_conversation_graph._interpret_answer("the third one", ["a", "b", "c", "d"], session=session)
    assert idx == 2


def test_interpret_answer_raises_typed_error_on_out_of_range_index(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: _FakeStructuredChatModelBad())

    with pytest.raises(quiz_conversation_graph.QuizVoiceInterpretationError):
        quiz_conversation_graph._interpret_answer("nonsense", ["a", "b"], session=session)


def test_grade_answer_marks_correct_by_equality_with_canonical_correct_index(
    session, seeded_user, quiz_conversation_graph
):
    question = QuizQuestion(
        quiz_set_id=1,
        question_text="q",
        options=["a", "b", "c", "d"],
        correct_option_index=1,
        order_index=0,
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    attempt = _start_voice_attempt(session, seeded_user.id, _make_quiz_set(session, seeded_user.id))

    state: dict = {
        "attempt_id": attempt.id if attempt.id is not None else 0,
        "question_index": 0,
        "score": 0,
        "last_answer_text": "the second one",
        "finished": False,
        "selected_option_index": 1,
    }

    out = quiz_conversation_graph.grade_answer(state)
    assert out["is_correct"] is True
    assert out["score"] == 1


def test_grade_answer_marks_incorrect_when_index_mismatches(
    session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    question = session.exec(
        select(QuizQuestion).where(QuizQuestion.quiz_set_id == quiz_set.id)
    ).first()
    assert question is not None
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)

    state: dict = {
        "attempt_id": attempt.id if attempt.id is not None else 0,
        "question_index": 0,
        "score": 0,
        "last_answer_text": "wrong",
        "finished": False,
        "selected_option_index": 0,
    }

    out = quiz_conversation_graph.grade_answer(state)
    assert out["is_correct"] is False
    assert out["score"] == 0


def test_ask_question_loads_current_question_text(session, seeded_user, quiz_conversation_graph):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    state: dict = {
        "attempt_id": attempt.id if attempt.id is not None else 0,
        "question_index": 0,
        "score": 0,
        "last_answer_text": None,
        "finished": False,
        "selected_option_index": None,
    }

    out = quiz_conversation_graph.ask_question(state, session=session)
    assert out["question_text"] == "q0"
    assert out["options"] == ["a", "b", "c", "d"]


def test_next_or_finish_advances_cursor_and_does_not_finish_before_last_question(
    session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    state: dict = {
        "attempt_id": attempt.id if attempt.id is not None else 0,
        "question_index": 0,
        "score": 0,
        "last_answer_text": None,
        "finished": False,
        "selected_option_index": None,
    }

    out = quiz_conversation_graph.next_or_finish(state, session=session)
    assert out["question_index"] == 1
    assert out["finished"] is False
    refreshed = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    assert refreshed.completed_at is None


def test_next_or_finish_completes_attempt_on_final_question(
    session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    state: dict = {
        "attempt_id": attempt.id if attempt.id is not None else 0,
        "question_index": quiz_set.question_count - 1,
        "score": 1,
        "last_answer_text": None,
        "finished": False,
        "selected_option_index": 0,
    }

    out = quiz_conversation_graph.next_or_finish(state, session=session)
    assert out["finished"] is True
    refreshed = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    assert refreshed.completed_at is not None
    assert refreshed.score == 1


def test_process_voice_turn_correct_answer_advances_and_persists(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: _FakeChatModel([0]))

    result = quiz_conversation_graph.process_voice_turn(
        session=session, attempt=attempt, answer_text="the first one"
    )

    assert result["is_correct"] is True
    assert result["finished"] is False
    assert result["next_question_text"] == "q1"
    assert result["score"] == 1
    answers = session.exec(select(QuizAnswer).where(QuizAnswer.attempt_id == attempt.id)).all()
    assert len(answers) == 1
    assert answers[0].is_correct is True


def test_process_voice_turn_wrong_answer_advances_without_incrementing_score(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: _FakeChatModel([1]))

    result = quiz_conversation_graph.process_voice_turn(
        session=session, attempt=attempt, answer_text="the second one"
    )

    assert result["is_correct"] is False
    assert result["score"] == 0
    answers = session.exec(select(QuizAnswer).where(QuizAnswer.attempt_id == attempt.id)).all()
    assert answers[0].is_correct is False


def test_process_voice_turn_ambiguous_answer_maps_to_zero_index(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: _FakeChatModel([0]))

    result = quiz_conversation_graph.process_voice_turn(
        session=session, attempt=attempt, answer_text="huh"
    )

    assert result["is_correct"] is True
    assert result["score"] == 1


def test_process_voice_turn_llm_interpretation_error_raises_typed_error(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(
        quiz_conversation_graph, "_get_chat_model",
        lambda: _FakeStructuredChatModelBad(),
    )

    with pytest.raises(quiz_conversation_graph.QuizVoiceInterpretationError):
        quiz_conversation_graph.process_voice_turn(
            session=session, attempt=attempt, answer_text="???", llm=None
        )


def test_process_voice_turn_last_question_completes_attempt(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""),
    ]
    quiz_set = _make_quiz_set(session, seeded_user.id, drafts)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    monkeypatch.setattr(quiz_conversation_graph, "_get_chat_model", lambda: _FakeChatModel([0]))

    result = quiz_conversation_graph.process_voice_turn(
        session=session, attempt=attempt, answer_text="the first one"
    )

    assert result["finished"] is True
    assert result["score"] == 1
    refreshed = session.exec(select(QuizAttempt).where(QuizAttempt.id == attempt.id)).one()
    assert refreshed.completed_at is not None
    assert refreshed.score == 1


def test_process_voice_turn_completed_attempt_raises_attempt_already_completed(
    monkeypatch, session, seeded_user, quiz_conversation_graph
):
    quiz_set = _make_quiz_set(session, seeded_user.id)
    attempt = _start_voice_attempt(session, seeded_user.id, quiz_set)
    attempt.completed_at = attempt.started_at
    attempt.score = 1
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    with pytest.raises(quiz_conversation_graph.QuizAttemptAlreadyCompletedError):
        quiz_conversation_graph.process_voice_turn(
            session=session, attempt=attempt, answer_text="anything"
        )
