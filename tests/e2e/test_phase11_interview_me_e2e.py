"""SRA-1101 end-to-end contract for an on-demand combined assessment.

FastAPI, authentication, SQLModel, repositories, quiz answer routes, interview
turn routes, and result reads remain real.  Only model inference is replaced by
small handwritten fakes; the reference is pasted text, so no network is used.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sra.auth.google_oauth import issue_app_credential
from sra.config import get_settings


def _install_model_fakes(monkeypatch):
    from sra.agents import assessment_analyzer, interview_conversation_graph, interview_planner, quiz_graph
    from sra.agents.interview_planner import InterviewOutline, InterviewOutlineItem
    from sra.agents.schemas import MCQDraft
    from sra.db.repository import create_quiz_set

    suggestions = assessment_analyzer.TopicSuggestions(
        items=[
            assessment_analyzer.TopicSuggestion(
                key="self-attention",
                label="Self-attention",
                description="How queries, keys, and values produce context.",
                evidence_excerpt="Queries compare with keys and weight values.",
            ),
            assessment_analyzer.TopicSuggestion(
                key="multi-head-attention",
                label="Multi-head attention",
                description="Why several learned projections are combined.",
                evidence_excerpt="Multiple heads learn different relationships.",
            ),
        ]
    )
    monkeypatch.setattr(
        assessment_analyzer,
        "analyze_topics",
        lambda *, topic, reference_texts, model=None: suggestions,
    )

    generation_calls: dict[str, Any] = {"quiz": None, "interview": None}

    def fake_quiz_generation(
        *, entry, user_id, session, question_count, option_count, focus_topics, **kwargs
    ):
        generation_calls["quiz"] = {
            "count": question_count,
            "focus_topics": list(focus_topics),
        }
        drafts = []
        for index in range(question_count):
            drafts.append(
                MCQDraft(
                    question_text=f"Grounded question {index + 1}?",
                    options=["Queries and keys", "Only values", "Token count", "Batch size"][:option_count],
                    correct_option_index=0,
                    explanation="The notes say queries compare with keys.",
                )
            )
        return create_quiz_set(
            entry=entry,
            user_id=user_id,
            session=session,
            generated_on=date.today(),
            question_count=question_count,
            option_count=option_count,
            drafts=drafts,
        )

    def fake_interview_plan(
        entry, target_questions, *, references=None, focus_topics=None, **kwargs
    ):
        generation_calls["interview"] = {
            "count": target_questions,
            "focus_topics": list(focus_topics or []),
        }
        topics = list(focus_topics or ["Self-attention"])
        return InterviewOutline(
            items=[
                InterviewOutlineItem(
                    subtopic=topics[index % len(topics)],
                    difficulty="medium",
                    focus_prompt=f"Explain {topics[index % len(topics)]} question {index + 1}.",
                    source_passage_quote="Queries compare with keys and weight values across the sequence.",
                    ideal_answer_rubric="Explains the grounded role of queries, keys, and values.",
                    bloom_level="understand",
                )
                for index in range(target_questions)
            ]
        )

    monkeypatch.setattr(quiz_graph, "generate_and_persist_questions", fake_quiz_generation)
    monkeypatch.setattr(interview_planner, "generate_interview_outline", fake_interview_plan)

    class FakeStructured:
        def invoke(self, messages):
            return interview_conversation_graph.InterviewDecision(
                score=35,
                rationale="The answer omits the role of independent representation subspaces.",
                feedback="Explain how different heads learn different relationships.",
                next_action="finish",
                summary="Good start; revisit why multiple attention heads are useful.",
            )

    class FakeInterviewModel:
        def with_structured_output(self, schema):
            return FakeStructured()

    monkeypatch.setattr(
        interview_conversation_graph, "_get_chat_model", lambda: FakeInterviewModel()
    )
    return generation_calls


def test_combined_interview_me_flow_is_resumable_owner_scoped_and_saves_results(
    client, session, seeded_user, other_user, monkeypatch
):
    calls = _install_model_fakes(monkeypatch)

    analyzed = client.post(
        "/api/v1/assessments/analyze",
        json={
            "topic": "Transformer attention",
            "references": [
                {
                    "kind": "paste",
                    "title": "Lecture notes",
                    "pasted_text": (
                        "Queries compare with keys and weight values. "
                        "Multiple heads learn different relationships."
                    ),
                }
            ],
        },
    )
    assert analyzed.status_code == 201, analyzed.text
    draft = analyzed.json()
    assessment_id = draft["id"]
    assert draft["status"] == "draft"
    assert [item["key"] for item in draft["suggestions"]] == [
        "self-attention",
        "multi-head-attention",
    ]
    assert all(item["evidence_excerpt"] for item in draft["suggestions"])

    generated = client.post(
        f"/api/v1/assessments/{assessment_id}/generate",
        json={
            "selected_topic_keys": ["self-attention", "multi-head-attention"],
            "mode": "both",
            "mcq_question_count": 2,
            "interview_question_count": 3,
        },
    )
    assert generated.status_code == 200, generated.text
    ready = generated.json()
    assert ready["status"] == "ready"
    assert ready["mode"] == "both"
    assert isinstance(ready["quiz_set_id"], int)
    assert isinstance(ready["interview_plan_id"], int)
    assert calls["quiz"] == {
        "count": 2,
        "focus_topics": ["Self-attention", "Multi-head attention"],
    }
    assert calls["interview"] == {
        "count": 3,
        "focus_topics": ["Self-attention", "Multi-head attention"],
    }

    first_start = client.post(f"/api/v1/assessments/{assessment_id}/start/mcq")
    second_start = client.post(f"/api/v1/assessments/{assessment_id}/start/mcq")
    assert first_start.status_code == 200, first_start.text
    assert second_start.status_code == 200, second_start.text
    assert first_start.json()["attempt_id"] == second_start.json()["attempt_id"]
    assert second_start.json()["resumed"] is True
    mcq_attempt_id = first_start.json()["attempt_id"]

    quiz = client.get(f"/api/v1/quiz/{ready['quiz_set_id']}").json()
    assert len(quiz["questions"]) == 2
    for index, question in enumerate(quiz["questions"]):
        answer = client.post(
            f"/api/v1/quiz/attempts/{mcq_attempt_id}/answers",
            json={
                "question_id": question["id"],
                "selected_option_index": 0 if index == 0 else 1,
            },
        )
        assert answer.status_code == 200, answer.text
    completed_mcq = client.post(f"/api/v1/quiz/attempts/{mcq_attempt_id}/complete")
    assert completed_mcq.status_code == 200, completed_mcq.text

    interview_start = client.post(
        f"/api/v1/assessments/{assessment_id}/start/interview"
    )
    assert interview_start.status_code == 200, interview_start.text
    interview_attempt_id = interview_start.json()["attempt_id"]
    interview_answer = client.post(
        f"/api/v1/quiz/attempts/{interview_attempt_id}/interview-turn",
        json={"action": "answer", "answer_text": "It makes the model larger."},
    )
    assert interview_answer.status_code == 200, interview_answer.text
    assert interview_answer.json()["finished"] is True

    results = client.get(f"/api/v1/assessments/{assessment_id}/results")
    assert results.status_code == 200, results.text
    report = results.json()
    assert report["status"] == "completed"
    assert report["mode"] == "both"
    assert len(report["items"]) == 3
    assert {item["kind"] for item in report["items"]} == {"mcq", "interview"}
    assert any(item["answer"] == "It makes the model larger." for item in report["items"])
    interview_item = next(item for item in report["items"] if item["kind"] == "interview")
    assert interview_item["feedback"].startswith("Explain how")
    assert interview_item["rationale"].startswith("The answer omits")
    assert report["strengths"]
    assert report["weaknesses"]
    assert report["deep_dive_topics"]
    assert report["version"] >= 1

    # Result reads use the persisted report and never invoke inference again.
    monkeypatch.setattr(
        "sra.agents.assessment_reporter.build_assessment_report",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("report regenerated on read")),
    )
    second_results = client.get(f"/api/v1/assessments/{assessment_id}/results")
    assert second_results.status_code == 200
    assert second_results.json() == report

    history = client.get("/api/v1/assessments")
    assert history.status_code == 200
    assert history.json()[0]["id"] == assessment_id
    assert history.json()[0]["status"] == "completed"

    # Another authenticated user cannot inspect or start the session.
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "rate-limit-test-signing-secret-with-entropy")
    get_settings.cache_clear()
    assert other_user.id is not None
    other_token = issue_app_credential(
        other_user.id,
        signing_secret="rate-limit-test-signing-secret-with-entropy",
        ttl_seconds=3600,
        now=datetime.now(UTC),
    )
    other_headers = {"Authorization": f"Bearer {other_token}"}
    assert client.get(
        f"/api/v1/assessments/{assessment_id}", headers=other_headers
    ).status_code == 403
    assert client.get(
        f"/api/v1/assessments/{assessment_id}/results", headers=other_headers
    ).status_code == 403
