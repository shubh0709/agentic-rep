"""Real WAV/Whisper/HTTP/database flow; only provider inference differs by tier.

The simulated microphone replays PCM through the production recorder callback.
No OS microphone access, UI automation, or personal study database is used.
"""

import json
import os
import random
import socket
import struct
import threading
import time
import wave
from collections import deque
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from dateutil.relativedelta import relativedelta
from langchain_core.messages import AIMessage, ToolMessage
from sqlmodel import Session, select

from sra.agents.schemas import StudyEntryDraft
from sra.auth.google_oauth import issue_app_credential
from sra.capture.menubar_app import CaptureController
from sra.capture.pipeline import process_recording
from sra.capture.recording import Recorder
from sra.capture.transcription import TranscriptionError
from sra.config import get_client_settings, get_settings
from sra.db.models import ReviewSchedule, StudyEntry, User
from sra.db.session import get_engine

pytestmark = [
    pytest.mark.phase2,
    pytest.mark.skipif(os.getenv("SRA_RUN_AUDIO_FLOW") != "1", reason="Run make test-data or make test-live"),
]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "study_note.wav"


class FixtureExtraction:
    def extract(self, transcript):
        assert "photosynthesis" in transcript.casefold()
        return [StudyEntryDraft(
            topic="Photosynthesis", summary="Plants use sunlight to produce sugar and oxygen.",
        )]


class FixtureQuery:
    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        results = [message for message in messages if isinstance(message, ToolMessage)]
        if results:
            # Return the actual database tool result, not a predetermined answer.
            assert "Entry ID:" in str(results[-1].content)
            return AIMessage(content=str(results[-1].content))
        return AIMessage(content="", tool_calls=[{
            "name": "search_entries_tool", "args": {"query": "photosynthesis"}, "id": "fixture-search",
        }])


@pytest.fixture
def audio_backend(monkeypatch, tmp_path, request):
    live = request.node.get_closest_marker("live_openai") is not None
    if live:
        if os.getenv("SRA_RUN_LIVE_E2E") != "1":
            pytest.skip("Real OpenAI calls require make test-live")
        # Load the configured credential before moving away from the repository .env.
        settings = get_settings()
        assert settings.openai_api_key.get_secret_value().strip(), "OPENAI_API_KEY is required"
        monkeypatch.setenv("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
        monkeypatch.setenv("OPENAI_MODEL", settings.openai_model)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-no-network")
        monkeypatch.setattr("sra.agents.query_graph._get_chat_model", FixtureQuery)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'study.db'}")
    monkeypatch.setenv("WHISPER_MODEL_SIZE", "base")
    monkeypatch.setenv("AUTH_SIGNING_SECRET", "offline-audio-fixture-signing-secret")
    get_settings.cache_clear()
    get_client_settings.cache_clear()
    get_engine.cache_clear()
    submissions = []
    credential = None

    @contextmanager
    def running():
        nonlocal credential
        from sra.agents.entry_extractor import get_entry_extractor
        from sra.api.app import create_app

        app = create_app()
        if not live:
            app.dependency_overrides[get_entry_extractor] = FixtureExtraction
        if credential is None:
            with Session(get_engine()) as session:
                user = User(email="audio@example.com", display_name="Audio fixture", google_sub="audio-fixture")
                session.add(user)
                session.commit()
                session.refresh(user)
                assert user.id is not None
                credential = issue_app_credential(
                    user.id, signing_secret="offline-audio-fixture-signing-secret",
                    ttl_seconds=3600, now=datetime.now(UTC),
                )

        @app.middleware("http")
        async def observe(request, call_next):
            if request.url.path == "/api/v1/entries/ingest":
                submissions.append(json.loads(await request.body()))
            return await call_next(request)

        # Keep the socket bound throughout startup to avoid a free-port race.
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            base = f"http://127.0.0.1:{listener.getsockname()[1]}"
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
            thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert server.started, "Local test API failed to start"
                monkeypatch.setenv("API_BASE_URL", base)
                get_client_settings.cache_clear()
                with httpx.Client(
                    base_url=base, timeout=90, trust_env=False, headers={"Authorization": f"Bearer {credential}"},
                ) as client:
                    yield client
            finally:
                server.should_exit = True
                thread.join(timeout=10)
                assert not thread.is_alive(), "Local test API did not stop"

    try:
        yield running, submissions
    finally:
        get_engine().dispose()
        get_engine.cache_clear()
        get_settings.cache_clear()
        get_client_settings.cache_clear()


def replay_capture(monkeypatch, tmp_path, variant, credential):
    """Exercise real controller and recorder using a reusable device buffer."""
    with wave.open(str(FIXTURE), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
        pcm = wav.readframes(wav.getnframes())
    if variant != "clean":
        samples = struct.unpack(f"<{len(pcm) // 2}h", pcm)
        rng = random.Random(206)
        if variant == "quiet":
            samples = [round(sample * 0.2) for sample in samples]
        elif variant == "noise":
            samples = [max(-32768, min(32767, sample + rng.randint(-100, 100))) for sample in samples]
        pcm = struct.pack(f"<{len(samples)}h", *samples)
    streams = []

    class Stream:
        def __init__(self, *, sample_rate, channels, callback):
            assert (sample_rate, channels) == (16000, 1)
            self.callback = callback
            self.closed = False
            streams.append(self)

        def start(self):
            if len(streams) == 1 and variant == "permission":
                raise PermissionError("microphone permission denied")
            if len(streams) == 1 and variant == "overflow":
                self.callback(b"\0\0", 1, None, "input overflow")
                return
            for offset in range(0, len(pcm), 2048):
                buffer = bytearray(pcm[offset:offset + 2048])
                self.callback(buffer, len(buffer) // 2, None, False)
                buffer[:] = b"\0" * len(buffer)  # Device reuses memory after callback.

        def stop(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr("sra.capture.recording._open_audio_stream", Stream)
    work, ui = deque(), deque()
    statuses, saved = [], []
    owned = tmp_path / "recordings"
    owned.mkdir()

    def process(path):
        assert all(stream.closed for stream in streams)
        with wave.open(str(path), "rb") as wav:
            assert wav.readframes(wav.getnframes()) == pcm
        entries = process_recording(path, credential=credential)
        saved.extend(entries)
        return entries

    controller = CaptureController(
        recorder_factory=lambda: Recorder(temp_dir=owned), process=process,
        submit=work.append, dispatch=ui.append, show_status=statuses.append, stop_listener=lambda: None,
    )
    try:
        if variant in {"permission", "overflow"}:
            controller.toggle()
            if variant == "overflow":
                controller.toggle()
            assert controller.state == "idle"
            assert not work and not saved
            assert variant in statuses[-1].casefold(), statuses
            assert list(owned.iterdir()) == []
        controller.toggle()
        assert controller.state == "recording"
        controller.toggle()
        assert controller.state == "processing"
        for _ in range(10):
            controller.toggle()
        assert len(work) == 1
        work.popleft()()
        while ui:
            ui.popleft()()
        assert controller.state == "idle"
        assert saved, statuses
        assert "saved" in statuses[-1].casefold(), statuses
        return saved
    finally:
        controller.quit()
        assert all(stream.closed for stream in streams)
        assert list(owned.iterdir()) == []


def verify_flow(audio_backend, monkeypatch, tmp_path, variant):
    running, submissions = audio_backend
    original = FIXTURE.read_bytes()
    with running() as client:
        credential = client.headers["Authorization"].removeprefix("Bearer ")
        entries = replay_capture(monkeypatch, tmp_path, variant, credential)
        ids = {entry["id"] for entry in entries}
        assert len(ids) == len(entries)
        assert len(submissions) == 1
        assert set(submissions[0]) == {"transcript"}
        transcript = submissions[0]["transcript"]
        for keyword in ("photosynthesis", "plants", "sunlight", "oxygen"):
            assert keyword in transcript.casefold(), transcript
        with Session(get_engine()) as session:
            stored = session.exec(select(StudyEntry)).all()
            assert {entry.id for entry in stored} == ids
            owner = session.exec(select(User)).one()
            assert owner.email == "audio@example.com" and owner.google_sub == "audio-fixture"
            for entry in stored:
                assert entry.user_id == owner.id and entry.raw_transcript == transcript
                response = client.get("/api/v1/search", params={"q": entry.topic})
                response.raise_for_status()
                assert entry.id in {row["id"] for row in response.json()}
                reviews = session.exec(select(ReviewSchedule).where(ReviewSchedule.entry_id == entry.id)).all()
                day = entry.created_at.date()
                assert len(reviews) == 6
                assert {r.interval_label: r.scheduled_date for r in reviews} == {
                    "1d": day + timedelta(days=1), "3d": day + timedelta(days=3),
                    "7d": day + timedelta(days=7), "1mo": day + relativedelta(months=1),
                    "3mo": day + relativedelta(months=3), "6mo": day + relativedelta(months=6),
                }
                assert all(r.status == "pending" and r.completed_at is None for r in reviews)
            original_dates = {r.id: r.scheduled_date for r in session.exec(select(ReviewSchedule)).all()}
            capture_day = min(entry.created_at.date() for entry in stored)

        class Clock(date):
            current = capture_day

            @classmethod
            def today(cls):
                return cls.current

        monkeypatch.setattr("sra.api.routers.digest.date", Clock)
        response = client.get("/api/v1/digest")
        response.raise_for_status()
        assert response.json() == []
        Clock.current = max(original_dates.values())  # Check all six due intervals.
        response = client.get("/api/v1/digest")
        response.raise_for_status()
        assert len(response.json()) == 6 * len(ids)
        Clock.current = capture_day + timedelta(days=1)
        response = client.get("/api/v1/digest")
        response.raise_for_status()
        assert {row["entry"]["id"] for row in response.json()} == ids
        assert all(row["interval_label"] == "1d" for row in response.json())
        answer = client.post("/api/v1/chat", json={
            "question": "Search my saved study notes for photosynthesis. What did I study about it?",
        })
        answer.raise_for_status()
        assert "photosynthesis" in answer.json()["answer"].casefold(), answer.text

    # Recreate application, engine, sessions and server against the same isolated file.
    get_engine().dispose()
    get_engine.cache_clear()
    with running() as client, Session(get_engine()) as session:
        response = client.get("/api/v1/search", params={"q": ""})
        response.raise_for_status()
        assert {entry["id"] for entry in response.json()} == ids
        assert {r.id: r.scheduled_date for r in session.exec(select(ReviewSchedule)).all()} == original_dates
    assert FIXTURE.read_bytes() == original
    assert len(submissions) == 1, "Server restart must not resubmit capture"


@pytest.mark.parametrize("variant", ["clean", "quiet", "noise", "permission", "overflow"])
def test_fixture_audio_flow(audio_backend, monkeypatch, tmp_path, variant):
    verify_flow(audio_backend, monkeypatch, tmp_path, variant)


@pytest.mark.live_openai
def test_live_openai_audio_flow(audio_backend, monkeypatch, tmp_path):
    verify_flow(audio_backend, monkeypatch, tmp_path, "clean")


@pytest.mark.parametrize("kind", ["silence", "corrupt"])
def test_unusable_real_audio_does_not_submit(audio_backend, tmp_path, kind):
    running, submissions = audio_backend
    path = tmp_path / "unusable.wav"
    if kind == "silence":
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\0\0" * 16000 * 3)
    else:
        path.write_bytes(b"not a WAV")
    original = path.read_bytes()
    with running():
        if kind == "silence":
            assert process_recording(path) == []
        else:
            with pytest.raises(TranscriptionError):
                process_recording(path)
        with Session(get_engine()) as session:
            assert session.exec(select(StudyEntry)).all() == []
            assert session.exec(select(ReviewSchedule)).all() == []
    assert submissions == []
    assert path.read_bytes() == original
