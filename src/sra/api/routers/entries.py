from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from sra.agents.ingestion_graph import IngestionState, build_ingestion_graph
from sra.agents.tracing import trace_agent
from sra.api.deps import get_session, require_user
from sra.api.schemas import EntryOut, IngestRequest
from sra.db.models import User

router = APIRouter(prefix="/entries", tags=["entries"])


@router.post("/ingest", status_code=201, response_model=list[EntryOut])
def ingest_transcript(
    body: IngestRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> list[EntryOut]:
    """
    Run the ingestion agent on body.transcript for the current user
    and return the created entries.

    Steps:
      1. graph = build_ingestion_graph()
      2. result = graph.invoke({"transcript": body.transcript, "user_id": user.id,
                                 "session": session, "drafts": [], "entries": [], "error": None})
      3. Map result["entries"] (StudyEntry rows) into EntryOut and return them.
         If result["error"] is set instead, raise HTTPException(422, detail=result["error"]).
    """
    assert user.id is not None
    initial_state: IngestionState = {
        "transcript": body.transcript,
        "user_id": user.id,
        "session": session,
        "drafts": [],
        "entries": [],
        "error": None,
    }
    graph = build_ingestion_graph()
    with trace_agent():
        result = graph.invoke(initial_state)
    if result["error"]:
        raise HTTPException(status_code=422, detail=result["error"])

    out = []
    for entry in result["entries"]:
        assert entry.id is not None
        out.append(
            EntryOut(
                id=entry.id,
                topic=entry.topic,
                subtopic=entry.subtopic,
                tags=entry.tags,
                summary=entry.summary,
                created_at=entry.created_at,
            )
        )
    return out
