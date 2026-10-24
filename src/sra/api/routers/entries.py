from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import Session

from sra.agents.entry_extractor import EntryExtractor, get_entry_extractor
from sra.agents.ingestion_graph import IngestionState, build_ingestion_graph
from sra.agents.tracing import trace_agent
from sra.api.deps import get_session, require_user
from sra.api.schemas import EntryOut, EntryUpdateRequest, IngestRequest
from sra.db.models import StudyEntry, User
from sra.db.repository import delete_entry_cascade, update_entry

router = APIRouter(prefix="/entries", tags=["entries"])


def _entry_out(entry: StudyEntry) -> EntryOut:
    assert entry.id is not None
    return EntryOut(
        id=entry.id,
        topic=entry.topic,
        subtopic=entry.subtopic,
        tags=entry.tags,
        summary=entry.summary,
        raw_transcript=entry.raw_transcript,
        created_at=entry.created_at,
    )


def _owned_entry(entry_id: int, user: User, session: Session) -> StudyEntry:
    entry = session.get(StudyEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.user_id != user.id:
        raise HTTPException(status_code=403, detail="Entry not accessible")
    return entry


@router.patch("/{entry_id}", response_model=EntryOut)
def patch_entry(
    entry_id: int,
    body: EntryUpdateRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> EntryOut:
    entry = _owned_entry(entry_id, user, session)
    return _entry_out(
        update_entry(entry, topic=body.topic, created_at=body.created_at, session=session)
    )


@router.delete("/{entry_id}", status_code=204)
def delete_entry(
    entry_id: int,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> Response:
    delete_entry_cascade(_owned_entry(entry_id, user, session), session)
    return Response(status_code=204)


@router.post("/ingest", status_code=201, response_model=list[EntryOut])
def ingest_transcript(
    body: IngestRequest,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
    extractor: EntryExtractor = Depends(get_entry_extractor),
) -> list[EntryOut]:
    """
    Run the ingestion agent on body.transcript for the current user
    and return the created entries.

    Steps:
      1. FastAPI resolves the EntryExtractor dependency and passes it to
         build_ingestion_graph(extractor=extractor).
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
    graph = build_ingestion_graph(extractor=extractor)
    with trace_agent():
        result = graph.invoke(initial_state)
    if result["error"]:
        raise HTTPException(status_code=422, detail=result["error"])

    out = []
    for entry in result["entries"]:
        assert entry.id is not None
        out.append(_entry_out(entry))
    return out
