"""Shared fixtures for the Playwright browser test suite.

These tests exercise the rendered web pages (landing, sign-in, settings,
quiz, dashboard, interview) through a real browser against a real
Uvicorn server. The goal is regression coverage for the user-facing
flows so a future feature (Phase 8+ interview, voice, etc.) cannot
silently break the existing UX without failing at least one of these
contracts.

Activation: opt-in via ``SRA_RUN_PLAYWRIGHT_TESTS=1`` because the suite
requires the ``playwright`` package and a Chromium browser binary.
``tests/integration/test_phase4_real_browser.py`` follows the same
pattern for the dummy round-trip; this conftest carries it across the
real auth/credential/render pipeline the dashboard needs.
"""

from __future__ import annotations

import importlib.util
import os
import socket
import threading
import time
import uuid
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from sra.api.app import create_app
from sra.auth.google_oauth import issue_app_credential
from sra.config import get_client_settings, get_settings

ROOT = Path(__file__).resolve().parents[2]


def _playwright_importable() -> bool:
    return importlib.util.find_spec("playwright") is not None


def _chromium_installed() -> bool:
    """Look for any installed Chromium build before spending 30s on launch."""
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    if not cache.exists():
        return False
    return any(cache.glob("chromium*"))


SKIP_REASON = (
    "playwright is opt-in: set SRA_RUN_PLAYWRIGHT_TESTS=1 and install "
    "'playwright install chromium' to run the real-browser suite"
)


@pytest.fixture(scope="session")
def playwright_browser():
    if os.environ.get("SRA_RUN_PLAYWRIGHT_TESTS") != "1":
        pytest.skip(SKIP_REASON)
    if not _playwright_importable():
        pytest.skip("playwright is not installed in this environment")
    if not _chromium_installed():
        pytest.skip(
            "chromium browser is not installed; run 'playwright install chromium'"
        )
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def browser_page(playwright_browser, live_server):
    """A signed-in browser context preloaded with the session cookie.

    The cookie is the only way the in-page ``fetch`` call can authenticate
    (it uses ``credentials: "same-origin"``, not the ``Authorization``
    header). Tests that need an anonymous browser can clear cookies via
    ``page.context.clear_cookies()`` after the fixture yields.
    """
    context = playwright_browser.new_context(ignore_https_errors=True)
    context.add_cookies(
        [
            {
                "name": "sra_session",
                "value": live_server["bearer"],
                "domain": "127.0.0.1",
                "path": "/",
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ]
    )
    page = context.new_page()
    page.set_default_timeout(10_000)
    yield page
    context.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server(monkeypatch, tmp_path):
    """Boot a real Uvicorn on a free port against a fresh in-memory DB.

    The fresh DB keeps each test isolated; the seeded user is created
    through the same SQLModel metadata the production code uses, so
    schema/migration drift surfaces here before it surfaces in CI.
    """
    from sra.db import models as db_models  # noqa: F401 — registers tables
    from sra.db.models import User
    from sra.db.session import get_engine

    if os.environ.get("SRA_RUN_PLAYWRIGHT_TESTS") != "1":
        pytest.skip(SKIP_REASON)

    db_file = tmp_path / f"playwright-{uuid.uuid4().hex}.db"
    db_url = f"sqlite:///{db_file}"

    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False},
    )
    # ``SQLModel.metadata.create_all`` runs the schema once for the test
    # engine. We then patch ``init_db`` to a no-op so the running server
    # does not run alembic on top of the freshly-created tables (which
    # would raise a ``CircularDependencyError`` for constraints SQLAlchemy
    # already inferred from the metadata).
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("sra.api.app.init_db", lambda: None)
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "playwright-signing-secret")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_CAPACITY", "10000")
    monkeypatch.setenv("RATE_LIMIT_ANONYMOUS_CAPACITY", "10000")
    monkeypatch.setenv("RATE_LIMIT_AUTHENTICATED_REFILL_PER_SECOND", "1000")
    monkeypatch.setenv("RATE_LIMIT_ANONYMOUS_REFILL_PER_SECOND", "1000")
    get_settings.cache_clear()
    get_client_settings.cache_clear()

    get_engine.cache_clear()

    # Stub the two LLM seams the pregen path hits so the suite never
    # needs a real OPENAI_API_KEY. The MCQ stub inserts deterministic
    # drafts; the planner stub inserts a minimal InterviewOutline.
    from sra.agents import interview_conversation_graph as _icg
    from sra.agents import interview_planner as _ip
    from sra.agents import quiz_graph as _qg
    from sra.agents.schemas import MCQDraft, StudyEntryDraft
    from sra.db.repository import create_quiz_set

    def _fake_gap(entry, user_id, session, question_count=10, option_count=4):  # noqa: ARG001
        drafts = [
            MCQDraft(
                question_text=f"q{i}",
                options=[f"o{j}" for j in range(option_count)],
                correct_option_index=i % option_count,
                explanation="e",
            )
            for i in range(question_count)
        ]
        return create_quiz_set(
            entry=entry,
            user_id=user_id,
            session=session,
            generated_on=date.today(),
            question_count=len(drafts),
            option_count=option_count,
            drafts=drafts,
        )

    def _fake_outline(entry, target_questions, *, llm=None):  # noqa: ARG001
        from sra.agents.interview_planner import (
            InterviewOutline,
            InterviewOutlineItem,
        )

        return InterviewOutline(
            items=[
                InterviewOutlineItem(
                    subtopic=f"sub{i}",
                    difficulty="medium",
                    focus_prompt=f"focus-{i}",
                )
                for i in range(target_questions)
            ]
        )

    class _FakeEntryExtractor:
        """Deterministic stand-in for the external ingestion boundary.

        Returns one draft per transcript so the route can produce a saved
        StudyEntry + ReviewSchedule without contacting OpenAI.
        """

        def extract(self, transcript):
            text = transcript
            topic_word = "Study Note"
            lowered = text.lower()
            if "mitosis" in lowered:
                topic_word = "Mitosis"
            elif "photo" in lowered:
                topic_word = "Photosynthesis"
            return [StudyEntryDraft(
                topic=topic_word,
                subtopic="Web capture",
                tags=["web"],
                summary=text[:200] or "Captured from the landing page.",
            )]

    class _FakeInterviewModel:
        """Finish a browser-driven interview after one deterministic answer."""

        def with_structured_output(self, schema, **kwargs):  # noqa: ARG002
            assert schema is _icg.InterviewDecision
            return self

        def invoke(self, messages, **kwargs):  # noqa: ARG002
            return _icg.InterviewDecision(
                score=90,
                rationale="The answer explains the core mechanism.",
                feedback="Clear and accurate.",
                next_action="finish",
                summary="Browser interview completed.",
            )

    monkeypatch.setattr("sra.jobs.quiz_pregeneration.generate_and_persist_questions", _fake_gap)
    monkeypatch.setattr(_qg, "generate_and_persist_questions", _fake_gap)
    monkeypatch.setattr(_ip, "generate_interview_outline", _fake_outline)
    _ip.generate_interview_outline = _fake_outline  # noqa: SLF001 (parity for direct callers)
    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeInterviewModel())

    user_id_holder: dict[str, int] = {}

    with Session(engine) as session:
        user = User(
            email="playwright@example.com",
            display_name="Playwright User",
            google_sub="playwright-subject",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.id is not None
        user_id_holder["id"] = user.id

    app = create_app()
    from sra.agents.entry_extractor import get_entry_extractor

    app.dependency_overrides[get_entry_extractor] = _FakeEntryExtractor
    # After create_app runs, the lifespan may have re-cached the engine;
    # clear it again so the TestClient's get_session and live_server["engine"]
    # point to the same SQLAlchemy URL.
    from sra.db.session import get_engine as _get_engine2

    _get_engine2.cache_clear()

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.1)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        pytest.fail(f"uvicorn did not bind 127.0.0.1:{port} within 10s")

    base_url = f"http://127.0.0.1:{port}"
    bearer = issue_app_credential(
        user_id_holder["id"],
        signing_secret="playwright-signing-secret",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    api_client = TestClient(app, headers={"Authorization": f"Bearer {bearer}"})

    try:
        yield {
            "base_url": base_url,
            "bearer": bearer,
            "user_id": user_id_holder["id"],
            "client": api_client,
            "engine": engine,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)
