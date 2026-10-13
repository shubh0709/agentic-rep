from unittest.mock import Mock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client
from langsmith.run_helpers import get_tracing_context
from pydantic import SecretStr

from sra.agents.schemas import StudyEntryDrafts
from sra.agents.tracing import _get_client, trace_agent
from sra.config import Settings


class FakeStudyModel(FakeMessagesListChatModel):
    """Use real LangChain callbacks with scripted responses and no model API."""

    def bind_tools(self, *args, **kwargs):
        return self

    def with_structured_output(self, *args, **kwargs):
        def parse(message: BaseMessage) -> StudyEntryDrafts:
            return StudyEntryDrafts.model_validate_json(message.text)

        return self | RunnableLambda(parse)


@pytest.fixture
def tracing_setup(monkeypatch):
    settings = Settings(
        openai_api_key=SecretStr("sk-test"),
        langsmith_tracing=True,
        langsmith_api_key=SecretStr("ls-test"),
        langsmith_project="study-test",
        langsmith_endpoint="https://eu.api.smith.langchain.com/",
        langsmith_workspace_id="test-workspace",
    )
    trace_client = Mock(spec=Client)
    client_factory = Mock(return_value=trace_client)
    monkeypatch.setattr("sra.agents.tracing.get_settings", lambda: settings)
    monkeypatch.setattr("sra.agents.tracing.Client", client_factory)
    _get_client.cache_clear()
    yield settings, trace_client, client_factory
    wait_for_all_tracers()
    _get_client.cache_clear()


def test_api_traces_include_graphs_models_and_tools(client, monkeypatch, tracing_setup):
    settings, trace_client, client_factory = tracing_setup
    ingestion_model = FakeStudyModel(responses=[
        AIMessage(content='{"drafts": [{"topic": "Mitosis", "summary": "Cell division"}]}'),
    ])
    query_model = FakeStudyModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "search_entries_tool", "args": {"query": "mitosis"}, "id": "search"},
        ]),
        AIMessage(content="You studied mitosis."),
    ])
    monkeypatch.setattr("sra.agents.ingestion_graph._get_chat_model", lambda: ingestion_model)
    monkeypatch.setattr("sra.agents.query_graph._get_chat_model", lambda: query_model)

    assert client.post("/api/v1/entries/ingest", json={"transcript": "I studied mitosis"}).status_code == 201
    response = client.post("/api/v1/chat", json={"question": "What did I study?"})
    assert response.status_code == 200
    assert response.json()["answer"] == "You studied mitosis."
    wait_for_all_tracers()

    runs = [call.kwargs for call in trace_client.create_run.call_args_list]
    roots = {run["name"]: run for run in runs if run.get("parent_run_id") is None}
    assert set(roots) == {"sra-ingestion", "sra-query"}
    assert all(run["session_name"] == settings.langsmith_project for run in runs)
    assert len([run for run in runs if run["run_type"] == "llm"]) == 3
    tool_run = next(run for run in runs if run["run_type"] == "tool")
    assert tool_run["name"] == "search_entries_tool"
    tools_node = next(run for run in runs if run["id"] == tool_run["parent_run_id"])
    assert tools_node["name"] == "tools"
    assert tools_node["parent_run_id"] == roots["sra-query"]["id"]
    client_factory.assert_called_once_with(
        api_key="ls-test",
        api_url="https://eu.api.smith.langchain.com",
        workspace_id="test-workspace",
    )


def test_disabled_tracing_does_not_create_client_or_upload(tracing_setup):
    settings, trace_client, client_factory = tracing_setup
    settings.langsmith_tracing = False

    with trace_agent():
        result = FakeStudyModel(responses=[AIMessage(content="Hello")]).invoke("Hi")

    assert result.text == "Hello"
    client_factory.assert_not_called()
    trace_client.create_run.assert_not_called()


def test_tracing_context_is_restored_after_failure(tracing_setup):
    original = get_tracing_context()
    with pytest.raises(RuntimeError, match="Model failed"), trace_agent():
        assert get_tracing_context()["enabled"] is True
        raise RuntimeError("Model failed")
    assert get_tracing_context() == original
