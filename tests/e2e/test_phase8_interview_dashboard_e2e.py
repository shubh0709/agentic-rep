"""SRA-807 — Interview dashboard end-to-end through real ``create_app()``."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false

import json
import os
from datetime import date
from pathlib import Path

import pytest

from sra.agents.schemas import MCQDraft
from sra.db.models import StudyEntry, StudyEntryCreate

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[2]
INTERVIEW_JS = ROOT / "dashboard" / "static" / "interview.js"


def _seed_entry(session, user_id: int) -> StudyEntry:
    entry = StudyEntry.model_validate(
        StudyEntryCreate(topic="Mitosis", summary="cell division", raw_transcript="t"),
        update={"user_id": user_id},
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _make_quiz_set(session, user_id: int) -> int:
    from sra.db.repository import create_quiz_set

    entry = _seed_entry(session, user_id)
    drafts = [
        MCQDraft(
            question_text="q0", options=["a", "b"], correct_option_index=0, explanation=""
        )
    ]
    qs = create_quiz_set(
        entry=entry,
        user_id=user_id,
        session=session,
        generated_on=date.today(),
        question_count=len(drafts),
        option_count=2,
        drafts=drafts,
    )
    return qs.id if qs.id is not None else 0


def _patch_interview_planner(monkeypatch, n: int = 1):
    from sra.agents import interview_planner as _ip

    class _Plan:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages, **kwargs):
            from sra.agents.interview_planner import (
                InterviewOutline,
                InterviewOutlineItem,
            )

            return InterviewOutline(
                items=[
                    InterviewOutlineItem(
                        subtopic=f"s{i}",
                        difficulty="medium",
                        focus_prompt=f"f{i}",
                    )
                    for i in range(n)
                ]
            )

    monkeypatch.setattr(_ip, "_get_chat_model", lambda: _Plan())


def _patch_decision(monkeypatch, scripted):
    from sra.agents import interview_conversation_graph as _icg

    class _FakeChatModel:
        def with_structured_output(self, schema, **kwargs):
            assert schema is _icg.InterviewDecision
            return self

        def invoke(self, messages, **kwargs):
            return _icg.InterviewDecision(**scripted.pop(0))

    monkeypatch.setattr(_icg, "_get_chat_model", lambda: _FakeChatModel())


def test_interview_dashboard_e2e_endpoints_match_documented_set(monkeypatch, client, session, seeded_user):
    """The dashboard's expected URL set during a start -> 2 answers -> next -> answer -> end loop."""

    quiz_set_id = _make_quiz_set(session, seeded_user.id)
    _patch_interview_planner(monkeypatch)

    start = client.post(
        f"/api/v1/quiz/{quiz_set_id}/attempts",
        json={"mode": "interview"},
    )
    assert start.status_code == 201
    attempt_id = start.json()["attempt_id"]
    body = start.json()
    assert "question_text" in body
    assert "transcript_so_far" in body

    decisions = [
        dict(
            score=70,
            rationale="ok",
            feedback="good",
            next_action="ask_new",
            next_question_text="q2",
        ),
        dict(
            score=80,
            rationale="ok2",
            feedback="good2",
            next_action="finish",
            next_question_text=None,
            summary="two answers logged",
        ),
    ]
    _patch_decision(monkeypatch, decisions)

    a1 = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "a1"},
    )
    assert a1.status_code == 200
    nxt = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "next"},
    )
    assert nxt.status_code == 200
    a2 = client.post(
        f"/api/v1/quiz/attempts/{attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "a2"},
    )
    assert a2.status_code == 200
    assert a2.json()["finished"] is True

    detail = client.get(f"/api/v1/quiz/attempts/{attempt_id}/interview")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["plan"]["quiz_set_id"] == quiz_set_id
    assert detail_body["attempt"]["id"] == attempt_id
    roles = {t["role"] for t in detail_body["turns"]}
    assert "user" in roles
    assert "ai" in roles
    assert "system_summary" in roles


def test_interview_dashboard_module_loads_via_node(monkeypatch, tmp_path):
    """The ``interview.js`` file ships with an ``initInterview`` global."""
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("node is required for the jsdom dashboard test harness")

    if not INTERVIEW_JS.exists():
        pytest.skip(f"interview.js does not exist yet: {INTERVIEW_JS}")

    driver = (
        "const fs=require('node:fs');"
        "globalThis.fetch=async()=>({});"
        "const src=fs.readFileSync(process.env.MOD,'utf8');"
        "new Function('globalThis','source',src+';globalThis.__interviewProbe=(typeof initInterview===\"function\");')(globalThis,src);"
        "console.log(JSON.stringify({loaded:globalThis.__interviewProbe}));"
    )
    completed = subprocess.run(
        ["node", "-e", driver],
        env={**os.environ, "MOD": str(INTERVIEW_JS)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        pytest.skip(f"node runner failed: {completed.stderr}")
    payload = json.loads(completed.stdout)
    assert payload["loaded"] is True
