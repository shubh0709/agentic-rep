from datetime import UTC, date, datetime

import pytest

from sra.agents.query_graph import make_tools
from sra.db.models import ReviewSchedule, StudyEntry


def test_make_tools_exposes_only_search_arguments(session, seeded_user):
    tools = make_tools(seeded_user.id, session)

    assert {tool.name: set(tool.args) for tool in tools} == {
        "search_entries_tool": {"query", "date_from", "date_to"},
        "get_due_reviews_tool": {"as_of"},
        "get_entry_detail_tool": {"entry_id"},
    }


@pytest.mark.parametrize("query", ["PHOTO", "chlorophyll", "botany", "light + dark"])
def test_search_tool_returns_matching_entry_summaries(session, seeded_user, query):
    entry = StudyEntry(
        user_id=seeded_user.id,
        topic="Photosynthesis",
        subtopic="Chlorophyll",
        tags=["botany"],
        summary="light + dark reactions",
        raw_transcript="Full transcript is only for the detail tool.",
        created_at=datetime(2026, 9, 25, 12, tzinfo=UTC),
    )
    session.add_all([
        entry,
        StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="cell division", raw_transcript="t"),
    ])
    session.commit()
    search_tool = make_tools(seeded_user.id, session)[0]

    result = search_tool.invoke({"query": query})

    for expected in (str(entry.id), "2026-09-25", "Photosynthesis", "Chlorophyll", "botany", "light + dark reactions"):
        assert expected in result
    assert "Mitosis" not in result
    assert entry.raw_transcript not in result


def test_search_tools_keep_each_users_context(session, seeded_user, other_user):
    session.add_all([
        StudyEntry(user_id=seeded_user.id, topic="My topic", summary="My summary", raw_transcript="t"),
        StudyEntry(user_id=other_user.id, topic="Private topic", summary="Private summary", raw_transcript="t"),
    ])
    session.commit()
    my_search = make_tools(seeded_user.id, session)[0]
    other_search = make_tools(other_user.id, session)[0]

    other_result = other_search.invoke({"query": ""})
    # An extra argument from the model must not override the factory's user.
    my_result = my_search.invoke({"query": "", "user_id": other_user.id})

    assert "My topic" in my_result
    assert "My summary" in my_result
    assert "Private" not in my_result
    assert "Private topic" in other_result
    assert "My topic" not in other_result


@pytest.mark.parametrize(
    ("filters", "expected_topics"),
    [
        ({}, ["Latest", "Evening", "Morning", "Earliest"]),
        ({"date_from": "2026-09-25"}, ["Latest", "Evening", "Morning"]),
        ({"date_to": "2026-09-25"}, ["Evening", "Morning", "Earliest"]),
        ({"date_from": "2026-09-25", "date_to": "2026-09-25"}, ["Evening", "Morning"]),
    ],
)
def test_search_tool_filters_dates_and_preserves_newest_first_order(session, seeded_user, filters, expected_topics):
    for topic, created_at in [
        ("Earliest", datetime(2026, 9, 24, 23, 59, 59, tzinfo=UTC)),
        ("Morning", datetime(2026, 9, 25, tzinfo=UTC)),
        ("Evening", datetime(2026, 9, 25, 23, 59, 59, 999999, tzinfo=UTC)),
        ("Latest", datetime(2026, 9, 26, tzinfo=UTC)),
    ]:
        session.add(StudyEntry(user_id=seeded_user.id, topic=topic, summary="s", raw_transcript="t", created_at=created_at))
    session.commit()
    search_tool = make_tools(seeded_user.id, session)[0]

    result = search_tool.invoke({"query": "", **filters})

    topics = [line.removeprefix("Topic: ") for line in result.splitlines() if line.startswith("Topic: ")]
    assert topics == expected_topics


def test_search_tool_reports_no_matches(session, seeded_user, other_user):
    session.add(StudyEntry(user_id=other_user.id, topic="Photosynthesis", summary="private", raw_transcript="t"))
    session.commit()
    search_tool = make_tools(seeded_user.id, session)[0]

    result = search_tool.invoke({"query": "Photosynthesis"})

    assert result == "No study entries found matching your search."


@pytest.mark.parametrize("field", ["date_from", "date_to"])
def test_search_tool_rejects_invalid_dates(session, seeded_user, field):
    search_tool = make_tools(seeded_user.id, session)[0]

    with pytest.raises(ValueError):
        search_tool.invoke({"query": "", field: "2026-02-30"})


def test_due_reviews_include_study_content_and_only_owned_due_pending_reviews(session, seeded_user, other_user):
    mine = StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="Cell division", raw_transcript="Full notes")
    other = StudyEntry(user_id=other_user.id, topic="Private topic", summary="Secret", raw_transcript="Private notes")
    session.add_all([mine, other])
    session.commit()
    assert mine.id is not None and other.id is not None
    session.add_all([
        ReviewSchedule(entry_id=mine.id, interval_label="1d", scheduled_date=date(2026, 10, 6)),
        ReviewSchedule(entry_id=mine.id, interval_label="3d", scheduled_date=date(2026, 10, 7)),
        ReviewSchedule(entry_id=mine.id, interval_label="7d", scheduled_date=date(2026, 10, 11)),
        ReviewSchedule(entry_id=mine.id, interval_label="14d", scheduled_date=date(2026, 10, 5), status="done"),
        ReviewSchedule(entry_id=other.id, interval_label="1d", scheduled_date=date(2026, 10, 6)),
    ])
    session.commit()

    result = make_tools(seeded_user.id, session)[1].invoke({"as_of": "2026-10-07", "user_id": other_user.id})

    assert result.count(f"Entry ID: {mine.id}") == 2
    assert "Mitosis" in result and "Cell division" in result
    assert result.index("2026-10-06") < result.index("2026-10-07")
    for excluded in ("2026-10-11", "2026-10-05", "Private", "Secret", "Full notes", "MetaData"):
        assert excluded not in result


def test_due_reviews_report_no_matches(session, seeded_user):
    result = make_tools(seeded_user.id, session)[1].invoke({"as_of": "2026-10-07"})
    assert result == "No review entries found matching date."


@pytest.mark.parametrize("arguments", [{}, {"as_of": None}, {"as_of": "2026-02-30"}])
def test_due_reviews_reject_missing_null_and_invalid_dates(session, seeded_user, arguments):
    with pytest.raises(ValueError):
        make_tools(seeded_user.id, session)[1].invoke(arguments)


def test_entry_detail_includes_transcript_and_hides_other_users_entries(session, seeded_user, other_user):
    mine = StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="Cell division", raw_transcript="My full notes")
    other = StudyEntry(user_id=other_user.id, topic="Private topic", summary="Secret", raw_transcript="Private notes")
    session.add_all([mine, other])
    session.commit()
    detail = make_tools(seeded_user.id, session)[2]

    result = detail.invoke({"entry_id": mine.id})

    assert "Mitosis" in result and "Cell division" in result and "My full notes" in result
    assert detail.invoke({"entry_id": other.id, "user_id": other_user.id}) == "Study entry not found."
    assert detail.invoke({"entry_id": 99999}) == "Study entry not found."
