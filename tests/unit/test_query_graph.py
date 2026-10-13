from datetime import date

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from sra.agents.query_graph import build_query_graph
from sra.db.models import ReviewSchedule, StudyEntry


class FakeToolCallingModel:
    """
    A stand-in for a ChatOpenAI bound with .bind_tools(...): the
    first .invoke() returns a message requesting one tool call, the
    second .invoke() (after the tool result is appended) returns a
    plain final answer -- the minimal two-turn shape build_query_graph
    needs to exercise its [agent] <-> [tools] loop.
    """

    def __init__(self, tool_name: str, tool_args: dict, final_answer: str):
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._final_answer = final_answer
        self._calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._calls += 1
        if self._calls == 1:
            return {
                "role": "ai",
                "tool_calls": [{"name": self._tool_name, "args": self._tool_args, "id": "call_1"}],
                "content": "",
            }
        return {"role": "ai", "tool_calls": [], "content": self._final_answer}


def test_query_graph_calls_search_tool_for_a_topic_question(monkeypatch, session, seeded_user):
    session.add(StudyEntry(user_id=seeded_user.id, topic="Photosynthesis", summary="light + dark reactions", raw_transcript="t"))
    session.commit()

    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: FakeToolCallingModel(
            tool_name="search_entries_tool",
            tool_args={"query": "photosynthesis"},
            final_answer="You studied photosynthesis: light + dark reactions.",
        ),
    )

    graph = build_query_graph(seeded_user.id, session)
    result = graph.invoke({"messages": [("user", "what did I learn about photosynthesis?")]})

    final_text = result["messages"][-1].content if hasattr(result["messages"][-1], "content") else result["messages"][-1]["content"]
    assert "photosynthesis" in final_text.lower()


def test_query_graph_calls_due_reviews_tool_for_whats_due_today(monkeypatch, session, seeded_user):
    entry = StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="cell division", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    session.add(ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=date.today(), status="pending"))
    session.commit()

    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: FakeToolCallingModel(
            tool_name="get_due_reviews_tool",
            tool_args={"as_of": date.today().isoformat()},
            final_answer="Mitosis is due today.",
        ),
    )

    graph = build_query_graph(seeded_user.id, session)
    result = graph.invoke({"messages": [("user", "what's due today?")]})

    final_text = result["messages"][-1].content if hasattr(result["messages"][-1], "content") else result["messages"][-1]["content"]
    assert "mitosis" in final_text.lower()


def test_query_graph_never_leaks_another_users_entries(monkeypatch, session, seeded_user, other_user):
    session.add(StudyEntry(user_id=other_user.id, topic="Secret Topic", summary="s", raw_transcript="t"))
    session.commit()

    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: FakeToolCallingModel(
            tool_name="search_entries_tool",
            tool_args={"query": ""},
            final_answer="I couldn't find anything in your history.",
        ),
    )

    graph = build_query_graph(seeded_user.id, session)
    result = graph.invoke({"messages": [("user", "what have I studied?")]})

    final_text = result["messages"][-1].content if hasattr(result["messages"][-1], "content") else result["messages"][-1]["content"]
    assert "secret topic" not in final_text.lower()


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.received_messages = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    def invoke(self, messages):
        self.received_messages.append(list(messages))
        return next(self.responses)


def test_graph_passes_real_tool_results_back_to_model_across_multiple_rounds(monkeypatch, session, seeded_user, other_user):
    entry = StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="Cell division", raw_transcript="Detailed phases")
    session.add_all([
        entry,
        StudyEntry(user_id=other_user.id, topic="Private topic", summary="Secret", raw_transcript="Private notes"),
    ])
    session.commit()
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[
            {"name": "search_entries_tool", "args": {"query": "", "user_id": other_user.id}, "id": "search"},
            {"name": "get_due_reviews_tool", "args": {"as_of": "2026-10-07"}, "id": "due"},
        ]),
        AIMessage(content="", tool_calls=[
            {"name": "get_entry_detail_tool", "args": {"entry_id": entry.id}, "id": "detail"},
        ]),
        AIMessage(content="You studied mitosis."),
    ])
    monkeypatch.setattr("sra.agents.query_graph._get_chat_model", lambda: model)

    result = build_query_graph(seeded_user.id, session).invoke({"messages": [("user", "What did I study?")]})

    assert len(model.received_messages) == 3
    second_turn_results = [m for m in model.received_messages[1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in second_turn_results] == ["search", "due"]
    assert "Mitosis" in second_turn_results[0].text
    assert "Private" not in second_turn_results[0].text
    assert "Detailed phases" not in second_turn_results[0].text
    assert "No review entries" in second_turn_results[1].text
    third_turn_results = [m for m in model.received_messages[2] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in third_turn_results] == ["search", "due", "detail"]
    assert "Detailed phases" in third_turn_results[-1].text
    assert result["messages"][-1].text == "You studied mitosis."


@pytest.mark.parametrize(("tool_name", "arguments"), [
    ("unknown_tool", {}),
    ("get_due_reviews_tool", {}),
    ("get_due_reviews_tool", {"as_of": "2026-02-30"}),
])
def test_graph_returns_tool_errors_to_model_for_correction(monkeypatch, session, seeded_user, tool_name, arguments):
    model = ScriptedModel([
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": arguments, "id": "bad"}]),
        AIMessage(content="", tool_calls=[
            {"name": "get_due_reviews_tool", "args": {"as_of": "2026-10-07"}, "id": "corrected"},
        ]),
        AIMessage(content="No reviews are due."),
    ])
    monkeypatch.setattr("sra.agents.query_graph._get_chat_model", lambda: model)

    result = build_query_graph(seeded_user.id, session).invoke({"messages": [("user", "What's due?")]})

    error = model.received_messages[1][-1]
    assert isinstance(error, ToolMessage) and error.status == "error" and error.tool_call_id == "bad"
    corrected = model.received_messages[2][-1]
    assert isinstance(corrected, ToolMessage) and corrected.status == "success"
    assert result["messages"][-1].text == "No reviews are due."


def test_graph_finishes_without_tool_calls(monkeypatch, session, seeded_user):
    model = ScriptedModel([AIMessage(content="Hello! Ask me about your study history.")])
    monkeypatch.setattr("sra.agents.query_graph._get_chat_model", lambda: model)

    result = build_query_graph(seeded_user.id, session).invoke({"messages": [("user", "Hello")]})

    assert len(model.received_messages) == 1
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])
    assert result["messages"][-1].text.startswith("Hello!")
