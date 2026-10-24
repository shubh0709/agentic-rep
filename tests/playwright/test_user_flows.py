"""SRA-810 — Playwright regression coverage for every user-facing flow.

These tests pin down the everyday flows of the SRA product so a new
feature (most recently Phase 8's voice-based interview) cannot regress
them silently. Each scenario drives a real headless Chromium against a
real Uvicorn instance — exactly the failure surface the bug report
("voice interview broke quiz pregeneration") would have caught earlier
had it existed.

Activation: opt-in via ``SRA_RUN_PLAYWRIGHT_TESTS=1`` because the suite
requires the ``playwright`` package and a Chromium browser binary.
``web.routes._current_user_or_none`` accepts either cookie OR bearer,
and ``src.sra.auth.dependencies.require_user`` rejects requests that
send both at once. The browser fixture preloads the signed session
*cookie* so the in-page ``fetch`` calls (which use
``credentials: "same-origin"``) authenticate without needing any
``Authorization`` header.
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false

from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest
from sqlmodel import Session, select

pytestmark = pytest.mark.phase8


def _dashboard_errors(page):
    """Capture uncaught page errors and error-level console messages."""
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )
    return errors


def test_landing_page_renders_without_session(browser_page, live_server):
    """Anonymous browser (no cookie) still gets the landing page."""
    page = browser_page
    errors = _dashboard_errors(page)
    page.context.clear_cookies()

    response = page.goto(live_server["base_url"] + "/")
    assert response is not None and response.status == 200
    assert "Sign in" in page.content()
    asset_urls = page.eval_on_selector_all(
        'link[rel="stylesheet"], script[src]',
        "nodes => nodes.map(node => node.href || node.src)",
    )
    assert asset_urls and all("?v=" in url for url in asset_urls)
    assert errors == []


def test_landing_page_flips_authenticated_nav_for_session_holder(browser_page, live_server):
    """With the session cookie set, the landing nav exposes Sign out."""
    page = browser_page

    response = page.goto(live_server["base_url"] + "/")
    assert response is not None and response.status == 200
    assert "Sign out" in page.content()


def test_landing_capture_form_submits_pasted_transcript_and_renders_entry(browser_page, live_server):
    """SRA-900: the landing-page capture card submits a pasted transcript
    to the same /api/v1/entries/ingest endpoint the desktop uses, and the
    saved entry topic + tags render inline without a full page reload."""
    page = browser_page

    page.goto(live_server["base_url"] + "/")
    page.wait_for_selector("#capture-form")

    transcript = (
        "Today I reviewed mitosis in the web UI capture card. "
        "The phases are prophase, metaphase, anaphase, telophase."
    )
    page.fill("#capture-transcript", transcript)
    page.click("button#capture-submit")

    status = page.locator("#capture-status")
    page.wait_for_function(
        "el => el.classList.contains('success') || el.classList.contains('error')",
        arg=status.element_handle(),
        timeout=15_000,
    )
    text = (status.text_content() or "").strip()
    assert "error" not in (status.get_attribute("class") or ""), text
    assert "Saved" in text, text

    page.wait_for_selector("#capture-results-list .capture-result-topic")
    topic_text = (
        page.locator("#capture-results-list .capture-result-topic").first.text_content() or ""
    ).strip()
    assert topic_text, "Expected a rendered topic after a successful capture"

    summary_text = (
        page.locator("#capture-results-list .capture-result-summary").first.text_content() or ""
    ).strip()
    assert summary_text, "Expected a rendered summary after a successful capture"

    raw = page.evaluate("() => document.getElementById('capture-transcript').value")
    assert raw == "", "Textarea should be cleared after a successful capture"

    saved = live_server["client"].get("/api/v1/search?q=mitosis")
    assert saved.status_code == 200
    rows = saved.json()
    assert any("Mitosis" in (row.get("topic") or "") for row in rows), rows


def test_landing_voice_capture_reports_stopped_state(browser_page, live_server):
    """Stopping browser dictation must clear the stale Listening status."""
    page = browser_page
    page.add_init_script(
        """() => {
          class FakeSpeechRecognition {
            start() {}
            stop() { queueMicrotask(() => this.onend && this.onend()); }
          }
          window.SpeechRecognition = FakeSpeechRecognition;
          window.webkitSpeechRecognition = FakeSpeechRecognition;
        }"""
    )
    page.goto(live_server["base_url"] + "/")

    page.click("#capture-record")
    assert "Listening" in (page.locator("#capture-status").text_content() or "")
    page.click("#capture-record")
    page.wait_for_function(
        "() => document.querySelector('#capture-status')?.textContent?.includes('Recording stopped')"
    )

    assert page.locator("#capture-record").get_attribute("aria-pressed") == "false"
    assert page.locator("#capture-record").text_content().strip() == "Record"


@pytest.mark.phase9
def test_dashboard_starts_without_page_or_console_errors(browser_page, live_server):
    """All three startup reads complete instead of a cross-IIFE ReferenceError."""
    page = browser_page
    errors = _dashboard_errors(page)

    response = page.goto(live_server["base_url"] + "/dashboard")
    assert response is not None and response.status == 200
    page.wait_for_function(
        "() => !document.querySelector('#entries-status')?.textContent?.includes('Loading')"
    )
    page.wait_for_function(
        "() => document.querySelector('#stat-due-today')?.textContent !== '—'"
    )
    page.wait_for_function(
        "() => !document.querySelector('#upcoming-status')?.textContent?.includes('Loading')"
    )
    assert page.locator("#stat-upcoming-total").text_content() == "0"
    assert errors == []


@pytest.mark.phase9
def test_dashboard_upcoming_summary_tracks_selected_window(browser_page, live_server):
    """Changing the review window keeps the summary label honest."""
    page = browser_page
    page.goto(live_server["base_url"] + "/dashboard")
    page.select_option("#upcoming-window", "7")
    page.wait_for_function(
        "() => !document.querySelector('#upcoming-status')?.textContent?.includes('Loading')"
    )

    assert page.locator("#stat-upcoming-label").text_content() == "Upcoming (next 7 days)"


@pytest.mark.phase9
def test_dashboard_fits_mobile_viewport_without_horizontal_scroll(
    browser_page, live_server
):
    """Primary navigation and dashboard content remain usable at phone width."""
    page = browser_page
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(live_server["base_url"] + "/dashboard")
    page.wait_for_function(
        "() => !document.querySelector('#entries-status')?.textContent?.includes('Loading')"
    )

    geometry = page.evaluate(
        """() => {
          const signout = document.querySelector('form[action="/signout"] button');
          const rect = signout.getBoundingClientRect();
          return {
            innerWidth: window.innerWidth,
            scrollWidth: document.documentElement.scrollWidth,
            signoutLeft: rect.left,
            signoutRight: rect.right,
          };
        }"""
    )
    assert geometry["scrollWidth"] <= geometry["innerWidth"]
    assert 0 <= geometry["signoutLeft"] < geometry["signoutRight"] <= geometry["innerWidth"]


@pytest.mark.phase9
def test_create_topic_modal_reopens_without_duplicate_submit_requests(
    browser_page, live_server
):
    """Reopening binds neither duplicate add-reference nor submit handlers."""
    page = browser_page
    requests: list[str] = []
    page.on(
        "request",
        lambda request: requests.append(request.url)
        if request.method == "POST" and request.url.endswith("/api/v1/topics")
        else None,
    )
    page.goto(live_server["base_url"] + "/dashboard")

    page.click('[data-action="create-topic"]')
    page.click('#create-topic-modal [data-action="cancel"]')
    page.click('[data-action="create-topic"]')
    page.fill("#ct-topic", "Mitosis")
    page.fill("#ct-summary", "How cells divide")
    page.click('#create-topic-modal [data-action="add-paste"]')
    title_fields = page.locator('input[name="ref-title"]')
    paste_fields = page.locator('textarea[name="ref-pasted-text"]')
    for index in range(title_fields.count()):
        title_fields.nth(index).fill(f"Course notes {index + 1}")
        paste_fields.nth(index).fill("Mitosis has four main stages.")
    page.click('#create-topic-modal [data-action="submit"]')

    page.wait_for_selector('.entry-card:has-text("Mitosis")', timeout=15_000)
    assert len(requests) == 1, requests


@pytest.mark.phase9
def test_create_topic_modal_traps_keyboard_focus(browser_page, live_server):
    """Tabbing at either edge of the dialog must not enter the background page."""
    page = browser_page
    page.goto(live_server["base_url"] + "/dashboard")
    page.click('[data-action="create-topic"]')

    page.locator('#create-topic-modal [data-action="close-modal"]').focus()
    page.keyboard.press("Shift+Tab")

    assert page.evaluate("() => document.activeElement?.dataset?.action") == "submit"


@pytest.mark.phase9
def test_create_topic_with_paste_renders_valid_quiz_and_interview_actions(
    browser_page, live_server
):
    """Generate now persists the topic and links only to mounted browser pages."""
    page = browser_page
    errors = _dashboard_errors(page)
    page.goto(live_server["base_url"] + "/dashboard")
    page.click('[data-action="create-topic"]')
    page.fill("#ct-topic", "Cell cycle")
    page.fill("#ct-summary", "Mitosis and cell division")
    page.click('#create-topic-modal [data-action="add-paste"]')
    page.fill('input[name="ref-title"]', "Biology notes")
    page.fill('textarea[name="ref-pasted-text"]', "Mitosis creates two daughter cells.")
    page.click('#create-topic-modal [data-action="submit"]')

    card = page.locator('.entry-card:has-text("Cell cycle")').first
    card.wait_for(timeout=15_000)
    quiz_href = card.locator('a:has-text("Start quiz")').get_attribute("href")
    interview_href = card.locator('a:has-text("Start interview")').get_attribute("href")
    assert quiz_href is not None and urlparse(quiz_href).path == "/quiz"
    assert interview_href is not None
    parsed_interview = urlparse(interview_href)
    assert parsed_interview.path == "/dashboard/interview"
    quiz_ids = parse_qs(parsed_interview.query).get("quiz_set_id", [])
    assert len(quiz_ids) == 1 and quiz_ids[0].isdigit()
    page.wait_for_function(
        "() => document.querySelector('#stat-total-entries')?.textContent === '1'"
    )
    page.wait_for_function(
        "() => Number(document.querySelector('#stat-upcoming-total')?.textContent) > 0"
    )
    assert errors == []


def test_settings_page_round_trips_legacy_4_field_form(browser_page, live_server):
    """SRA-501 + SRA-801 escape valve: the legacy 4-field settings form keeps working.

    Phase 8 promoted two extra ``interview_*`` fields to the API; the
    preferences form only ships the original four. The router + schema
    must accept the smaller payload and report ``Preferences saved.``
    with the defaults preserved. A regression here was the customer
    escape valve bug in this commit.
    """
    page = browser_page
    page.goto(live_server["base_url"] + "/settings")
    page.wait_for_selector("#preferences-form")

    page.fill("#preferred_review_time", "08:15")
    page.fill("#timezone", "America/New_York")
    page.fill("#quiz_question_count", "12")
    page.fill("#quiz_option_count", "4")
    page.click("button:has-text('Save preferences')")

    status = page.locator("#preferences-status")
    page.wait_for_function(
        "el => el.textContent.toLowerCase().includes('saved') || el.textContent.toLowerCase().includes('rejected')",
        arg=status.element_handle(),
        timeout=5_000,
    )
    text = (status.text_content() or "").strip()
    assert "rejected" not in text.lower(), text
    assert "saved" in text.lower()

    confirm = page.context.request.get(
        live_server["base_url"] + "/api/v1/me/preferences"
    )
    assert confirm.status == 200
    body = confirm.json()
    assert body["preferred_review_time"] == "08:15"
    assert body["timezone"] == "America/New_York"
    assert body["quiz_question_count"] == 12
    assert body["quiz_option_count"] == 4
    assert body["interview_question_count"] == 6
    assert body["interview_target_minutes"] == 5


def test_settings_page_surfaces_validation_error_inline(browser_page, live_server):
    """Server-side validation errors must render in the status span, not silently fail."""
    page = browser_page
    page.goto(live_server["base_url"] + "/settings")
    page.wait_for_selector("#preferences-form")

    page.fill("#preferred_review_time", "08:15")
    page.fill("#timezone", "IST")  # not a valid IANA name
    page.fill("#quiz_question_count", "12")
    page.fill("#quiz_option_count", "4")
    page.click("button:has-text('Save preferences')")

    status = page.locator("#preferences-status")
    page.wait_for_function(
        "el => el.classList.contains('error') || el.textContent.includes('Timezone')",
        arg=status.element_handle(),
        timeout=5_000,
    )
    text = (status.text_content() or "").strip()
    assert "Timezone" in text


def test_settings_page_preserves_interview_defaults_after_legacy_put(browser_page, live_server):
    """After Phase 8 sets interview_question_count=11 + interview_target_minutes=9,
    the legacy 4-field form must NOT reset them back to schema defaults (6, 5)."""
    page = browser_page

    put_initial = page.context.request.put(
        live_server["base_url"] + "/api/v1/me/preferences",
        data={
            "preferred_review_time": "07:00",
            "timezone": "UTC",
            "quiz_question_count": 10,
            "quiz_option_count": 4,
            "interview_question_count": 11,
            "interview_target_minutes": 9,
        },
    )
    assert put_initial.status == 200

    page.goto(live_server["base_url"] + "/settings")
    page.wait_for_selector("#preferences-form")
    page.fill("#preferred_review_time", "07:30")
    page.fill("#timezone", "UTC")
    page.fill("#quiz_question_count", "10")
    page.fill("#quiz_option_count", "4")
    page.click("button:has-text('Save preferences')")

    status = page.locator("#preferences-status")
    page.wait_for_function(
        "el => el.textContent.toLowerCase().includes('saved')",
        arg=status.element_handle(),
        timeout=5_000,
    )

    confirm = page.context.request.get(
        live_server["base_url"] + "/api/v1/me/preferences"
    )
    body = confirm.json()
    assert body["interview_question_count"] == 11
    assert body["interview_target_minutes"] == 9
    assert body["preferred_review_time"] == "07:30"


def test_quiz_dashboard_renders_today_quiz_with_start_button(browser_page, live_server):
    """Quiz dashboard must render today's QuizSets with a Start (web) button."""
    _seed_quiz_set_for_today(live_server)
    page = browser_page

    response = page.goto(live_server["base_url"] + "/quiz")
    assert response is not None and response.status == 200

    page.wait_for_selector("button.button.primary:has-text('Start')", timeout=10_000)
    assert "correct_option_index" not in page.content()


def test_quiz_dashboard_completes_full_web_attempt(browser_page, live_server):
    """A user can open the quiz page, answer all MCQs, and see the completion score."""
    _seed_quiz_set_for_today(
        live_server,
        drafts=[
            {
                "question_text": "What is 2+2?",
                "options": ["3", "4", "5", "6"],
                "correct_option_index": 1,
                "explanation": "Basic arithmetic.",
            }
        ],
    )
    page = browser_page
    page.goto(live_server["base_url"] + "/quiz")
    page.wait_for_selector("button.button.primary:has-text('Start')", timeout=10_000)
    page.locator("button.button.primary:has-text('Start')").first.click()

    page.wait_for_selector(".options button", timeout=10_000)
    page.locator(".options button").nth(1).click()

    page.wait_for_selector("button:has-text('Finish')", timeout=10_000)
    page.click("button:has-text('Finish')")

    page.wait_for_selector("text=/Score:/", timeout=10_000)
    assert "1 / 1" in page.content()


def test_quiz_dashboard_empty_state_when_no_entries(browser_page, live_server):
    """Empty-state copy must show instead of a broken form when no QuizSets exist."""
    page = browser_page

    response = page.goto(live_server["base_url"] + "/quiz")
    assert response is not None and response.status == 200

    page.wait_for_selector("#quiz-root", timeout=10_000)
    page.wait_for_function(
        "() => document.querySelector('#quiz-status')?.textContent?.toLowerCase()"
        "?.includes('no quizzes')",
        timeout=10_000,
    )


def test_interview_dashboard_page_loads_after_introducing_phase8(browser_page, live_server):
    """SRA-807 regression: interview dashboard page still renders after Phase 8.

    The voice-based interview feature shipped in Phase 8; this test
    catches regressions where the new feature silently fails to render.
    The page must load and must NOT redirect to sign-in.
    """
    page = browser_page
    response = page.goto(live_server["base_url"] + "/dashboard/interview")
    assert response is not None and response.status == 200
    assert "/signin" not in page.url
    body = page.content()
    assert "interview" in body.lower()


def test_interview_dashboard_send_button_completes_browser_turn(
    browser_page, live_server
):
    """The visible Send answer button drives the real interview route."""
    quiz_set_id = _seed_quiz_set_for_today(live_server)
    page = browser_page
    page.goto(
        live_server["base_url"]
        + f"/dashboard/interview?quiz_set_id={quiz_set_id}"
    )
    page.wait_for_selector('[data-panel="answer-input"]', timeout=10_000)

    page.fill(
        '[data-panel="answer-input"]',
        "Mitosis separates replicated chromosomes into two nuclei.",
    )
    page.click('[data-action="send-answer"]')

    page.wait_for_selector('[data-panel="summary"]', timeout=10_000)
    assert "Browser interview completed." in page.content()


def test_interview_dashboard_next_and_end_buttons_send_actions(
    browser_page, live_server
):
    """The remaining visible interview controls send their named actions."""
    quiz_set_id = _seed_quiz_set_for_today(live_server)
    page = browser_page
    actions: list[str] = []

    def record_action(request):
        if request.method == "POST" and request.url.endswith("/interview-turn"):
            actions.append(request.post_data_json["action"])

    page.on("request", record_action)
    page.goto(
        live_server["base_url"]
        + f"/dashboard/interview?quiz_set_id={quiz_set_id}"
    )
    page.wait_for_selector('[data-action="next-question"]', timeout=10_000)

    page.click('[data-action="next-question"]')
    page.wait_for_function("() => document.querySelectorAll('[data-action=\"next-question\"]').length === 1")
    page.click('[data-action="end-interview"]')

    page.wait_for_selector('[data-panel="summary"]', timeout=10_000)
    assert actions == ["next", "end"]


def test_protected_route_blocks_anonymous_user(browser_page, live_server):
    """Anonymous /settings must not render the preferences form."""
    page = browser_page
    page.context.clear_cookies()

    response = page.goto(live_server["base_url"] + "/settings")
    assert response is not None
    assert page.locator("#preferences-form").count() == 0
    assert response.status in (401, 302, 303) or "/signin" in page.url


def test_get_preferences_includes_interview_fields_after_phase8(browser_page, live_server):
    """Phase 8 added two interview fields to the GET response. A regression that
    drops these fields breaks every dashboard tile that renders them."""
    page = browser_page
    response = page.context.request.get(
        live_server["base_url"] + "/api/v1/me/preferences"
    )
    assert response.status == 200
    body = response.json()
    for key in (
        "preferred_review_time",
        "timezone",
        "quiz_question_count",
        "quiz_option_count",
        "interview_question_count",
        "interview_target_minutes",
        "wakeup_notification_sent_at",
    ):
        assert key in body, f"GET /api/v1/me/preferences missing field: {key}"


def test_quiz_pregen_pairs_every_quiz_set_with_interview_plan(browser_page, live_server):
    """SRA-802→SRA-809 regression guard: pregen must still create today's
    QuizSet AND pair it with an InterviewPlan once Phase 8 lands.

    This is exactly the regression the bug report named —
    "voice interview broke quiz pregeneration". A silent planner
    failure (one that returns an empty outline instead of raising)
    would make this assertion fail because no InterviewPlan would
    be persisted for the seeded entry.
    """
    from sra.db.models import (
        InterviewPlan,
        QuizSet,
        ReviewSchedule,
        StudyEntry,
    )
    from sra.db.repository import schedule_reviews_for_entry

    engine = live_server["engine"]
    user_id = live_server["user_id"]

    with Session(engine) as session:
        entry = StudyEntry(
            user_id=user_id,
            topic="Mitosis",
            summary="cell division",
            raw_transcript="t",
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        schedule_reviews_for_entry(entry.id, date.today(), session)
        session.add(
            ReviewSchedule(
                entry_id=entry.id, interval_label="1d",
                scheduled_date=date.today(), status="pending",
            )
        )
        session.commit()
        entry_id = entry.id

    response = live_server["client"].post(
        "/api/v1/me/dev/pregenerate-now?include_upcoming=false",
    )
    assert response.status_code == 200, response.text
    summary = response.json()["summary"]
    assert summary["sets_created"] >= 1, summary

    with Session(engine) as session:
        quiz_set = session.exec(
            select(QuizSet).where(QuizSet.entry_id == entry_id)
        ).first()
        assert quiz_set is not None, "Phase 8 pregen must produce a QuizSet"
        plan = session.exec(
            select(InterviewPlan).where(InterviewPlan.quiz_set_id == quiz_set.id)
        ).first()
        assert plan is not None, (
            "Phase 8 pregen must pair every QuizSet with an InterviewPlan; "
            "this is the regression that broke 'no quiz is being generated for today'."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_quiz_set_for_today(live_server, drafts: list[dict] | None = None) -> int:
    """Insert a QuizSet directly via the live DB engine. Returns the quiz_set_id."""
    from sra.agents.schemas import MCQDraft
    from sra.db.models import StudyEntry, StudyEntryCreate
    from sra.db.repository import create_quiz_set

    drafts = drafts or [
        {
            "question_text": "q0",
            "options": ["a", "b"],
            "correct_option_index": 0,
            "explanation": "",
        }
    ]
    engine = live_server["engine"]
    user_id = live_server["user_id"]

    with Session(engine) as session:
        entry = StudyEntry.model_validate(
            StudyEntryCreate(
                topic="Mitosis", summary="cell division", raw_transcript="t",
            ),
            update={"user_id": user_id},
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        mcq_drafts = [
            MCQDraft(
                question_text=d["question_text"],
                options=d["options"],
                correct_option_index=d["correct_option_index"],
                explanation=d.get("explanation", ""),
            )
            for d in drafts
        ]
        quiz_set = create_quiz_set(
            entry=entry,
            user_id=user_id,
            session=session,
            generated_on=date.today(),
            question_count=len(mcq_drafts),
            option_count=len(drafts[0]["options"]),
            drafts=mcq_drafts,
        )
        assert quiz_set.id is not None
        return quiz_set.id
