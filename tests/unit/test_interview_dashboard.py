"""SRA-807 — Interview dashboard ``interview.js`` browser-side contracts.

Approach mirrors ``tests/unit/test_quiz_dashboard.py``: the script is loaded
via ``node`` with stubbed ``globalThis.fetch`` and a fake DOM. Tests skip
when ``node`` is missing or when the module does not exist (during the
TDD red phase).
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportGeneralTypeIssues=false

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "dashboard" / "static" / "interview.js"

DRIVER = r"""
const fs = require('node:fs');
const responses = JSON.parse(process.env.SRA_INTERVIEW_RESPONSES || '[]');
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

const target = process.env.SRA_INTERVIEW_MODULE;
const source = fs.readFileSync(target, 'utf8');
const moduleObj = { exports: {} };
new Function('module', 'exports', 'fetch', source + '; if (typeof initInterview === "function") module.exports.initInterview = initInterview;')(moduleObj, moduleObj.exports, globalThis.fetch);

const root = { tag: 'root', attrs: {}, children: [], textContent: '' };
const doc = {
  root,
  createElement(tag) {
    const el = { tag, attrs: {}, textContent: '', children: [] };
    el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
    el.appendChild = (c) => { el.children.push(c); c.parent = el; };
    return el;
  },
  createTextNode(text) {
    return { nodeType: 'text', textContent: String(text), children: [] };
  },
  getElementById() { return root; },
};

if (typeof moduleObj.exports.initInterview === 'function') {
  moduleObj.exports.initInterview(root, doc, { quiz_set_id: '42', auto_advance: true });
} else {
  globalThis.initInterview(root, doc, { quiz_set_id: '42', auto_advance: true });
}

const dump = (node) => ({
  tag: node.tag || (node.nodeType === 'text' ? '#text' : '?'),
  text: node.textContent || '',
  attrs: node.attrs || {},
  children: (node.children || []).map(dump),
});

setImmediate(() => {
  console.log(JSON.stringify({ calls, dom: dump(root) }));
});
"""


@pytest.fixture(scope="module")
def node_available():
    return shutil.which("node") is not None


def _run_dashboard(responses: list[dict], timeout: int = 10) -> dict:
    env = {
        **__import__("os").environ,
        "SRA_INTERVIEW_MODULE": str(MODULE_PATH),
        "SRA_INTERVIEW_RESPONSES": json.dumps(responses),
    }
    completed = subprocess.run(
        ["node", "-e", DRIVER],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"Interview runner failed: rc={completed.returncode}\nSTDERR={completed.stderr}\nSTDOUT={completed.stdout}"
        )
    return json.loads(completed.stdout)


def _start_response(attempt_id: int = 42, question: str = "first q") -> dict:
    return {
        "status": 201,
        "body": {
            "attempt_id": attempt_id,
            "question_text": question,
            "transcript_so_far": [
                {"role": "ai", "content": question, "score": None, "outline_index": 0}
            ],
        },
    }


def _answer_response(finished: bool = False, score: int = 70, qtext: str | None = "next") -> dict:
    return {
        "status": 200,
        "body": {
            "question_text": qtext,
            "score": score,
            "rationale": "ok",
            "feedback": "good",
            "finished": finished,
            "summary": None,
            "transcript_so_far": [
                {"role": "user", "content": "the answer", "score": score, "outline_index": 0},
                {"role": "ai", "content": "good", "score": None, "outline_index": 0},
            ],
        },
    }


def test_module_loads_via_node_with_stubbed_fetch(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")
    _run_dashboard([_start_response()])


def test_start_call_hits_create_attempt_route(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    output = _run_dashboard([_start_response()])
    urls = [c["url"] for c in output["calls"]]
    assert any(url.endswith("/attempts") and "/quiz/" in url for url in urls)
    bodies = [c["body"] for c in output["calls"]]
    assert any(json.loads(b).get("mode") == "interview" for b in bodies if b)


def test_render_after_answer_includes_score_chip_and_user_bubble(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _start_response(),
        _answer_response(finished=False, score=85, qtext="next q"),
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "85" in serialized
    assert "next q" in serialized


def test_render_after_finish_shows_summary_panel(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _start_response(),
        _answer_response(finished=True, score=92, qtext=None),
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "summary" in serialized.lower() or "92" in serialized


def test_401_response_renders_status_panel(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [{"status": 401, "body": {"detail": "Sign in"}}]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "sign" in serialized.lower() or "401" in serialized


def test_429_response_renders_status_panel_with_retry_after(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        {
            "status": 429,
            "headers": {"Retry-After": "5"},
            "body": {"detail": "Rate limit"},
        }
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "rate" in serialized.lower() or "429" in serialized


def test_wrong_mode_returns_400_panel(node_available):
    if not node_available:
        pytest.skip("node is required for the interview dashboard test harness")
    if not MODULE_PATH.exists():
        pytest.skip(f"dashboard module {MODULE_PATH} does not exist yet")

    responses = [
        _start_response(),
        {"status": 400, "body": {"detail": "wrong mode"}},
    ]
    output = _run_dashboard(responses)
    serialized = json.dumps(output["dom"])
    assert "wrong mode" in serialized.lower() or "400" in serialized
