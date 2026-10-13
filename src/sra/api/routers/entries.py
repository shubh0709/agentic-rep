from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.api.deps import get_current_user, get_session
from sra.api.schemas import EntryOut, IngestRequest
from sra.db.models import User

router = APIRouter(prefix="/entries", tags=["entries"])


@router.post("/ingest", status_code=201, response_model=list[EntryOut])
def ingest_transcript(
    body: IngestRequest,
    user: User = Depends(get_current_user),
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
    raise NotImplementedError
