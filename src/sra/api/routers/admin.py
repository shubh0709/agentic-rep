"""Administrator-only operational aggregates."""

from datetime import UTC, datetime, time

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from sra.api.deps import get_session, require_admin
from sra.api.rate_limit import RateLimitState, RateLimitSummary, get_rate_limits, limit_authenticated
from sra.db.models import StudyEntry, User

router = APIRouter(
    prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin), Depends(limit_authenticated)]
)


@router.get("/rate-limits")
def get_rate_limit_summary(limits: RateLimitState = Depends(get_rate_limits)) -> dict[str, RateLimitSummary]:
    """Expose configured rates and aggregate counts, never principal keys."""
    return {"authenticated": limits.authenticated.summary(), "anonymous": limits.anonymous.summary()}


@router.get("/usage")
def get_usage(
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    """Return counts only; never include user profiles or study content."""
    del admin
    today = datetime.now(UTC).date()
    start = datetime.combine(today, time.min, tzinfo=UTC)
    user_count = session.exec(select(func.count()).select_from(User)).one()
    entry_count = session.exec(select(func.count()).select_from(StudyEntry)).one()
    entries_created_today = session.exec(
        select(func.count()).select_from(StudyEntry).where(StudyEntry.created_at >= start)
    ).one()
    return {
        "user_count": user_count,
        "entry_count": entry_count,
        "entries_created_today": entries_created_today,
    }
