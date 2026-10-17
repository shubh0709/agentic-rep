"""SRA-902 — Dashboard template DOM contract tests (phase9).

Phase 9: pins the vanilla-DOM contract the SRA-902 "Create topic" modal
controller relies on. The dashboard SSR template must:

  * expose a ``data-action="create-topic"`` button (wired by the JS module),
  * include the human-visible ``Create topic`` text,
  * continue to load ``/static/js/dashboard.js`` as its module entry point,
  * not leak any server-side secret (e.g. ``OPENAI_API_KEY``) into the HTML,
  * not leak the server-side ``correct_option_index`` (Phase 4 invariant --
    the dashboard quiz module already pins this; SRA-902 must keep it clean).

The first two assertions fail today because the template predates the SRA-902
affordance; the remaining three are already green and stay green as a
regression guard for the implementer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.phase9

TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "web" / "templates" / "dashboard.html"
)
DASHBOARD_JS_PATH = (
    Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "dashboard.js"
)


def _read_template() -> str:
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def _read_dashboard_js() -> str:
    return DASHBOARD_JS_PATH.read_text(encoding="utf-8")


def _iife_number_at(source: str, offset: int) -> int:
    """Return the ordinal of the IIFE containing ``offset``.

    ``dashboard.js`` deliberately uses simple, non-nested IIFEs, so this
    small source contract can catch a cross-closure call without requiring a
    Node executable in the default Python test environment.
    """
    starts = [
        index
        for index in range(len(source))
        if source.startswith("(function () {", index)
    ]
    ends = [
        index
        for index in range(len(source))
        if source.startswith("})();", index)
    ]
    for ordinal, (start, end) in enumerate(zip(starts, ends, strict=True)):
        if start <= offset <= end:
            return ordinal
    raise AssertionError(f"Offset {offset} is not inside a dashboard IIFE")


def _function_source(source: str, name: str, next_name: str | None) -> str:
    start = source.index(f"function {name}(")
    end = (
        source.index(f"function {next_name}(", start)
        if next_name is not None
        else source.index("})();", start)
    )
    return source[start:end]


# ---------------------------------------------------------------------------
# RED: template must carry the create-topic affordance for SRA-902
# ---------------------------------------------------------------------------


def test_dashboard_template_contains_create_topic_text():
    """The dashboard template must reference 'Create topic' so the button is findable.

    The SRA-902 modal controller opens when the user clicks a button whose
    visible label is "Create topic". The SSR template must render that text
    so screen readers and server-rendered crawlers can find it.
    """
    template = _read_template()
    assert "Create topic" in template, (
        "Expected dashboard.html to include the visible 'Create topic' text. "
        "SRA-902 must add the data-action='create-topic' button at the top "
        "of the entries section."
    )


def test_dashboard_template_has_create_topic_data_action():
    """The dashboard template must include a ``data-action="create-topic"`` button.

    This is the exact selector the vanilla-JS controller binds to in
    ``web/static/js/dashboard.js`` (SRA-902). Without the attribute the
    controller has nothing to wire up.
    """
    template = _read_template()
    assert 'data-action="create-topic"' in template, (
        "Expected dashboard.html to contain data-action=\"create-topic\" so "
        "the SRA-902 JS controller can open the create-topic modal."
    )


# ---------------------------------------------------------------------------
# GREEN: regression guards for the implementer
# ---------------------------------------------------------------------------


def test_dashboard_template_loads_dashboard_js_module():
    """The template must keep wiring the ``web/static/js/dashboard.js`` module.

    SRA-902 extends that module with modal logic; the implementer must not
    accidentally point the template at a different path or drop the script
    tag during the dashboard refactor.
    """
    template = _read_template()
    assert "dashboard.js" in template, (
        "Expected dashboard.html to keep loading the dashboard.js module. "
        "SRA-902 should extend, not replace, the script wiring."
    )


def test_dashboard_template_does_not_leak_openai_api_key():
    """The SSR template must not embed any API key or settings secret.

    The dashboard template is served to the browser; a stray
    ``OPENAI_API_KEY`` reference would leak the model provider's credential.
    The grep below is deliberately broad (``sk-`` is the OpenAI key prefix
    and ``OPENAI_API_KEY`` is the env var name).
    """
    template = _read_template()
    assert "sk-" not in template, "Found 'sk-' substring in dashboard.html (API key leak?)"
    assert "OPENAI_API_KEY" not in template, (
        "Found 'OPENAI_API_KEY' substring in dashboard.html -- the template "
        "must never embed server-side secrets."
    )


def test_dashboard_template_does_not_leak_correct_option_index():
    """The SSR template must not reference ``correct_option_index``.

    Phase 4 invariant: the answer key stays server-side; the dashboard quiz
    module reveals it only via the ``POST /answers`` response. SRA-902 must
    not regress this contract.
    """
    template = _read_template()
    assert "correct_option_index" not in template, (
        "dashboard.html must not embed the server-side answer key. The "
        "correct_option_index field belongs to the /answers response shape."
    )


# ---------------------------------------------------------------------------
# SRA-906: dashboard module startup and modal lifecycle contracts
# ---------------------------------------------------------------------------


def test_create_topic_initializer_is_in_the_same_lexical_scope_as_its_call():
    """Module evaluation must not abort with ``initCreateTopicModal`` undefined."""
    source = _read_dashboard_js()
    call = source.index("initCreateTopicModal(createTopicButton)")
    definition = source.index("function initCreateTopicModal(")

    assert _iife_number_at(source, call) == _iife_number_at(source, definition), (
        "dashboard.js calls initCreateTopicModal from a different IIFE than its "
        "definition, so module startup raises ReferenceError before any dashboard "
        "reads or controls can run"
    )


def test_create_topic_trigger_binds_one_open_handler_for_all_reopens():
    """Opening the cached modal repeatedly must not accumulate its handlers."""
    source = _read_dashboard_js()
    initializer = _function_source(source, "initCreateTopicModal", "buildModal")

    assert initializer.count('trigger.addEventListener("click"') == 1, (
        "initCreateTopicModal must install one trigger handler and bind the modal's "
        "close/add/submit handlers once when the modal is built"
    )
    assert '{ once: false }' not in initializer


def test_generated_actions_use_mounted_pages_and_quiz_id_for_interview():
    """Cards must not construct unmounted ``/quiz/{id}`` or ``/interview/{id}`` URLs."""
    source = _read_dashboard_js()

    assert 'href: "/quiz/" +' not in source
    assert 'href: "/interview/" +' not in source
    assert "/dashboard/interview?quiz_set_id=" in source, (
        "Interview actions must open the mounted /dashboard/interview page and "
        "pass quiz_set_id, which interview.js uses to start the attempt"
    )


@pytest.mark.parametrize(
    ("function_name", "next_function"),
    [("onSubmit", "readFileAsDataURL"), ("onRegenerate", None)],
)
def test_network_actions_restore_controls_in_finally(function_name, next_function):
    """A rejected fetch must not leave create/regenerate controls disabled."""
    source = _read_dashboard_js()
    function = _function_source(source, function_name, next_function)

    assert "try {" in function and "finally" in function, (
        f"{function_name} must guard its fetch with try/finally so a rejected "
        "request restores the button and leaves a visible inline error"
    )
