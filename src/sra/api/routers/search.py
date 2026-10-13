from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.api.deps import get_current_user, get_session
from sra.api.schemas import EntryOut
from sra.db.models import User
from sra.db.repository import search_entries

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[EntryOut])
def search(
    q: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[EntryOut]:
    """
    Call repository.search_entries(user.id, q, session, date_from, date_to)
    (parsing date_from/date_to strings to date objects if present) and
    map the results to EntryOut.
    """
    assert user.id is not None
    entries = search_entries(
        user_id=user.id,
        query=q,
        session=session,
        date_from=date.fromisoformat(date_from) if date_from else None,
        date_to=date.fromisoformat(date_to) if date_to else None,
    )

    out = []
    for entry in entries:
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
