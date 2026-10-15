"""SRA-604 — structural content checks for ``.env.example``.

Every Phase 6 environment variable must appear in the example file with a
default value, so a new self-hoster never has to read the Settings class
to discover what knobs exist.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE_PATH = ROOT / ".env.example"


@pytest.fixture(scope="module")
def env_example_text():
    if not ENV_EXAMPLE_PATH.exists():
        pytest.skip(".env.example is not present yet")
    return ENV_EXAMPLE_PATH.read_text()


def test_env_example_documents_demo_mode_variable(env_example_text):
    assert "DEMO_MODE" in env_example_text


def test_env_example_documents_demo_data_retention_days(env_example_text):
    assert "DEMO_DATA_RETENTION_DAYS" in env_example_text


def test_env_example_documents_demo_reset_interval_s(env_example_text):
    assert "DEMO_RESET_INTERVAL_S" in env_example_text


def test_env_example_demo_mode_default_is_false(env_example_text):
    """The documented default for ``DEMO_MODE`` is ``false`` so a default git clone is not in demo mode."""
    import re

    # Match the line (allowing optional leading whitespace and a ``#`` comment) and
    # confirm the value is exactly ``false``.
    match = re.search(r"^[^\n]*DEMO_MODE\s*=\s*(\S+)", env_example_text, flags=re.MULTILINE)
    assert match is not None, "DEMO_MODE must be declared in .env.example"
    assert match.group(1).lower() == "false", (
        f"DEMO_MODE default must be false (off by default); got {match.group(1)!r}"
    )


def test_env_example_demo_data_retention_days_default_is_seven(env_example_text):
    import re

    match = re.search(
        r"^[^\n]*DEMO_DATA_RETENTION_DAYS\s*=\s*(\S+)",
        env_example_text,
        flags=re.MULTILINE,
    )
    assert match is not None, "DEMO_DATA_RETENTION_DAYS must be declared in .env.example"
    assert match.group(1) == "7", (
        f"DEMO_DATA_RETENTION_DAYS default must be 7; got {match.group(1)!r}"
    )


def test_env_example_demo_reset_interval_s_default_is_3600(env_example_text):
    import re

    match = re.search(
        r"^[^\n]*DEMO_RESET_INTERVAL_S\s*=\s*(\S+)",
        env_example_text,
        flags=re.MULTILINE,
    )
    assert match is not None, "DEMO_RESET_INTERVAL_S must be declared in .env.example"
    assert match.group(1) == "3600", (
        f"DEMO_RESET_INTERVAL_S default must be 3600; got {match.group(1)!r}"
    )