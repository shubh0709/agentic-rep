
from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.api.deps import get_current_user, get_session
from sra.api.schemas import ReviewOut
from sra.db.models import User

router = APIRouter(prefix="/digest", tags=["digest"])


@router.get("", response_model=list[ReviewOut])
def get_digest(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ReviewOut]:
    """
    Call repository.get_due_reviews(user.id, date.today(), session)
    and map each ReviewSchedule (with its parent StudyEntry) into a
    ReviewOut.
    """
    raise NotImplementedError
