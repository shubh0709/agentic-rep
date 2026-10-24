"""Browser-facing pages rendered by Jinja2 templates.

Pages reuse the existing API routers under ``/api/v1`` (quiz, preferences,
auth). Authentication is the same session cookie the API uses; SSR pages
read it via :func:`sra.auth.dependencies.require_user`, and the static
JavaScript on the quiz and settings pages calls the API with
``credentials: "same-origin"`` so the cookie is included automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from sra.api.deps import get_session, require_user
from sra.api.rate_limit import limit_authenticated
from sra.auth.dependencies import get_current_user
from sra.config import Settings, get_settings
from sra.db.models import User
from sra.db.repository import UserNotFoundError, get_user_preferences

WEB_DIR = Path(__file__).resolve().parent
STATIC_VERSION = "20261024"

router = APIRouter(tags=["web"], include_in_schema=False)
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.globals["static_version"] = STATIC_VERSION


def _google_login_url(settings: Settings) -> str:
    return "/api/v1/auth/google/login?client=web"


def _auth_configured(settings: Settings) -> bool:
    return bool(
        settings.google_client_id
        and settings.auth_signing_secret.get_secret_value()
    )


def _current_user_or_none(request: Request, session: Session) -> User | None:
    """Resolve the request's user if a cookie OR bearer credential is valid.

    Returns ``None`` for anonymous browsers so pages can render an
    unauthenticated nav without raising a 401. The same dependency
    chain the API uses (``get_current_user``) accepts both transports,
    which means e2e tests using ``Authorization: Bearer`` work too.
    """
    settings = get_settings()
    has_cookie = bool(request.cookies.get(settings.auth_cookie_name))
    has_bearer = bool(request.headers.get("Authorization"))
    if not has_cookie and not has_bearer:
        return None
    try:
        return get_current_user(request=request, session=session, settings=settings)
    except HTTPException:
        return None


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """Public landing page. Shows an authenticated nav if a valid cookie exists."""
    settings = get_settings()
    user = _current_user_or_none(request, session)
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "current_user": user,
            "google_login_url": _google_login_url(settings),
            "auth_configured": _auth_configured(settings),
        },
    )


@router.get("/signin", response_class=HTMLResponse)
def signin_page(
    request: Request,
    next: str | None = None,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Public sign-in page that starts the Google OAuth flow."""
    settings = get_settings()
    user = _current_user_or_none(request, session)
    if user is not None:
        target = next or "/quiz"
        return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "signin.html",
        {
            "current_user": None,
            "google_login_url": _google_login_url(settings),
            "auth_configured": _auth_configured(settings),
            "next": next,
        },
    )


@router.post("/signout")
def signout() -> RedirectResponse:
    """Clear the session cookie and redirect home.

    Mirrors ``/api/v1/auth/logout`` for browser forms. Returns 303 so a
    POST/PUT-following browser ends up on a GET to ``/``.
    """
    settings = get_settings()
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(
        settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    return response


@router.get("/quiz", response_class=HTMLResponse, dependencies=[Depends(limit_authenticated)])
def quiz_page(request: Request, user: User = Depends(require_user)) -> HTMLResponse:
    """Protected daily quiz page. Renders the shell; JS loads today's sets."""
    return templates.TemplateResponse(
        request,
        "quiz.html",
        {"current_user": user, "active_nav": "quiz"},
    )


@router.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(limit_authenticated)])
def dashboard_page(request: Request, user: User = Depends(require_user)) -> HTMLResponse:
    """Protected study dashboard.

    Renders the entry list and upcoming-review timeline; the JS module
    ``web/static/js/dashboard.js`` fetches data from ``/api/v1/search``,
    ``/api/v1/digest``, and ``/api/v1/reviews/upcoming`` so the page itself
    stays a thin shell that requires no server-side data fetching.
    """
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"current_user": user, "active_nav": "dashboard"},
    )


def _read_static_file(rel_path: str) -> str:
    """Read a file from ``dashboard/static`` and return its UTF-8 contents.

    Used by ``/dashboard/interview`` and any future static-page mounts that
    need to be served through the Jinja2 router so they share the
    authentication and rate-limit dependencies.
    """
    candidate = Path(__file__).resolve().parent.parent / "dashboard" / "static" / rel_path
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="dashboard page not found")
    return candidate.read_text(encoding="utf-8")


@router.get("/dashboard/interview", response_class=HTMLResponse, dependencies=[Depends(limit_authenticated)])
def interview_page(request: Request, user: User = Depends(require_user)) -> HTMLResponse:
    """Protected interactive AI interview page.

    Renders ``dashboard/static/interview.html`` directly so the page can use
    the existing dashboard helpers without a parallel Jinja2 template. The
    JS module ``dashboard/static/interview.js`` consumes the SRA-805 routes
    (``POST /api/v1/quiz/{id}/attempts`` with ``mode: "interview"``,
    ``POST /api/v1/quiz/attempts/{id}/interview-turn``, and
    ``GET /api/v1/quiz/attempts/{id}/interview``).
    """
    html = _read_static_file("interview.html")
    return HTMLResponse(content=html)


@router.get("/settings", response_class=HTMLResponse, dependencies=[Depends(limit_authenticated)])
def settings_page(
    request: Request,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Protected preferences page. Server-renders current values; JS handles PUT."""
    assert user.id is not None
    try:
        prefs = get_user_preferences(user.id, session)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=404, detail="User not found") from exc
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "current_user": user,
            "active_nav": "settings",
            "preferences": {
                "preferred_review_time": prefs["preferred_review_time"],
                "timezone": prefs["timezone"],
                "quiz_question_count": prefs["quiz_question_count"],
                "quiz_option_count": prefs["quiz_option_count"],
                "wakeup_notification_sent_at": prefs["wakeup_notification_sent_at"],
            },
            "max_question_count": 10,
            "max_option_count": 6,
        },
    )


@router.get("/healthz", response_class=HTMLResponse)
def healthz() -> HTMLResponse:
    """Lightweight page used by the test suite to confirm templates are wired."""
    return HTMLResponse("<!doctype html><title>ok</title><p>ok</p>")


def mount(app: Any) -> None:
    """Mount the web router and static assets on a FastAPI app.

    Keeps all wiring in one call so :func:`sra.api.app.create_app` only has
    to know ``web.mount(app)``. Static files are served from
    ``/static`` -> ``web/static/`` and ``/dashboard-static`` ->
    ``dashboard/static/`` so the interview page can pull its sibling JS
    via ``<script src="interview.js">``.
    """
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="web-static")
    dashboard_static = Path(__file__).resolve().parent.parent / "dashboard" / "static"
    if dashboard_static.exists():
        app.mount(
            "/dashboard-static",
            StaticFiles(directory=str(dashboard_static)),
            name="dashboard-static",
        )
    app.include_router(router)


__all__ = ["mount", "router", "templates"]
