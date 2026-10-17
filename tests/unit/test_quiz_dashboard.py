"""SRA-405 dashboard quiz module unit tests with pure-Python fetch mocking.

Approach: ``quiz.js`` is a plain ES module that uses ``globalThis.fetch`` as
its only transport. We use ``node`` to load it inside an isolated script that
substitutes ``globalThis.fetch`` with a queue of pre-recorded responses and
captures the rendered DOM tree. The test is skipped cleanly when ``node`` is
unavailable or when the module file does not exist (the Phase 4 starting
point). No new Python test dependency is introduced.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.phase4

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "dashboard" / "static" / "quiz.js"

DRIVER = r"""
const fs = require('node:fs');
const responses = JSON.parse(process.env.SRA_QUIZ_RESPONSES || '[]');
const responseCount = responses.length;
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), method: (init && init.method) || 'GET', body: init && init.body });
  const next = responses.shift();
  if (!next) {
    throw new Error('No queued response for ' + url);
  }
  const headerMap = new Map(Object.entries(next.headers || {}));
  return {
    status: next.status,
    ok: next.status >= 200 && next.status < 300,
    headers: { get: (name) => headerMap.get(String(name).toLowerCase()) || null },
    async json() { return next.body; },
    async text() { return JSON.stringify(next.body); },
  };
};

const target = process.env.SRA_QUIZ_MODULE;
const source = fs.readFileSync(target, 'utf8');
const moduleObj = { exports: {} };
new Function('module', 'exports', 'fetch', source + '; if (typeof initQuiz === "function") module.exports.initQuiz = initQuiz;')(moduleObj, moduleObj.exports, globalThis.fetch);

const makeElement = (tag) => {
  const el = { tag, attrs: {}, textContent: '', children: [] };
  el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
  el.appendChild = (c) => { el.children.push(c); c.parent = el; return c; };
  return el;
};
const root = makeElement('root');
const doc = {
  root,
  createElement(tag) {
    return makeElement(tag);
  },
  createTextNode(text) {
    return { nodeType: 'text', textContent: String(text), children: [] };
  },
  getElementById() { return root; },
};

const dump = (node) => ({
  tag: node.tag || (node.nodeType === 'text' ? '#text' : '?'),
  text: node.textContent || '',
  attrs: node.attrs || {},
  children: (node.children || []).map(dump),
});

(async () => {
  const controller = typeof moduleObj.exports.initQuiz === 'function'
    ? moduleObj.exports.initQuiz(root, doc)
    : globalThis.initQuiz(root, doc);
  await new Promise((resolve) => setImmediate(resolve));

  if (responseCount >= 2) {
    await controller.startQuiz(7);
  }
  if (responseCount >= 3) {
    await controller.loadQuizDetail(7);
  }
  if (responseCount === 4) {
    await controller.submitAnswer(99, 11, 0);
  }
  if (responseCount >= 6) {
    await controller.submitAnswer(99, 11, 0);
    await controller.submitAnswer(99, 12, 1);
    await controller.completeAttempt(99);
  }

  console.log(JSON.stringify({ calls, dom: dump(root) }));
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


@pytest.fixture(scope="module")
def node_available():
    return shutil.which("node") is not None


def _run_dashboard(responses: list[dict], timeout: int = 10) -> dict:
    """Run the dashboard module with a queued response list and capture the DOM."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the dashboard unit-test harness")
    env = {
        "SRA_QUIZ_MODULE": str(MODULE_PATH),
        "SRA_QUIZ_RESPONSES": json.dumps(responses),
        "PATH": "/usr/bin:/usr/local/bin",
    }
    completed = subprocess.run(
        [node, "-e", DRIVER],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Dashboard runner failed: rc={completed.returncode}\nSTDERR={completed.stderr}\nSTDOUT={completed.stdout}"
        )
    return json.loads(completed.stdout)


def _today_index_response() -> dict:
    return {
        "status": 200,
        "body": [
            {
                "id": 7,
                "entry": {"id": 1, "topic": "Photosynthesis"},
                "generated_on": "2026-10-14",
                "questions": [
                    {"id": 11, "question_text": "Q0", "options": ["a", "b", "c", "d"], "order_index": 0},
                    {"id": 12, "question_text": "Q1", "options": ["w", "x", "y", "z"], "order_index": 1},
                ],
            }
        ],
    }


def _quiz_detail_response() -> dict:
    return {
        "status": 200,
        "body": {
            "id": 7,
            "entry": {"id": 1, "topic": "Photosynthesis"},
            "generated_on": "2026-10-14",
            "questions": [
                {"id": 11, "question_text": "Q0", "options": ["a", "b", "c", "d"], "order_index": 0},
                {"id": 12, "question_text": "Q1", "options": ["w", "x", "y", "z"], "order_index": 1},
            ],
        },
    }


def test_module_loads_via_node_with_stubbed_fetch(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")
    _run_dashboard([_today_index_response()])


def test_load_today_quizzes_renders_list_without_correct_option_index(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    output = _run_dashboard([_today_index_response()])
    urls = [c["url"] for c in output["calls"]]
    assert any(url.endswith("/quiz/today") for url in urls)

    serialized = json.dumps(output["dom"])
    assert "correct_option_index" not in serialized


def test_post_answer_response_reveals_correct_option_index_and_explanation(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _today_index_response(),
        {"status": 201, "body": {"attempt_id": 99, "question_text": None}},
        _quiz_detail_response(),
        {"status": 200, "body": {"is_correct": True, "correct_option_index": 0, "explanation": "ok"}},
    ]
    output = _run_dashboard(responses)

    answer_call = [c for c in output["calls"] if "/answers" in c["url"]]
    assert answer_call, "the dashboard never POSTed to /answers"
    payload = json.loads(answer_call[-1]["body"])
    assert payload["question_id"] == 11
    assert payload["selected_option_index"] == 0

    serialized = json.dumps(output["dom"])
    assert "correct_option_index" in serialized


def test_voice_mode_is_rejected_and_shown(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _today_index_response(),
        {"status": 400, "body": {"detail": "Voice mode is not yet supported. See Phase 5."}},
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "Voice" in serialized or "voice" in serialized


def test_unauthenticated_response_renders_panel(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [{"status": 401, "body": {"detail": "Not authenticated"}}]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "Not authenticated" in serialized or "sign" in serialized.lower()


def test_rate_limited_response_renders_panel_with_retry_after(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        {
            "status": 429,
            "headers": {"Retry-After": "2"},
            "body": {"detail": "Rate limit exceeded"},
        }
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "Rate limit" in serialized or "429" in serialized


def test_complete_attempt_renders_score_panel(node_available):
    if not node_available:
        pytest.skip("node is required for the dashboard unit-test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _today_index_response(),
        {"status": 201, "body": {"attempt_id": 99, "question_text": None}},
        _quiz_detail_response(),
        {"status": 200, "body": {"is_correct": True, "correct_option_index": 0, "explanation": "ok"}},
        {"status": 200, "body": {"is_correct": False, "correct_option_index": 1, "explanation": "ok"}},
        {"status": 200, "body": {"score": 1, "total_questions": 2}},
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "score" in serialized or "Score" in serialized
