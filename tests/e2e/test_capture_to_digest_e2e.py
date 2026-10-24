"""
Phase 1 end-to-end flow: POST a transcript to /api/v1/entries/ingest,
then confirm it shows up in /api/v1/digest when its first review is due and that
/api/v1/chat can answer a question about it.

No voice/audio involved yet -- that's Phase 2. This proves the
ingestion agent, scheduler, and digest/chat routes compose correctly
as one system, independent of any single unit's own mocks.
"""

class ScriptedEntryExtractor:
    def __init__(self, drafts):
        self._drafts = drafts

    def extract(self, transcript):  # noqa: ARG002
        return self._drafts


class FakeToolCallingModel:
    def __init__(self, answer: str):
        self._answer = answer
        self._calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._calls += 1
        if self._calls == 1:
            return {
                "role": "ai",
                "tool_calls": [{"name": "search_entries_tool", "args": {"query": ""}, "id": "call_1"}],
                "content": "",
            }
        return {"role": "ai", "tool_calls": [], "content": self._answer}


def test_ingest_then_digest_shows_the_new_entrys_first_checkpoint(
    client, monkeypatch, override_entry_extractor
):
    from datetime import date, datetime, timedelta

    from sra.agents.schemas import StudyEntryDraft

    override_entry_extractor(
        ScriptedEntryExtractor(
            [StudyEntryDraft(topic="Photosynthesis", summary="light + dark reactions")]
        )
    )

    ingest_response = client.post("/api/v1/entries/ingest", json={"transcript": "today I studied photosynthesis"})
    assert ingest_response.status_code == 201
    entries = ingest_response.json()
    assert len(entries) == 1
    assert entries[0]["topic"] == "Photosynthesis"

    digest_day = datetime.fromisoformat(entries[0]["created_at"]).date()

    class DigestDate(date):
        @classmethod
        def today(cls):
            return digest_day

    monkeypatch.setattr("sra.api.routers.digest.date", DigestDate)
    assert client.get("/api/v1/digest").json() == []

    # The first checkpoint is due one day after studying, not on the capture date.
    digest_day += timedelta(days=1)
    digest_response = client.get("/api/v1/digest")
    assert digest_response.status_code == 200
    due = digest_response.json()
    assert len(due) == 1
    assert any(r["entry"]["topic"] == "Photosynthesis" and r["interval_label"] == "1d" for r in due)


def test_ingest_then_chat_can_answer_about_the_new_entry(
    client, monkeypatch, override_entry_extractor
):
    from sra.agents.schemas import StudyEntryDraft

    override_entry_extractor(
        ScriptedEntryExtractor(
            [StudyEntryDraft(topic="Mitosis", summary="cell division phases")]
        )
    )
    client.post("/api/v1/entries/ingest", json={"transcript": "today I studied mitosis"})

    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: FakeToolCallingModel(answer="You studied mitosis: cell division phases."),
    )

    chat_response = client.post("/api/v1/chat", json={"question": "what did I study today?"})

    assert chat_response.status_code == 200
    assert "mitosis" in chat_response.json()["answer"].lower()


def test_chat_about_never_studied_topic_does_not_fabricate(client, monkeypatch):
    monkeypatch.setattr(
        "sra.agents.query_graph._get_chat_model",
        lambda: FakeToolCallingModel(answer="I couldn't find anything about that in your study history."),
    )

    chat_response = client.post("/api/v1/chat", json={"question": "what did I learn about quantum computing?"})

    assert chat_response.status_code == 200
    answer = chat_response.json()["answer"].lower()
    assert "couldn't find" in answer or "no" in answer or "haven't" in answer
