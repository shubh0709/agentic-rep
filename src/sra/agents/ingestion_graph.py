from langchain_openai import ChatOpenAI

from sra.config import get_settings


def _get_chat_model() -> ChatOpenAI:
    """
    Construct the OpenAI model used by extract_entries.
    Structured-output setup and invocation happen in extract_entries.

    Kept separate so tests can monkeypatch
    `sra.agents.ingestion_graph._get_chat_model` with a fake model,
    avoiding network calls and API keys.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=2048,
        timeout=30,
        max_retries=1,
    )
