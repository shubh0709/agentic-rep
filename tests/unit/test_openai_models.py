from sra.agents import entry_extractor, query_graph
from sra.agents.schemas import StudyEntryDrafts
from sra.config import get_settings


def _configure_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-dotenv-test\n")
    get_settings.cache_clear()


def test_query_agent_uses_configured_openai_model_and_dotenv_key(monkeypatch, tmp_path):
    _configure_dotenv(monkeypatch, tmp_path)

    model = query_graph._get_chat_model()

    assert model.model_name == "gpt-4.1-mini"
    assert model.openai_api_key is not None
    assert model.openai_api_key.get_secret_value() == "sk-dotenv-test"
    assert "sk-dotenv-test" not in repr(model)


def test_ingestion_adapter_uses_configured_openai_model_and_dotenv_key(monkeypatch, tmp_path):
    """The adapter owns provider configuration behind the EntryExtractor port."""
    _configure_dotenv(monkeypatch, tmp_path)
    captured = {}

    class CapturingChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def with_structured_output(self, schema, **kwargs):
            assert schema is StudyEntryDrafts
            return self

        def invoke(self, messages):  # noqa: ARG002
            return StudyEntryDrafts(drafts=[])

    monkeypatch.setattr(entry_extractor, "ChatOpenAI", CapturingChatModel)

    assert entry_extractor.OpenAIEntryExtractor().extract("I studied mitosis") == []
    assert captured["model"] == "gpt-4.1-mini"
    assert captured["api_key"].get_secret_value() == "sk-dotenv-test"
    assert "sk-dotenv-test" not in repr(captured)
