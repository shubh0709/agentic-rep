"""SRA-1101 unit contracts for instant Interview Me assessments.

Imports of new Phase 11 modules stay inside test bodies so pytest collection
remains healthy before the implementation exists.  These tests intentionally
fail on the missing behavior during the TDD red step.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


def _schemas():
    return importlib.import_module("sra.api.schemas")


def test_analyze_request_reuses_reference_discriminator_and_requires_a_reference():
    schemas = _schemas()
    request_type = schemas.AssessmentAnalyzeRequest

    parsed = request_type.model_validate(
        {
            "topic": "Transformer attention",
            "references": [
                {
                    "kind": "paste",
                    "title": "Lecture notes",
                    "pasted_text": "Queries compare with keys and weight values.",
                }
            ],
        }
    )
    assert parsed.topic == "Transformer attention"
    assert parsed.references[0].kind == "paste"

    with pytest.raises(ValidationError):
        request_type.model_validate({"topic": "Transformer attention", "references": []})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "selected_topic_keys": [],
            "mode": "both",
            "mcq_question_count": 5,
            "interview_question_count": 3,
        },
        {
            "selected_topic_keys": ["attention"],
            "mode": "invalid",
            "mcq_question_count": 5,
            "interview_question_count": 3,
        },
        {
            "selected_topic_keys": ["attention"],
            "mode": "mcq",
            "mcq_question_count": 0,
            "interview_question_count": 3,
        },
        {
            "selected_topic_keys": ["attention"],
            "mode": "interview",
            "mcq_question_count": 5,
            "interview_question_count": 2,
        },
    ],
)
def test_generate_request_rejects_empty_topics_bad_mode_and_out_of_range_counts(payload):
    request_type = _schemas().AssessmentGenerateRequest
    with pytest.raises(ValidationError):
        request_type.model_validate(payload)


def test_generate_request_accepts_all_three_modes_without_mutating_user_preferences():
    request_type = _schemas().AssessmentGenerateRequest

    for mode in ("mcq", "interview", "both"):
        parsed = request_type.model_validate(
            {
                "selected_topic_keys": ["self-attention", "multi-head"],
                "mode": mode,
                "mcq_question_count": 7,
                "interview_question_count": 4,
            }
        )
        assert parsed.mode == mode
        assert parsed.mcq_question_count == 7
        assert parsed.interview_question_count == 4


def test_analyzer_returns_unique_source_grounded_suggestions_through_model_boundary():
    analyzer = importlib.import_module("sra.agents.assessment_analyzer")

    class FakeStructuredModel:
        def invoke(self, messages):
            assert "queries" in str(messages).lower()
            return analyzer.TopicSuggestions(
                items=[
                    analyzer.TopicSuggestion(
                        key="self-attention",
                        label="Self-attention",
                        description="How queries, keys, and values interact.",
                        evidence_excerpt="Queries compare with keys and weight values.",
                    ),
                    analyzer.TopicSuggestion(
                        key="multi-head-attention",
                        label="Multi-head attention",
                        description="Why several attention projections are combined.",
                        evidence_excerpt="Multiple heads learn different relationships.",
                    ),
                ]
            )

    class FakeModel:
        def with_structured_output(self, schema):
            assert schema is analyzer.TopicSuggestions
            return FakeStructuredModel()

    result = analyzer.analyze_topics(
        topic="Transformer attention",
        reference_texts=[
            ("Lecture notes", "Queries compare with keys and weight values. Multiple heads learn different relationships.")
        ],
        model=FakeModel(),
    )

    assert 2 <= len(result.items) <= 12
    assert len({item.key for item in result.items}) == len(result.items)
    assert all(item.evidence_excerpt.strip() for item in result.items)


def test_reporter_preserves_answers_feedback_and_builds_strengths_and_deep_dives():
    reporter = importlib.import_module("sra.agents.assessment_reporter")

    report = reporter.build_assessment_report(
        mode="both",
        items=[
            reporter.AssessmentResultItem(
                kind="mcq",
                topic="Self-attention",
                question="What is compared to produce attention weights?",
                answer="Queries and keys",
                expected_answer="Queries and keys",
                score=100,
                is_correct=True,
                feedback="Correct and precise.",
                rationale="The selected option matches the grounded answer.",
                evidence_excerpt="Queries compare with keys.",
            ),
            reporter.AssessmentResultItem(
                kind="interview",
                topic="Multi-head attention",
                question="Why use multiple heads?",
                answer="It makes the model larger.",
                expected_answer="Different heads can learn different relationships.",
                score=30,
                is_correct=False,
                feedback="Discuss distinct representation subspaces.",
                rationale="The answer did not explain the purpose of separate heads.",
                evidence_excerpt="Multiple heads learn different relationships.",
            ),
        ],
    )

    assert report.overall_score == 65
    assert report.items[0].answer == "Queries and keys"
    assert report.items[1].feedback == "Discuss distinct representation subspaces."
    assert report.items[1].rationale.startswith("The answer did not")
    assert any("Self-attention" in value for value in report.strengths)
    assert any("Multi-head attention" in value for value in report.weaknesses)
    assert any("Multi-head attention" in value for value in report.deep_dive_topics)
    assert report.version >= 1


def test_assessment_models_keep_server_owned_fields_out_of_create_schema():
    models = importlib.import_module("sra.db.models")
    create_type = models.AssessmentSessionCreate
    table_type = models.AssessmentSession

    assert "id" not in create_type.model_fields
    assert "created_at" not in create_type.model_fields
    assert "report" not in create_type.model_fields
    assert "id" in table_type.model_fields
    assert "created_at" in table_type.model_fields
    assert "report" in table_type.model_fields
    assert table_type.model_fields["user_id"].foreign_key == "users.id"
    assert table_type.model_fields["entry_id"].foreign_key == "study_entries.id"


def test_authenticated_pages_expose_interview_me_entry_points(client):
    nav = client.get("/dashboard")
    assert nav.status_code == 200
    assert 'href="/interview-me"' in nav.text
    assert "Quiz me" in nav.text

    page = client.get("/interview-me")
    assert page.status_code == 200
    assert 'id="interview-me-root"' in page.text
    assert "interview-me.js" in page.text


def test_quiz_me_page_and_source_form_expose_accessible_orientation():
    """SRA-1204 gives the assessment wizard a clear name and labelled inputs."""
    template = (ROOT / "web" / "templates" / "interview_me.html").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "web" / "static" / "js" / "interview-me.js").read_text(
        encoding="utf-8"
    )

    assert "<h1>Quiz me</h1>" in template
    assert "What would you like to learn?" in template
    assert "id=\"assessment-topic\"" in script
    assert "for=\"assessment-topic\"" in script
    assert "id=\"assessment-notes\"" in script
    assert "for=\"assessment-notes\"" in script
    assert "id=\"assessment-url\"" in script
    assert "for=\"assessment-url\"" in script
    assert "id=\"assessment-file\"" in script
    assert "for=\"assessment-file\"" in script
    assert "PDF" in script and "DOCX" in script and "Markdown" in script


def test_browser_wizard_has_analyze_select_configure_run_and_results_states():
    script_path = ROOT / "web" / "static" / "js" / "interview-me.js"
    assert script_path.exists(), "SRA-1101 must add the Interview Me browser controller"
    source = script_path.read_text(encoding="utf-8")

    for endpoint_fragment in (
        "/api/v1/assessments/analyze",
        "/generate",
        "/start/mcq",
        "/start/interview",
        "/results",
    ):
        assert endpoint_fragment in source
    for state_name in ("sources", "topics", "configure", "mcq", "interview", "results"):
        assert state_name in source
    assert "selected_topic_keys" in source
    assert "mcq_question_count" in source
    assert "interview_question_count" in source
