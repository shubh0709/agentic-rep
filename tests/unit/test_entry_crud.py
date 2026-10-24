"""SRA-1203 entry-edit API and dashboard/quiz browser contracts."""

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from sra.auth.google_oauth import issue_app_credential
from sra.db.models import StudyEntry, StudyEntryCreate

ROOT = Path(__file__).resolve().parents[2]


def _entry(session, user_id: int, *, topic: str = "Check") -> StudyEntry:
    row = StudyEntry.model_validate(
        StudyEntryCreate(topic=topic, summary="A note", raw_transcript="source"),
        update={"user_id": user_id},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    return row


def _client_for(client, user) -> TestClient:
    assert user.id is not None
    token = issue_app_credential(
        user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    return TestClient(client.app, headers={"Authorization": f"Bearer {token}"})


def test_patch_entry_accepts_strict_partial_topic_update(client, session, seeded_user):
    entry = _entry(session, seeded_user.id)

    response = client.patch(
        f"/api/v1/entries/{entry.id}",
        json={"topic": "  Figure out job crawling  "},
    )

    assert response.status_code == 200
    assert response.json()["topic"] == "Figure out job crawling"
    session.expire_all()
    assert session.get(StudyEntry, entry.id).topic == "Figure out job crawling"


def test_patch_entry_rejects_empty_unknown_and_server_owned_fields(
    client, session, seeded_user
):
    entry = _entry(session, seeded_user.id)

    assert client.patch(f"/api/v1/entries/{entry.id}", json={}).status_code == 422
    assert client.patch(
        f"/api/v1/entries/{entry.id}", json={"topic": "   "}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/entries/{entry.id}", json={"summary": "not editable here"}
    ).status_code == 422
    assert client.patch(
        f"/api/v1/entries/{entry.id}", json={"user_id": seeded_user.id}
    ).status_code == 422


def test_patch_and_delete_distinguish_missing_from_another_users_entry(
    client, session, seeded_user, other_user
):
    theirs = _entry(session, other_user.id, topic="Private")

    assert client.patch(
        f"/api/v1/entries/{theirs.id}", json={"topic": "Stolen"}
    ).status_code == 403
    assert client.delete(f"/api/v1/entries/{theirs.id}").status_code == 403
    assert client.patch(
        "/api/v1/entries/999999", json={"topic": "Missing"}
    ).status_code == 404
    assert client.delete("/api/v1/entries/999999").status_code == 404
    session.expire_all()
    assert session.get(StudyEntry, theirs.id).topic == "Private"


def test_entry_mutations_require_authentication(client, session, seeded_user):
    entry = _entry(session, seeded_user.id)
    anonymous = TestClient(client.app)

    assert anonymous.patch(
        f"/api/v1/entries/{entry.id}", json={"topic": "No"}
    ).status_code == 401
    assert anonymous.delete(f"/api/v1/entries/{entry.id}").status_code == 401


def test_other_user_can_mutate_their_own_entry(client, session, other_user):
    theirs = _entry(session, other_user.id, topic="Before")
    other_client = _client_for(client, other_user)

    updated = other_client.patch(
        f"/api/v1/entries/{theirs.id}", json={"topic": "After"}
    )

    assert updated.status_code == 200
    assert updated.json()["topic"] == "After"


def test_dashboard_source_exposes_clickable_edit_and_per_card_quiz_contract():
    source = (ROOT / "web/static/js/dashboard.js").read_text()

    assert 'data-action: "edit-topic"' in source or 'data-action: "edit-entry"' in source
    assert 'data-action: "delete-entry"' in source
    assert '"Quiz me"' in source
    assert "quiz_set_id=" in source
    assert "PATCH" in source
    assert "DELETE" in source
    assert 'role: "dialog"' in source or 'role="dialog"' in source


def test_quiz_source_honors_requested_quiz_set_and_starts_it_immediately():
    source = (ROOT / "web/static/js/quiz.js").read_text()

    assert "URLSearchParams" in source
    assert "quiz_set_id" in source
    assert "startAttempt" in source
    assert "/today" not in source[source.index("quiz_set_id") : source.index("quiz_set_id") + 500]


def test_visible_interview_me_labels_are_renamed_to_quiz_me():
    dashboard = (ROOT / "web/templates/dashboard.html").read_text()
    nav = (ROOT / "web/templates/_nav.html").read_text()
    assessment = (ROOT / "web/templates/interview_me.html").read_text()

    assert "Interview Me" not in dashboard
    assert "Interview Me" not in nav
    assert "Interview Me" not in assessment
    assert "Quiz me" in dashboard
    assert "Quiz me" in nav
    assert "Quiz me" in assessment


def test_dashboard_css_adds_room_between_major_sections():
    css = (ROOT / "web/static/css/site.css").read_text()

    assert "--dashboard-section-space: clamp(" in css
    assert ".dashboard-section + .dashboard-section" in css
    section_rule = css[css.index(".dashboard-section + .dashboard-section") :][:180]
    assert "var(--dashboard-section-space)" in section_rule
