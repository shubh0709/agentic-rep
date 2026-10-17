"""SRA-903: tiny shim so tests can monkey-patch ``openai_verify_mcqs`` via the
dotted path ``sra.config.settings.openai_verify_mcqs``.

The pytest monkeypatch fixture in
``tests/unit/test_quiz_graph_phase9.py::test_verify_pass_skipped_when_openai_verify_mcqs_false``
calls ``monkeypatch.setattr("sra.config.settings.openai_verify_mcqs", False, raising=False)``
as a defensive fallback. With ``raising=False`` the fixture still tries to
import the dotted path ``sra.config.settings``; without this shim that import
fails with ``ImportError`` and the test fails before it gets to the real
class-attribute patch on ``_Settings.openai_verify_mcqs``.

This module re-exports the field's default as a module-level attribute so the
dotted path resolves cleanly. ``sra.agents.quiz_graph._verify_gate_enabled``
reads this module attribute first; production env-driven toggles
(``OPENAI_VERIFY_MCQS=false``) flow through the cached ``Settings`` instance
and override the module default whenever the instance value differs from it.

The shim also mirrors the field default onto the ``Settings`` *class* so
``monkeypatch.setattr(_Settings, "openai_verify_mcqs", False)`` works --
Pydantic v2 hides field defaults behind ``model_fields`` and would otherwise
raise ``AttributeError`` on a bare ``setattr`` of the field name. The class
attribute is a no-op for already-constructed ``Settings`` instances
(Pydantic still consults its own field descriptor) but gives the pytest
"oldval = getattr(...)" probe a real attribute to read.
"""

from sra.config import Settings

openai_verify_mcqs: bool = bool(Settings.model_fields["openai_verify_mcqs"].default)
Settings.openai_verify_mcqs = openai_verify_mcqs

__all__ = ["openai_verify_mcqs"]