"""SRA-1202 — pure topic grouping and review-horizon contracts."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "web" / "static" / "js" / "library-model.js"
)


@pytest.fixture(scope="module")
def node_available() -> bool:
    return shutil.which("node") is not None


def _call(node_available: bool, function_name: str, *args):
    if not node_available:
        pytest.skip("node is required for the browser library-model contract")
    assert MODEL_PATH.exists(), "SRA-1202 must provide web/static/js/library-model.js"
    driver = """
const model = require(process.env.SRA_LIBRARY_MODEL);
const input = JSON.parse(process.env.SRA_LIBRARY_INPUT);
const fn = model[input.functionName];
if (typeof fn !== "function") {
  throw new Error(`Missing exported function: ${input.functionName}`);
}
process.stdout.write(JSON.stringify(fn(...input.args)));
"""
    completed = subprocess.run(
        ["node", "-e", driver],
        env={
            "PATH": __import__("os").environ.get("PATH", ""),
            "SRA_LIBRARY_MODEL": str(MODEL_PATH),
            "SRA_LIBRARY_INPUT": json.dumps(
                {"functionName": function_name, "args": args}
            ),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


ENTRIES = [
    {
        "id": 2,
        "topic": "journey of request to backend",
        "subtopic": "HTTP",
        "tags": ["backend", "http"],
        "summary": "Newest useful summary",
        "raw_transcript": "new transcript",
        "created_at": "2026-10-24T10:00:00Z",
    },
    {
        "id": 1,
        "topic": "  Journey   of Request to Backend ",
        "subtopic": "Request lifecycle",
        "tags": ["backend", "learning"],
        "summary": "Older summary",
        "raw_transcript": "old transcript",
        "created_at": "2026-10-23T10:00:00Z",
    },
    {
        "id": 3,
        "topic": "RAG retrieval and storage",
        "subtopic": None,
        "tags": ["rag"],
        "summary": "RAG summary",
        "raw_transcript": "rag transcript",
        "created_at": "2026-10-22T10:00:00Z",
    },
    {
        "id": 4,
        "topic": "Retrieval and Storage",
        "subtopic": None,
        "tags": ["storage"],
        "summary": "Storage summary",
        "raw_transcript": "storage transcript",
        "created_at": "2026-10-21T10:00:00Z",
    },
    {
        "id": 5,
        "topic": "Quiet practice",
        "subtopic": None,
        "tags": ["habit"],
        "summary": "No review is scheduled",
        "raw_transcript": "quiet transcript",
        "created_at": "2026-10-20T10:00:00Z",
    },
]

DUE = [
    {
        "id": 11,
        "entry": ENTRIES[0],
        "interval_label": "1d",
        "scheduled_date": "2026-10-24",
        "status": "pending",
    },
    {
        "id": 12,
        "entry": ENTRIES[1],
        "interval_label": "1d",
        "scheduled_date": "2026-10-24",
        "status": "pending",
    },
]

UPCOMING = [
    {
        "date": "2026-10-27",
        "reviews": [
            {"id": 13, "entry": ENTRIES[0], "scheduled_date": "2026-10-27"},
            {"id": 14, "entry": ENTRIES[1], "scheduled_date": "2026-10-27"},
        ],
    },
    {
        "date": "2026-11-03",
        "reviews": [
            {"id": 15, "entry": ENTRIES[2], "scheduled_date": "2026-11-03"},
        ],
    },
    {
        "date": "2026-12-03",
        "reviews": [
            {"id": 16, "entry": ENTRIES[3], "scheduled_date": "2026-12-03"},
        ],
    },
]


def _groups(node_available: bool):
    return _call(
        node_available,
        "buildTopicGroups",
        ENTRIES,
        DUE,
        UPCOMING,
        "2026-10-24",
    )


def test_exact_normalized_names_group_without_fuzzy_merging(node_available):
    groups = _groups(node_available)

    assert len(groups) == 4
    journey = next(group for group in groups if group["key"] == "journey of request to backend")
    assert journey["noteCount"] == 2
    assert [entry["id"] for entry in journey["entries"]] == [2, 1]
    assert journey["summary"] == "Newest useful summary"
    assert journey["tags"] == ["backend", "http", "learning"]
    assert {group["key"] for group in groups} >= {
        "rag retrieval and storage",
        "retrieval and storage",
    }


def test_reviews_join_by_entry_id_and_dates_keep_checkpoint_counts(node_available):
    groups = _groups(node_available)
    journey = next(group for group in groups if group["key"] == "journey of request to backend")

    assert journey["nextReviewDate"] == "2026-10-24"
    assert journey["readyNow"] is True
    assert journey["reviewDates"] == [
        {"date": "2026-10-24", "count": 2},
        {"date": "2026-10-27", "count": 2},
    ]
    assert next(group for group in groups if group["key"] == "quiet practice")[
        "reviewDates"
    ] == []


def test_review_horizon_counts_unique_topics_in_non_overlapping_ranges(node_available):
    horizon = _call(
        node_available,
        "buildReviewHorizon",
        _groups(node_available),
        "2026-10-24",
    )

    assert horizon == {
        "ready": {"count": 1, "topicKeys": ["journey of request to backend"]},
        "next7": {"count": 0, "topicKeys": []},
        "next30": {"count": 1, "topicKeys": ["rag retrieval and storage"]},
        "later": {"count": 1, "topicKeys": ["retrieval and storage"]},
    }


def test_search_filters_whole_topic_group_across_nested_capture_fields(node_available):
    groups = _groups(node_available)
    result = _call(
        node_available,
        "filterAndSortTopics",
        groups,
        {"query": "learning", "filter": "all", "sort": "recent"},
        "2026-10-24",
    )

    assert [group["key"] for group in result] == ["journey of request to backend"]
    assert result[0]["noteCount"] == 2


@pytest.mark.parametrize(
    ("filter_name", "expected"),
    [
        ("ready", ["journey of request to backend"]),
        ("next7", []),
        ("none", ["quiet practice"]),
    ],
)
def test_topic_filters_use_schedule_state(node_available, filter_name, expected):
    result = _call(
        node_available,
        "filterAndSortTopics",
        _groups(node_available),
        {"query": "", "filter": filter_name, "sort": "recent"},
        "2026-10-24",
    )

    assert [group["key"] for group in result] == expected


def test_sorts_are_stable_for_next_review_recent_and_alphabetical(node_available):
    groups = _groups(node_available)
    next_review = _call(
        node_available,
        "filterAndSortTopics",
        groups,
        {"query": "", "filter": "all", "sort": "next"},
        "2026-10-24",
    )
    alphabetical = _call(
        node_available,
        "filterAndSortTopics",
        groups,
        {"query": "", "filter": "all", "sort": "az"},
        "2026-10-24",
    )

    assert [group["key"] for group in next_review] == [
        "journey of request to backend",
        "rag retrieval and storage",
        "retrieval and storage",
        "quiet practice",
    ]
    assert [group["key"] for group in alphabetical] == [
        "journey of request to backend",
        "quiet practice",
        "rag retrieval and storage",
        "retrieval and storage",
    ]
