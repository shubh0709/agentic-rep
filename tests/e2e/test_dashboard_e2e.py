"""SRA-701 dashboard end-to-end: real router + auth flow.

Proves the full path a signed-in browser takes when it loads the dashboard:
the SSR shell reaches `/dashboard`, then the static JS fetches `/api/v1/search`,
`/api/v1/digest`, and `/api/v1/reviews/upcoming` through the real router
stack (no TestClient fakes). All three responses must shape-correctly into
what the dashboard module renders.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from sra.db.models import ReviewSchedule, StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase7


def _seed_entry(session, user_id: int, topic: str = "Photosynthesis", summary: str = "light reactions") -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic=topic, summary=summary, raw_transcript="transcript about " + topic),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _seed_review(
    session,
    entry_id: int,
    *,
    interval_label: str,
    scheduled_date: date,
    status: str = "pending",
) -> ReviewSchedule:
    review = ReviewSchedule(
        entry_id=entry_id,
        interval_label=interval_label,
        scheduled_date=scheduled_date,
        status=status,
    )
    session.add(review)
    session.commit()
    session.refresh(review)
    return review


def test_dashboard_page_renders_with_three_data_sections(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    body = response.text
    # All three data sources the JS module reads must have a mount point in the SSR shell.
    assert 'id="entries-root"' in body
    assert 'id="upcoming-root"' in body
    assert 'id="stat-total-entries"' in body
    assert 'id="stat-due-today"' in body
    assert 'id="stat-upcoming-total"' in body
    assert 'id="entry-search"' in body
    assert 'id="upcoming-window"' in body


def test_dashboard_data_endpoints_return_consistent_shapes(client, session, seeded_user):
    """One entry, one due-today review, and two upcoming reviews exercise the full data shape."""
    entry = _seed_entry(session, seeded_user.id)
    assert entry.id is not None
    today = datetime.now(UTC).date()
    _seed_review(session, entry.id, interval_label="1d", scheduled_date=today)
    _seed_review(session, entry.id, interval_label="3d", scheduled_date=today + timedelta(days=2))
    _seed_review(session, entry.id, interval_label="7d", scheduled_date=today + timedelta(days=6))

    entries = client.get("/api/v1/search").json()
    assert len(entries) == 1
    assert entries[0]["topic"] == "Photosynthesis"
    # raw_transcript must be present so the dashboard can expand each card.
    assert entries[0]["raw_transcript"].startswith("transcript about")

    digest = client.get("/api/v1/digest").json()
    assert len(digest) == 1
    assert digest[0]["interval_label"] == "1d"
    assert digest[0]["entry"]["topic"] == "Photosynthesis"

    upcoming = client.get("/api/v1/reviews/upcoming").json()
    assert len(upcoming) == 2  # only 3d and 7d, since today belongs to /digest
    day_dates = {item["date"] for item in upcoming}
    assert (today + timedelta(days=2)).isoformat() in day_dates
    assert (today + timedelta(days=6)).isoformat() in day_dates


def test_dashboard_search_filters_to_one_entry(client, session, seeded_user):
    _seed_entry(session, seeded_user.id, topic="Photosynthesis", summary="light reactions")
    _seed_entry(session, seeded_user.id, topic="Mitosis", summary="cell division")

    all_entries = client.get("/api/v1/search").json()
    assert {e["topic"] for e in all_entries} == {"Photosynthesis", "Mitosis"}

    filtered = client.get("/api/v1/search?q=mitosis").json()
    assert len(filtered) == 1
    assert filtered[0]["topic"] == "Mitosis"


def test_dashboard_navigation_links_include_dashboard(client):
    body = client.get("/").text
    # Landing page CTA must point new users at the dashboard once they're signed in.
    assert 'href="/dashboard"' in body


def test_dashboard_navigation_highlights_dashboard_when_active(client):
    body = client.get("/dashboard").text
    # The active nav link should carry the is-active class so CSS highlights it.
    assert 'href="/dashboard"' in body
    assert "is-active" in body