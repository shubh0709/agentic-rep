"""SRA-501 — GET / PUT /api/v1/me/preferences."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlmodel import Session

from sra.api.deps import get_session
from sra.api.schemas import PreferencesIn, PreferencesOut
from sra.db.models import User
from sra.db.repository import (
    UserNotFoundError,
    UserPreferences,
    get_user_preferences,
    update_user_preferences,
)

router = APIRouter(prefix="/me", tags=["preferences"])


def _to_out(prefs: UserPreferences) -> PreferencesOut:
    return PreferencesOut(
        preferred_review_time=prefs["preferred_review_time"],
        timezone=prefs["timezone"],
        quiz_question_count=prefs["quiz_question_count"],
        quiz_option_count=prefs["quiz_option_count"],
        interview_question_count=prefs["interview_question_count"],
        interview_target_minutes=prefs["interview_target_minutes"],
        audio_enabled=prefs["audio_enabled"],
        wakeup_notification_sent_at=prefs["wakeup_notification_sent_at"],
    )


def _caller(request: Request) -> User:
    user: User | None = getattr(request.state, "user", None)
    if user is None or user.id is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/preferences", response_model=PreferencesOut)
def get_preferences(
    request: Request,
    session: Session = Depends(get_session),
) -> PreferencesOut:
    """Return the calling user's persisted preferences, or the model defaults."""
    user = _caller(request)
    assert user.id is not None
    try:
        prefs = get_user_preferences(user.id, session)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    return _to_out(prefs)


@router.put("/preferences", response_model=PreferencesOut)
def put_preferences(
    body: dict,
    request: Request,
    session: Session = Depends(get_session),
) -> PreferencesOut:
    """Validate and persist the caller-supplied preference fields.

    Returns 400 on validation failures so the dashboard can surface a
    typed error envelope without a second round-trip; returns 404 if the
    authenticated user row has vanished between the credential check and
    this handler.
    """
    try:
        parsed = PreferencesIn.model_validate(body)
    except ValidationError as exc:
        detail = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
            for err in exc.errors(include_url=False)
        ]
        raise HTTPException(status_code=400, detail=detail) from exc

    user = _caller(request)
    assert user.id is not None
    payload: dict[str, object] = {
        "preferred_review_time": parsed.preferred_review_time,
        "timezone": parsed.timezone,
        "quiz_question_count": parsed.quiz_question_count,
        "quiz_option_count": parsed.quiz_option_count,
    }
    if parsed.interview_question_count is not None:
        payload["interview_question_count"] = parsed.interview_question_count
    if parsed.interview_target_minutes is not None:
        payload["interview_target_minutes"] = parsed.interview_target_minutes
    if "audio_enabled" in parsed.model_fields_set:
        payload["audio_enabled"] = parsed.audio_enabled
    try:
        prefs = update_user_preferences(user.id, payload, session)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    return _to_out(prefs)
