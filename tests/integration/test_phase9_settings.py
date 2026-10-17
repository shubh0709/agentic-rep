# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false
"""SRA-903 — ``Settings.openai_verify_mcqs`` setting.

  * Default is ``True``.
  * Validator refuses ``openai_verify_mcqs=True`` when ``openai_api_key``
    is empty, raising a clear ``ValueError``.

Both tests fail today because ``Settings.openai_verify_mcqs`` is not
declared.
"""

from __future__ import annotations

import pytest

from sra.config import Settings, get_settings

pytestmark = pytest.mark.phase9


def test_settings_exposes_openai_verify_mcqs_default_true(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.openai_verify_mcqs is True


def test_settings_validator_rejects_openai_verify_mcqs_with_empty_api_key(monkeypatch):
    """When ``OPENAI_API_KEY`` is empty/blank and the verify gate is on,
    ``Settings()`` must raise a clear ``ValueError``.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "true")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        Settings()  # pyright: ignore[reportCallIssue]


def test_settings_openai_verify_mcqs_can_be_disabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_VERIFY_MCQS", "false")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.openai_verify_mcqs is False
