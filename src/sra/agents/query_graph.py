"""
LangGraph agent: answer natural-language questions about a user's
own study history by calling DB-backed tools.
"""

from typing import TypedDict

from langchain_core.messages import MessageLikeRepresentation
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from sqlmodel import Session

from sra.config import get_settings


class QueryInput(TypedDict):
    """Accept message objects, dictionaries, or (role, text) tuples at the boundary."""

    messages: list[MessageLikeRepresentation]


def make_tools(user_id: int, session: Session) -> list[BaseTool]:
    """
    Build and return the three tools below as a list, each closed
    over `user_id` and `session` so the LLM can only ever choose *what* to
    search, never *whose* data to search.

    Concept: closing a tool over server-trusted context instead of
    trusting an LLM-supplied `user_id` argument -- the correct
    pattern any time a tool call must stay scoped to the calling
    user. Defining the @tool-decorated functions here keeps that
    trusted context out of the tool argument schemas.
    """
    raise NotImplementedError


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


def build_query_graph(user_id: int, session: Session):
    """
    Build a tool-calling agent bound to make_tools(user_id, session):
    a node that calls the LLM with the tools bound via
    `.bind_tools(...)`, looping to a tools-execution node and back
    until the model responds with no further tool calls, at which
    point that final message is the answer. Return the compiled
    graph.

    Concept: this is the same `[agent] <-> [tools]` loop shape as
    LangGraph's prebuilt ReAct agent (`langgraph.prebuilt.create_react_agent`
    is worth reading even if you wire the loop by hand) -- the shape
    every tool-calling agent in this project reuses.
    """
    raise NotImplementedError
