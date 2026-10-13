
from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.api.deps import get_current_user, get_session
from sra.api.schemas import EntryOut
from sra.db.models import User

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
    raise NotImplementedError
