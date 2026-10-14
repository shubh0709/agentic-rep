from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.agents.query_graph import build_query_graph
from sra.agents.tracing import trace_agent
from sra.api.deps import get_session, require_user
from sra.api.schemas import ChatRequest, ChatResponse
from sra.db.models import User

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
def chat(
    body: ChatRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ChatResponse:
    """
    graph = build_query_graph(user.id, session)
    result = graph.invoke({"messages": [("user", body.question)]})
    Return ChatResponse(answer=<content of the final AI message in result["messages"]>).
    """
    assert user.id is not None
    graph = build_query_graph(user.id, session)
    with trace_agent():
        result = graph.invoke({"messages": [("user", body.question)]})
    return ChatResponse(answer=result["messages"][-1].text)
