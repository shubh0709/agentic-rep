"""SRA-802 — ``generate_interview_outline`` contracts with a stubbed LLM.

The planner uses the same ``_get_chat_model`` seam as ``quiz_graph.py``
so tests can monkey-patch the model and exercise the validation /
error-containment paths without any live OpenAI call.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

from typing import Any

import pytest

from sra.db.models import (
    QuizSet,
    StudyEntry,
    StudyEntryCreate,
)
from sra.db.repository import create_quiz_set

pytestmark = pytest.mark.phase8


@pytest.fixture
def interview_planner():
    from sra.agents import interview_planner as _ip

    return _ip


class _FakeStructuredChatModel:
    """Stand-in for ChatOpenAI that returns a scripted outline container."""

    def __init__(
        self,
        scripted_items: list[dict] | None = None,
        *,
        raise_on_invoke: bool = False,
        return_non_list: bool = False,
    ) -> None:
        self._items = scripted_items or []
        self._raise = raise_on_invoke
        self._return_non_list = return_non_list
        self.seen_messages: list[Any] = []

    def with_structured_output(self, schema, **kwargs):
        from sra.agents import interview_planner as _ip

        assert schema is _ip.InterviewOutline
        return self

    def invoke(self, messages, **kwargs):
        from sra.agents import interview_planner as _ip

        self.seen_messages.extend(messages)
        if self._raise:
            raise RuntimeError("simulated LLM failure")
        if self._return_non_list:
            return {"items": "not a list"}
        return _ip.InterviewOutline(items=[_ip.InterviewOutlineItem(**i) for i in self._items])


def _make_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    return entry


def _make_quiz_set(session, user_id: int) -> QuizSet:
    from sra.agents.schemas import MCQDraft

    entry = _make_entry(session, user_id)
    drafts = [
        MCQDraft(question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""),
    ]
    qs = create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=__import__("datetime").date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )
    return qs


def test_generate_interview_outline_happy_path_returns_validated_outline(
    monkeypatch, session, seeded_user, interview_planner
):
    fake = _FakeStructuredChatModel(
        scripted_items=[
            {"subtopic": "intro", "difficulty": "easy", "focus_prompt": "what is X"},
            {"subtopic": "body", "difficulty": "medium", "focus_prompt": "how does Y work"},
            {"subtopic": "wrap", "difficulty": "hard", "focus_prompt": "compare Z to W"},
        ]
    )
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    outline = interview_planner.generate_interview_outline(
        entry=entry, target_questions=3, llm=fake
    )

    assert len(outline.items) == 3
    assert [item.subtopic for item in outline.items] == ["intro", "body", "wrap"]
    assert {item.difficulty for item in outline.items} == {"easy", "medium", "hard"}


def test_generate_interview_outline_resizes_when_llm_returns_wrong_count(
    monkeypatch, session, seeded_user, interview_planner
):
    fake = _FakeStructuredChatModel(
        scripted_items=[
            {"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"},
            {"subtopic": "b", "difficulty": "medium", "focus_prompt": "y"},
        ]
    )
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    outline = interview_planner.generate_interview_outline(
        entry=entry, target_questions=4, llm=fake
    )

    assert len(outline.items) == 4


def test_generate_interview_outline_raises_quiz_generation_error_on_llm_failure(
    monkeypatch, session, seeded_user, interview_planner, caplog
):
    """SRA-809 regression: the planner must propagate LLM failures so callers can
    distinguish "LLM down" from "LLM succeeded but returned an empty list"."""
    from sra.agents.quiz_graph import QuizGenerationError

    fake = _FakeStructuredChatModel(raise_on_invoke=True)
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    with caplog.at_level("ERROR", logger="sra.agents.interview_planner"):
        with pytest.raises(QuizGenerationError):
            interview_planner.generate_interview_outline(
                entry=entry, target_questions=3, llm=fake
            )
    assert any(
        "Interview planner failed" in record.getMessage() for record in caplog.records
    )


def test_generate_interview_outline_raises_on_non_list_response(
    monkeypatch, session, seeded_user, interview_planner
):
    """Schema-validation failures (wrong shape) also bubble up — never silent."""
    from sra.agents.quiz_graph import QuizGenerationError

    fake = _FakeStructuredChatModel(return_non_list=True)
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    with pytest.raises(QuizGenerationError):
        interview_planner.generate_interview_outline(
            entry=entry, target_questions=3, llm=fake
        )


def test_generate_interview_outline_prompt_includes_entry_topic_and_summary(
    monkeypatch, session, seeded_user, interview_planner
):
    fake = _FakeStructuredChatModel(
        scripted_items=[
            {"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"},
            {"subtopic": "b", "difficulty": "medium", "focus_prompt": "y"},
        ]
    )
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    interview_planner.generate_interview_outline(
        entry=entry, target_questions=2, llm=fake
    )

    prompt_text = " ".join(
        getattr(message, "content", "")
        if not isinstance(message, tuple)
        else str(message[1])
        for message in fake.seen_messages
    )
    assert entry.topic in prompt_text
    assert entry.summary in prompt_text


def test_interview_planner_module_exposes_required_public_symbols(interview_planner):
    """The TDD contract requires the planner module to surface typed shapes."""
    assert hasattr(interview_planner, "InterviewOutline")
    assert hasattr(interview_planner, "InterviewOutlineItem")
    assert hasattr(interview_planner, "generate_interview_outline")
    assert hasattr(interview_planner, "_get_chat_model")


def test_interview_planner_module_can_be_patched_at_seam(monkeypatch, session, seeded_user, interview_planner):
    """``_get_chat_model`` is the seam tests monkey-patch."""
    fake = _FakeStructuredChatModel(
        scripted_items=[{"subtopic": "a", "difficulty": "easy", "focus_prompt": "x"}]
    )
    monkeypatch.setattr(interview_planner, "_get_chat_model", lambda: fake)
    entry = _make_entry(session, seeded_user.id)

    outline = interview_planner.generate_interview_outline(
        entry=entry, target_questions=1, llm=fake
    )
    assert len(outline.items) == 1
