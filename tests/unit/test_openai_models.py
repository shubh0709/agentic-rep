import pytest

from sra.agents import ingestion_graph, query_graph


@pytest.mark.parametrize("agent", [ingestion_graph, query_graph])
def test_agent_uses_configured_openai_model_and_dotenv_key(monkeypatch, tmp_path, agent):
    """Both real clients must use the same configurable model and .env credentials."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-dotenv-test\n")

    model = agent._get_chat_model()

    assert model.model_name == "gpt-4.1-mini"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "sk-dotenv-test"
    assert "sk-dotenv-test" not in repr(model)
