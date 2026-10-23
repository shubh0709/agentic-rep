"""SRA-604 — structural content checks for ``README.md``.

Documentation tickets are tested through tokenized scans: a regression in
the README (e.g. an accidental deletion during a refactor) shows up here
without anyone having to run the actual docker compose commands.

Phase *status* is owned by ``PROGRESS.md``; the README only points at that
owner, so the Phase 6 assertion lives against ``PROGRESS.md``.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README_PATH = ROOT / "README.md"
PROGRESS_PATH = ROOT / "PROGRESS.md"


@pytest.fixture(scope="module")
def readme_text():
    if not README_PATH.exists():
        pytest.skip("README.md is not present yet")
    return README_PATH.read_text()


@pytest.fixture(scope="module")
def progress_text():
    if not PROGRESS_PATH.exists():
        pytest.skip("PROGRESS.md is not present yet")
    return PROGRESS_PATH.read_text()


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


def test_readme_phase_checklist_marks_phase_6_implemented(readme_text, progress_text):
    """Phase 6 is implemented, and the docs must reflect that.

    ``PROGRESS.md`` owns phase status — the README's build-phases table no
    longer carries a ``Status`` column, so the assertion moved to the owner:
    PROGRESS.md's ``## Status at a glance`` table must record Phase 6 as
    complete. The README must keep pointing readers at that owner, so a
    future refactor that drops the pointer fails here too.
    """
    import re

    # --- the owner: PROGRESS.md's status table -------------------------------
    section = re.search(
        r"^##\s+Status at a glance\s*$(?P<body>.*?)(?=^##\s|\Z)",
        progress_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert section is not None, (
        "PROGRESS.md must keep a '## Status at a glance' section — it is the "
        "owner of phase status"
    )

    rows = []
    for line in section.group("body").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
            continue  # table separator row
        rows.append(cells)

    assert rows, "PROGRESS.md's status section must contain a table"
    header = rows[0]
    status_index = next(
        (i for i, cell in enumerate(header) if cell.lower().startswith("status")),
        None,
    )
    assert status_index is not None, (
        f"PROGRESS.md's status table must keep a 'Status' column; got header {header!r}"
    )

    phase6 = next(
        (cells for cells in rows[1:] if re.match(r"^(?:phase\s*)?6\b", cells[0], re.IGNORECASE)),
        None,
    )
    assert phase6 is not None, (
        "PROGRESS.md's status table must have a Phase 6 row; got phase cells: "
        f"{[cells[0] for cells in rows[1:]]!r}"
    )
    assert len(phase6) > status_index, (
        f"Phase 6 row must have a cell in the Status column; got {phase6!r}"
    )
    status_cell = phase6[status_index]
    assert re.search(
        r"\b(closed|done|implemented|complete|completed|verified)\b",
        status_cell,
        re.IGNORECASE,
    ), f"Phase 6 status must read as implemented (closed/done/implemented); got {status_cell!r}"
    assert not re.search(
        r"\b(not closed|not done|open|in progress|pending|blocked)\b",
        status_cell,
        re.IGNORECASE,
    ), f"Phase 6 status must not read as open/in-progress; got {status_cell!r}"

    # --- the pointer: the README must hand readers to that owner -----------
    build_phases = re.search(
        r"^##\s+Build phases\s*$(?P<body>.*?)(?=^##\s|\Z)",
        readme_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert build_phases is not None, "README must keep a '## Build phases' section"
    assert "PROGRESS.md" in build_phases.group("body"), (
        "README's build-phases section must point at PROGRESS.md, the owner of phase status"
    )
    assert "](PROGRESS.md)" in build_phases.group("body"), (
        "README's build-phases section must link to PROGRESS.md, the owner of phase status"
    )


def test_readme_lists_openai_api_key_as_required_env_var(readme_text):
    assert "OPENAI_API_KEY" in readme_text


def test_readme_references_contributing_for_dev_workflow(readme_text):
    assert "CONTRIBUTING.md" in readme_text