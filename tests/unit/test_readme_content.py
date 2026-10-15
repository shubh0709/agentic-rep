"""SRA-604 — structural content checks for ``README.md``.

Documentation tickets are tested through tokenized scans: a regression in
the README (e.g. an accidental deletion during a refactor) shows up here
without anyone having to run the actual docker compose commands.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"


@pytest.fixture(scope="module")
def readme_text():
    if not README_PATH.exists():
        pytest.skip("README.md is not present yet")
    return README_PATH.read_text()


def test_readme_contains_public_demo_url_placeholder(readme_text):
    """The top of the README must surface a public-demo URL placeholder."""
    lowered = readme_text.lower()
    assert "demo.spaced-repetition-agent.example" in lowered or "demo.sra.example" in lowered


def test_readme_documents_self_host_docker_quickstart(readme_text):
    """The README must show the canonical ``docker compose up`` self-host path."""
    assert "docker compose up" in readme_text


def test_readme_documents_demo_profile_command(readme_text):
    """The README must show ``docker compose --profile demo up`` for the demo path."""
    assert "docker compose --profile demo up" in readme_text


def test_readme_documents_google_oauth_setup_section(readme_text):
    """The README must contain a Google OAuth setup section / guidance."""
    lowered = readme_text.lower()
    assert "google oauth" in lowered or "google cloud" in lowered


def test_readme_documents_oauth_callback_url(readme_text):
    """The README must show the canonical Google callback path."""
    assert "/api/v1/auth/google/callback" in readme_text


def test_readme_environment_variable_table_mentions_demo_mode(readme_text):
    """The env-var table must list ``DEMO_MODE`` so a new self-hoster knows how to enable demo mode."""
    assert "DEMO_MODE" in readme_text


def test_readme_environment_variable_table_mentions_retention_days(readme_text):
    assert "DEMO_DATA_RETENTION_DAYS" in readme_text


def test_readme_environment_variable_table_mentions_reset_interval(readme_text):
    assert "DEMO_RESET_INTERVAL_S" in readme_text


def test_readme_phase_checklist_marks_phase_6_implemented(readme_text):
    """The phase checklist's Phase 6 row must read as implemented."""
    # Look for the Phase 6 line; the bracket checkbox should be checked.
    import re

    match = re.search(r"Phase 6[^\n]*", readme_text)
    assert match is not None, "README must mention Phase 6 in the build-phases list"
    phase6_line = match.group(0)
    assert "[x]" in phase6_line.lower() or "[X]" in phase6_line, (
        f"Phase 6 row should be checked; got {phase6_line!r}"
    )


def test_readme_lists_openai_api_key_as_required_env_var(readme_text):
    assert "OPENAI_API_KEY" in readme_text


def test_readme_references_contributing_for_dev_workflow(readme_text):
    assert "CONTRIBUTING.md" in readme_text