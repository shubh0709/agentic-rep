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

from datetime import UTC, date, datetime, timedelta
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


def _install_voice_fake(page):
    """Replace only the browser-owned speech output boundary."""
    page.add_init_script(
        """(() => {
          window.__spoken = JSON.parse(sessionStorage.getItem('__spoken') || '[]');
          window.__speechCancelCount = 0;
          class FakeUtterance {
            constructor(text) { this.text = String(text); }
          }
          Object.defineProperty(window, "SpeechSynthesisUtterance", {
            configurable: true,
            value: FakeUtterance,
          });
          Object.defineProperty(window, "speechSynthesis", {
            configurable: true,
            value: {
              speak(utterance) {
                window.__spoken.push(utterance.text);
                sessionStorage.setItem('__spoken', JSON.stringify(window.__spoken));
              },
              cancel() { window.__speechCancelCount += 1; },
              getVoices() { return []; },
            },
          });
        })()"""
    )


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
        """(() => {
          class FakeSpeechRecognition {
            start() {}
            stop() { queueMicrotask(() => this.onend && this.onend()); }
          }
          window.SpeechRecognition = FakeSpeechRecognition;
          window.webkitSpeechRecognition = FakeSpeechRecognition;
        })()"""
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


def test_landing_voice_capture_replaces_live_results_without_repeating_text(
    browser_page, live_server
):
    """SRA-910: successive Web Speech events render from one stable baseline.

    Chromium does not provide its speech recognizer in headless tests, so this
    replaces only that browser-owned boundary.  The page script, event handler,
    textarea rendering, stop lifecycle, and subsequent recording session remain
    real.
    """
    page = browser_page
    page.add_init_script(
        """(() => {
          class FakeSpeechRecognition {
            start() { window.__speechRecognition = this; }
            stop() { queueMicrotask(() => this.onend && this.onend()); }
            emit(parts, resultIndex = 0) {
              const results = parts.map(({ transcript, isFinal }) => {
                const result = [{ transcript }];
                result.isFinal = isFinal;
                return result;
              });
              this.onresult({ resultIndex, results });
            }
          }
          window.SpeechRecognition = FakeSpeechRecognition;
          window.webkitSpeechRecognition = FakeSpeechRecognition;
        })()"""
    )
    page.goto(live_server["base_url"] + "/")
    textarea = page.locator("#capture-transcript")
    textarea.fill("Typed context.")

    page.click("#capture-record")
    page.evaluate(
        "() => window.__speechRecognition.emit([{transcript: 'There is', isFinal: false}])"
    )
    assert textarea.input_value() == "Typed context. There is"

    page.evaluate(
        "() => window.__speechRecognition.emit([{transcript: 'There is a bug', isFinal: false}])"
    )
    assert textarea.input_value() == "Typed context. There is a bug"

    page.evaluate(
        "() => window.__speechRecognition.emit([{transcript: 'There is a bug', isFinal: true}])"
    )
    page.evaluate(
        "() => window.__speechRecognition.emit([{transcript: 'There is a bug', isFinal: true}])"
    )
    assert textarea.input_value() == "Typed context. There is a bug"

    page.evaluate(
        """() => window.__speechRecognition.emit([
          {transcript: 'There is a bug', isFinal: true},
          {transcript: 'in the transcript', isFinal: false},
        ], 1)"""
    )
    assert textarea.input_value() == "Typed context. There is a bug in the transcript"
    page.evaluate(
        """() => window.__speechRecognition.emit([
          {transcript: 'There is a bug', isFinal: true},
          {transcript: 'in the transcript', isFinal: true},
        ], 1)"""
    )

    page.click("#capture-record")
    page.wait_for_function(
        "() => document.querySelector('#capture-record')?.getAttribute('aria-pressed') === 'false'"
    )
    assert textarea.input_value() == "Typed context. There is a bug in the transcript"

    page.click("#capture-record")
    page.evaluate(
        "() => window.__speechRecognition.emit([{transcript: 'Second thought', isFinal: true}])"
    )
    page.click("#capture-record")
    page.wait_for_function(
        "() => document.querySelector('#capture-record')?.getAttribute('aria-pressed') === 'false'"
    )
    assert textarea.input_value() == (
        "Typed context. There is a bug in the transcript Second thought"
    )


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


def test_dashboard_is_a_today_workspace_with_daily_inspiration_and_primary_action(
    browser_page, live_server
):
    page = browser_page
    _install_voice_fake(page)

    response = page.goto(live_server["base_url"] + "/dashboard")
    assert response is not None and response.status == 200

    assert page.get_by_role("heading", name="Today", exact=True).count() == 1
    assert "Playwright" in (
        page.locator('[data-field="daily-greeting"]').text_content() or ""
    )
    primary = page.locator('[data-action="start-review"]')
    assert primary.count() == 1
    assert primary.is_visible()

    inspiration = page.locator('[data-panel="daily-inspiration"]')
    assert inspiration.count() == 1
    for field in ("quote", "attribution", "meaning"):
        text = (
            inspiration.locator(f'[data-field="{field}"]').text_content() or ""
        ).strip()
        assert text, f"Daily inspiration is missing {field}"


def test_enabled_voice_greets_by_first_name_once_and_controls_replay_and_stop(
    browser_page, live_server
):
    page = browser_page
    _install_voice_fake(page)
    page.goto(live_server["base_url"] + "/dashboard")
    page.wait_for_function("() => window.__spoken.length === 1")

    first = page.evaluate("() => window.__spoken[0]")
    assert "Hello Playwright" in first
    assert "wonderful day" in first.lower()

    page.goto(live_server["base_url"] + "/quiz")
    page.wait_for_timeout(100)
    assert page.evaluate("() => window.__spoken.length") == 1

    page.click('[data-action="play-welcome"]')
    page.wait_for_function("() => window.__spoken.length === 2")
    page.click('[data-action="stop-voice"]')
    assert page.evaluate("() => window.__speechCancelCount") >= 1


def test_voice_unavailable_keeps_today_usable_and_reports_silent_fallback(
    browser_page, live_server
):
    page = browser_page
    page.add_init_script(
        """(() => {
          Object.defineProperty(window, "speechSynthesis", {
            configurable: true, value: undefined,
          });
          Object.defineProperty(window, "SpeechSynthesisUtterance", {
            configurable: true, value: undefined,
          });
        })()"""
    )

    response = page.goto(live_server["base_url"] + "/dashboard")
    assert response is not None and response.status == 200
    assert page.locator('[data-action="start-review"]').is_visible()
    status = (page.locator("[data-voice-status]").text_content() or "").lower()
    assert "not available" in status


def test_calm_motion_is_short_and_reduced_motion_makes_it_effectively_instant(
    browser_page, live_server
):
    page = browser_page
    _install_voice_fake(page)
    page.goto(live_server["base_url"] + "/dashboard")
    panel = page.locator('[data-panel="daily-inspiration"]')

    normal = panel.evaluate(
        """element => {
          const style = getComputedStyle(element);
          return {
            properties: style.transitionProperty.split(',').map(v => v.trim()),
            durations: style.transitionDuration.split(',').map(v => parseFloat(v) || 0),
          };
        }"""
    )
    assert set(normal["properties"]) <= {"opacity", "transform"}
    assert normal["durations"] and 0 < max(normal["durations"]) <= 0.24

    page.emulate_media(reduced_motion="reduce")
    reduced = panel.evaluate(
        """element => {
          const style = getComputedStyle(element);
          return Math.max(
            ...style.transitionDuration.split(',').map(v => parseFloat(v) || 0),
            ...style.animationDuration.split(',').map(v => parseFloat(v) || 0)
          );
        }"""
    )
    assert reduced <= 0.01


def _seed_topic_shelf(live_server):
    """Create exact duplicates, similar names, and representative review ranges."""
    from sra.db.models import ReviewSchedule, StudyEntry

    now = datetime.now(UTC)
    today = date.today()
    definitions = [
        (
            "  Journey   of Request to Backend ",
            "Request lifecycle",
            ["backend", "learning"],
            "Older journey summary",
            "older original journey transcript",
            now - timedelta(days=1),
        ),
        (
            "journey of request to backend",
            "HTTP",
            ["backend", "http"],
            "Newest journey summary",
            "newer original journey transcript",
            now,
        ),
        (
            "RAG retrieval and storage",
            None,
            ["rag"],
            "RAG summary",
            "rag original transcript",
            now - timedelta(days=2),
        ),
        (
            "Retrieval and Storage",
            None,
            ["storage"],
            "Storage summary",
            "storage original transcript",
            now - timedelta(days=3),
        ),
        (
            "Quiet practice",
            None,
            ["habit"],
            "No review is scheduled",
            "quiet original transcript",
            now - timedelta(days=4),
        ),
    ]
    with Session(live_server["engine"]) as session:
        entries = []
        for topic, subtopic, tags, summary, transcript, created_at in definitions:
            entry = StudyEntry(
                user_id=live_server["user_id"],
                topic=topic,
                subtopic=subtopic,
                tags=tags,
                summary=summary,
                raw_transcript=transcript,
                created_at=created_at,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            entries.append(entry)

        reviews = [
            (entries[0], "1d", today),
            (entries[1], "1d", today),
            (entries[0], "3d", today + timedelta(days=3)),
            (entries[1], "3d", today + timedelta(days=3)),
            (entries[2], "1mo", today + timedelta(days=10)),
            (entries[3], "3mo", today + timedelta(days=40)),
        ]
        for entry, label, scheduled in reviews:
            session.add(
                ReviewSchedule(
                    entry_id=entry.id,
                    interval_label=label,
                    scheduled_date=scheduled,
                    status="pending",
                )
            )
        session.commit()


def _wait_for_topic_shelf(page):
    page.wait_for_function(
        "() => !document.querySelector('#entries-status')?.textContent?.includes('Loading')"
    )
    shelf = page.locator('[data-panel="topic-shelf"]')
    assert shelf.count() == 1, "SRA-1202 must render the topic shelf"
    page.wait_for_selector('[data-panel="topic-shelf"] .topic-card')
    return shelf


def test_topic_shelf_groups_exact_duplicates_and_preserves_distinct_notes(
    browser_page, live_server
):
    _seed_topic_shelf(live_server)
    page = browser_page
    page.goto(live_server["base_url"] + "/dashboard")
    _wait_for_topic_shelf(page)

    cards = page.locator('[data-panel="topic-shelf"] .topic-card')
    assert cards.count() == 4
    journey = page.locator(
        '.topic-card[data-topic-key="journey of request to backend"]'
    )
    assert journey.count() == 1
    assert "2 notes" in (journey.locator('[data-field="note-count"]').text_content() or "")
    assert "Newest journey summary" in (journey.text_content() or "")

    assert page.locator('.topic-card[data-topic-key="rag retrieval and storage"]').count() == 1
    assert page.locator('.topic-card[data-topic-key="retrieval and storage"]').count() == 1

    chips = journey.locator(".review-date-chip")
    assert chips.count() == 2
    assert chips.nth(0).get_attribute("data-count") == "2"
    assert chips.nth(1).get_attribute("data-count") == "2"

    journey.locator('[data-action="open-notes"]').click()
    notes = journey.locator(".topic-note")
    assert notes.count() == 2
    assert "newer original journey transcript" in (journey.text_content() or "")
    assert "older original journey transcript" in (journey.text_content() or "")
    assert journey.locator('[data-action="regenerate"]').count() == 2


def test_topic_toolbar_search_filters_and_sorts_locally_without_refetching(
    browser_page, live_server
):
    _seed_topic_shelf(live_server)
    page = browser_page
    search_requests: list[str] = []
    page.on(
        "request",
        lambda request: search_requests.append(request.url)
        if "/api/v1/search" in request.url
        else None,
    )
    page.goto(live_server["base_url"] + "/dashboard")
    _wait_for_topic_shelf(page)
    initial_requests = len(search_requests)

    page.fill("#entry-search", "learning")
    page.wait_for_function(
        "() => document.querySelectorAll('[data-panel=\"topic-shelf\"] .topic-card').length === 1"
    )
    assert "journey of request" in (
        page.locator('[data-panel="topic-shelf"] .topic-card').text_content() or ""
    ).lower()
    page.wait_for_timeout(300)
    assert len(search_requests) == initial_requests

    page.fill("#entry-search", "")
    page.click('[data-filter="none"]')
    page.wait_for_function(
        "() => document.querySelectorAll('[data-panel=\"topic-shelf\"] .topic-card').length === 1"
    )
    assert "Quiet practice" in (
        page.locator('[data-panel="topic-shelf"] .topic-card').text_content() or ""
    )

    page.click('[data-filter="all"]')
    page.select_option("#topic-sort", "az")
    headings = page.locator('[data-panel="topic-shelf"] .topic-card h3')
    assert headings.count() == 4
    assert (headings.nth(0).text_content() or "").strip().lower().startswith("journey")


def test_shelf_controls_keyboard_position_and_review_horizon_filter(
    browser_page, live_server
):
    _seed_topic_shelf(live_server)
    page = browser_page
    page.goto(live_server["base_url"] + "/dashboard")
    shelf = _wait_for_topic_shelf(page)

    position = page.locator("[data-shelf-position]")
    assert "1 of 4" in (position.text_content() or "")
    assert page.locator('[data-action="shelf-prev"]').is_disabled()
    page.click('[data-action="shelf-next"]')
    page.wait_for_function(
        "() => document.querySelector('[data-shelf-position]')?.textContent?.includes('2 of 4')"
    )

    shelf.focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_function(
        "() => document.querySelector('[data-shelf-position]')?.textContent?.includes('3 of 4')"
    )

    assert page.locator('[data-horizon="ready"] [data-field="count"]').text_content() == "1"
    assert page.locator('[data-horizon="next7"] [data-field="count"]').text_content() == "0"
    assert page.locator('[data-horizon="next30"] [data-field="count"]').text_content() == "1"
    assert page.locator('[data-horizon="later"] [data-field="count"]').text_content() == "1"

    page.click('[data-horizon="next30"]')
    page.wait_for_function(
        "() => document.querySelectorAll('[data-panel=\"topic-shelf\"] .topic-card').length === 1"
    )
    assert "RAG retrieval and storage" in (shelf.text_content() or "")


def test_topic_shelf_has_mobile_peek_scroll_snap_and_no_document_overflow(
    browser_page, live_server
):
    _seed_topic_shelf(live_server)
    page = browser_page
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(live_server["base_url"] + "/dashboard")
    _wait_for_topic_shelf(page)

    geometry = page.evaluate(
        """() => {
          const shelf = document.querySelector('[data-panel="topic-shelf"]');
          const card = shelf.querySelector('.topic-card');
          const style = getComputedStyle(shelf);
          return {
            innerWidth: window.innerWidth,
            documentWidth: document.documentElement.scrollWidth,
            shelfClientWidth: shelf.clientWidth,
            shelfScrollWidth: shelf.scrollWidth,
            cardWidth: card.getBoundingClientRect().width,
            snapType: style.scrollSnapType,
          };
        }"""
    )
    assert geometry["documentWidth"] <= geometry["innerWidth"]
    assert geometry["shelfScrollWidth"] > geometry["shelfClientWidth"]
    assert "x" in geometry["snapType"]
    assert geometry["innerWidth"] * 0.72 <= geometry["cardWidth"] <= geometry["innerWidth"] * 0.94


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
    quiz_href = card.locator('a:has-text("Quiz me")').get_attribute("href")
    interview_href = card.locator('a:has-text("Start interview")').get_attribute("href")
    assert quiz_href is not None
    parsed_quiz = urlparse(quiz_href)
    assert parsed_quiz.path == "/quiz"
    assert interview_href is not None
    parsed_interview = urlparse(interview_href)
    assert parsed_interview.path == "/dashboard/interview"
    quiz_ids = parse_qs(parsed_interview.query).get("quiz_set_id", [])
    assert len(quiz_ids) == 1 and quiz_ids[0].isdigit()
    assert parse_qs(parsed_quiz.query).get("quiz_set_id") == quiz_ids
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
    page.select_option("#timezone", "America/New_York")
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


def test_settings_audio_toggle_persists_and_disabling_cancels_speech(
    browser_page, live_server
):
    page = browser_page
    _install_voice_fake(page)
    page.goto(live_server["base_url"] + "/settings")
    toggle = page.locator("#audio_enabled")
    assert toggle.count() == 1
    assert toggle.is_checked()

    toggle.uncheck()
    page.click("button:has-text('Save preferences')")
    page.wait_for_function(
        "() => document.querySelector('#preferences-status')?.textContent?.toLowerCase().includes('saved')"
    )
    assert page.evaluate("() => window.__speechCancelCount") >= 1

    confirm = page.context.request.get(
        live_server["base_url"] + "/api/v1/me/preferences"
    )
    assert confirm.status == 200
    assert confirm.json()["audio_enabled"] is False

    page.evaluate("() => { window.__spoken = []; sessionStorage.setItem('__spoken', '[]'); }")
    page.goto(live_server["base_url"] + "/dashboard")
    page.wait_for_timeout(150)
    assert page.evaluate("() => window.__spoken.length") == 0


def test_settings_timezone_can_be_selected_saved_and_reloaded(
    browser_page, live_server
):
    """SRA-1204 keeps the selected IANA timezone across the real page/API path."""
    page = browser_page
    page.goto(live_server["base_url"] + "/settings")

    timezone = page.locator("#timezone")
    assert timezone.evaluate("el => el.tagName") == "SELECT"
    page.select_option("#timezone", "Asia/Kolkata")
    page.click("button:has-text('Save preferences')")
    page.wait_for_function(
        "() => document.querySelector('#preferences-status')?.textContent?.toLowerCase().includes('saved')"
    )

    page.reload()
    assert page.locator("#timezone").input_value() == "Asia/Kolkata"


def test_settings_page_surfaces_validation_error_inline(browser_page, live_server):
    """Server-side validation errors must render in the status span, not silently fail."""
    page = browser_page
    page.goto(live_server["base_url"] + "/settings")
    page.wait_for_selector("#preferences-form")

    page.fill("#preferred_review_time", "08:15")
    page.locator("#timezone").evaluate(
        "el => el.add(new Option('IST', 'IST'))"
    )
    page.select_option("#timezone", "IST")  # force an invalid API payload
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
    page.select_option("#timezone", "UTC")
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


def test_quiz_completion_dispatches_shared_study_event_and_speaks_encouragement(
    browser_page, live_server
):
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
    _install_voice_fake(page)
    page.goto(live_server["base_url"] + "/quiz")
    page.wait_for_function("() => window.__spoken.length === 1")
    page.evaluate(
        """() => {
          window.__studyEvents = [];
          window.addEventListener('study:completed', event => {
            window.__studyEvents.push(event.detail);
          });
          window.__spoken = [];
        }"""
    )

    page.locator("button.button.primary:has-text('Start')").first.click()
    page.wait_for_selector(".options button")
    page.locator(".options button").nth(1).click()
    page.click("button:has-text('Finish')")
    page.wait_for_selector("text=/Score:/")
    page.wait_for_function("() => window.__studyEvents.length === 1")

    detail = page.evaluate("() => window.__studyEvents[0]")
    assert detail["kind"] == "quiz"
    spoken = page.evaluate("() => window.__spoken")
    assert len(spoken) == 1
    assert any(word in spoken[0].lower() for word in ("complete", "well done", "wonderful"))


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
