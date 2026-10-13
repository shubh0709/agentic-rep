from datetime import date

from fastapi import APIRouter, Depends
from sqlmodel import Session

from sra.api.deps import get_current_user, get_session
from sra.api.schemas import EntryOut, ReviewOut
from sra.db.models import StudyEntry, User
from sra.db.repository import get_due_reviews

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
    assert user.id is not None
    reviews = get_due_reviews(user_id=user.id, as_of=date.today(), session=session)

    out = []
    for review in reviews:
        entry = session.get(StudyEntry, review.entry_id)
        assert entry is not None
        assert entry.id is not None
        assert review.id is not None

        out.append(
            ReviewOut(
                id=review.id,
                entry=EntryOut(
                    id=entry.id,
                    topic=entry.topic,
                    subtopic=entry.subtopic,
                    tags=entry.tags,
                    summary=entry.summary,
                    created_at=entry.created_at,
                ),
                interval_label=review.interval_label,
                scheduled_date=review.scheduled_date,
                status=review.status,
            )
        )

    return out
