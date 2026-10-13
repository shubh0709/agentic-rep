from langchain_openai import ChatOpenAI

from sra.config import get_settings


def _get_chat_model() -> ChatOpenAI:
    """
    Construct the OpenAI model used by build_query_graph's
    agent node.

    Concept: same seam as ingestion_graph._get_chat_model -- kept as
    its own function so tests can monkeypatch
    `sra.agents.query_graph._get_chat_model` with a fake model that
    returns a scripted sequence of tool calls, and assert on which
    tools actually got invoked without a live OpenAI call.
    """
    settings = get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        max_completion_tokens=2048,
        timeout=30,
        max_retries=1,
    )
