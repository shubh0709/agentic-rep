def test_digest_returns_only_due_and_pending_reviews(client, session, seeded_user):
    from datetime import date, timedelta

    from sra.db.models import ReviewSchedule, StudyEntry

    entry = StudyEntry(user_id=seeded_user.id, topic="Mitosis", summary="cell division", raw_transcript="t")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    assert entry.id is not None
    session.add(ReviewSchedule(entry_id=entry.id, interval_label="1d", scheduled_date=date.today(), status="pending"))
    session.add(ReviewSchedule(entry_id=entry.id, interval_label="3d", scheduled_date=date.today() + timedelta(days=3), status="pending"))
    session.commit()

    response = client.get("/api/v1/digest")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["interval_label"] == "1d"


def test_search_returns_empty_list_for_irrelevant_query(client, seeded_user):
    response = client.get("/api/v1/search", params={"q": "nonexistent-topic-xyz"})
    assert response.status_code == 200
    assert response.json() == []


def test_search_matches_seeded_entry(client, session, seeded_user):
    from sra.db.models import StudyEntry

    session.add(StudyEntry(user_id=seeded_user.id, topic="Photosynthesis", summary="light reactions", raw_transcript="t"))
    session.commit()

    response = client.get("/api/v1/search", params={"q": "photo"})

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["topic"] == "Photosynthesis"


def test_chat_returns_non_empty_answer(client, monkeypatch, session, seeded_user):
    from sra.db.models import StudyEntry

    session.add(StudyEntry(user_id=seeded_user.id, topic="Photosynthesis", summary="light reactions", raw_transcript="t"))
    session.commit()

    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: type(
            "Fake",
            (),
            {
                "bind_tools": lambda self, tools: self,
                "invoke": lambda self, messages: {"role": "ai", "tool_calls": [], "content": "You studied photosynthesis."},
            },
        )(),
    )

    response = client.post("/api/v1/chat", json={"question": "what did I study?"})

    assert response.status_code == 200
    assert len(response.json()["answer"]) > 0


def test_chat_with_malformed_body_returns_422(client):
    response = client.post("/api/v1/chat", json={"not_a_question_field": "oops"})
    assert response.status_code == 422
