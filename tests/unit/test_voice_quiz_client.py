"""SRA-508 — Voice quiz client driver: speak, record, transcribe, send, repeat."""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportOptionalMemberAccess=false, reportCallIssue=false, reportRedeclaration=false, reportGeneralTypeIssues=false

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.phase5


@pytest.fixture
def tts():
    from sra.capture import tts as _tts

    return _tts


@pytest.fixture
def voice_quiz_client():
    from sra.capture import voice_quiz_client as _vqc

    return _vqc


class _RecordingTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, text: str) -> None:
        self.calls.append(text)


@pytest.fixture
def recording_tts(monkeypatch, tts):
    backend = _RecordingTTS()
    tts.set_backend(backend)
    yield backend
    tts.reset_backend()


def _build_driver(voice_quiz_client, *, api_client=None, recorder=None, transcriber=None):
    return voice_quiz_client.run_voice_quiz(
        quiz_set_id=1,
        api_client=api_client,
        recorder=recorder,
        transcriber=transcriber,
    )


def _make_api_client(
    *,
    start_payload: dict | None = None,
    turn_payloads: list[dict] | None = None,
    start_side_effect: Exception | None = None,
    turn_error: Exception | None = None,
):
    client = SimpleNamespace()
    client.start_voice_quiz_attempt = MagicMock(
        return_value=start_payload
        if start_payload is not None
        else {"attempt_id": 99, "question_text": "q0", "options": ["a", "b"]}
    )
    if start_side_effect is not None:
        client.start_voice_quiz_attempt.side_effect = start_side_effect

    payloads = list(turn_payloads or [])
    raised = []

    def turn_handler(*args, **kwargs):
        if turn_error is not None and not raised:
            raised.append("raised")
            raise turn_error
        if not payloads:
            raise AssertionError("no scripted turn payload left")
        return payloads.pop(0)

    client.send_voice_turn = MagicMock(side_effect=turn_handler)
    return client


def test_run_voice_quiz_speaks_first_question_before_recording(
    recording_tts, tmp_path, voice_quiz_client
):
    api_client = _make_api_client(
        start_payload={"attempt_id": 1, "question_text": "first question", "options": ["a", "b"]},
        turn_payloads=[{
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": "q1",
            "finished": False,
            "score": 1,
        }, {
            "is_correct": False,
            "feedback_text": "Incorrect.",
            "next_question_text": "q1",
            "finished": True,
            "score": 1,
        }],
    )
    recorder = MagicMock(return_value=tmp_path / "answer.wav")
    transcriber = MagicMock(return_value="the second one")

    result = _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert result == {"score": 1, "total_questions": 2}
    assert recording_tts.calls[0] == "first question"


def test_run_voice_quiz_forwards_transcript_to_send_voice_turn(
    recording_tts, tmp_path, voice_quiz_client
):
    api_client = _make_api_client(
        turn_payloads=[{
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": None,
            "finished": True,
            "score": 1,
        }],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="the user said this")

    _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    api_client.send_voice_turn.assert_called_once_with(99, "the user said this")


def test_run_voice_quiz_speaks_feedback_then_next_question(recording_tts, tmp_path, voice_quiz_client):
    api_client = _make_api_client(
        turn_payloads=[{
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": "the next question",
            "finished": False,
            "score": 1,
        }, {
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": "last",
            "finished": True,
            "score": 2,
        }],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="yes")

    _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert "Correct." in recording_tts.calls[1]
    assert "the next question" in recording_tts.calls[1]


def test_run_voice_quiz_returns_score_and_total_questions(recording_tts, tmp_path, voice_quiz_client):
    api_client = _make_api_client(
        turn_payloads=[{
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": "q2",
            "finished": True,
            "score": 3,
        }],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="c")

    result = _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert result == {"score": 3, "total_questions": 4}


def test_run_voice_quiz_speaks_final_summary_when_finished(recording_tts, tmp_path, voice_quiz_client):
    api_client = _make_api_client(
        turn_payloads=[{
            "is_correct": True,
            "feedback_text": "Correct.",
            "next_question_text": "next",
            "finished": True,
            "score": 2,
        }],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="d")

    _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    summary = recording_tts.calls[-1]
    assert "2" in summary and "out" in summary.lower()


def test_run_voice_quiz_raises_typed_error_when_recorder_fails(recording_tts, tmp_path, voice_quiz_client):
    api_client = _make_api_client()
    recorder = MagicMock(side_effect=RuntimeError("microphone permission denied"))
    transcriber = MagicMock(return_value="ignored")

    with pytest.raises(voice_quiz_client.VoiceQuizError) as caught:
        _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert caught.value.code == "recorder_failed"
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_run_voice_quiz_raises_typed_error_when_transcriber_fails(recording_tts, tmp_path, voice_quiz_client):
    from sra.capture.transcription import TranscriptionError

    api_client = _make_api_client()
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(side_effect=TranscriptionError("decoder rejected"))

    with pytest.raises(voice_quiz_client.VoiceQuizError) as caught:
        _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert caught.value.code == "transcription_failed"


def test_run_voice_quiz_raises_typed_error_when_server_returns_409(recording_tts, tmp_path, voice_quiz_client):
    from sra.client.api_client import ApiClientError

    api_client = _make_api_client(turn_error=ApiClientError("attempt_completed"))
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="ignored")

    with pytest.raises(voice_quiz_client.VoiceQuizError) as caught:
        _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert caught.value.code == "attempt_completed"
    assert isinstance(caught.value.__cause__, ApiClientError)


def test_run_voice_quiz_raises_typed_error_on_generic_server_failure(recording_tts, tmp_path, voice_quiz_client):
    from sra.client.api_client import ApiClientError

    api_client = _make_api_client(turn_error=ApiClientError("server unavailable"))
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="ignored")

    with pytest.raises(voice_quiz_client.VoiceQuizError) as caught:
        _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert caught.value.code == "server_error"


def test_voice_quiz_error_is_a_runtime_error_subclass(voice_quiz_client):
    assert issubclass(voice_quiz_client.VoiceQuizError, RuntimeError)


def test_run_voice_quiz_records_then_transcribes_for_each_turn(recording_tts, tmp_path, voice_quiz_client):
    api_client = _make_api_client(
        turn_payloads=[
            {
                "is_correct": True,
                "feedback_text": "OK",
                "next_question_text": "q1",
                "finished": False,
                "score": 1,
            },
            {
                "is_correct": False,
                "feedback_text": "Nope",
                "next_question_text": None,
                "finished": True,
                "score": 1,
            },
        ],
    )
    paths = [tmp_path / "a.wav", tmp_path / "b.wav"]
    recorder = MagicMock(side_effect=paths)
    transcriber = MagicMock(side_effect=["first answer", "second answer"])

    _build_driver(voice_quiz_client, api_client=api_client, recorder=recorder, transcriber=transcriber)

    assert recorder.call_count == 2
    assert transcriber.call_count == 2
    assert api_client.send_voice_turn.call_count == 2


# ---------------------------------------------------------------------------
# SRA-808 — ``run_interview`` driver composes speak/record/transcribe/send
# the same way ``run_voice_quiz`` does.
# ---------------------------------------------------------------------------


phase8 = pytest.mark.phase8  # noqa: E402  -- marker alias below the Phase 5 marker


def _make_interview_api_client(
    *,
    start_payload: dict | None = None,
    turn_payloads: list[dict] | None = None,
    turn_error: Exception | None = None,
    detail_payload: dict | None = None,
):
    client = SimpleNamespace()
    client.start_interview_attempt = MagicMock(
        return_value=start_payload
        if start_payload is not None
        else {"attempt_id": 99, "question_text": "first q", "transcript_so_far": []}
    )

    payloads = list(turn_payloads or [])

    def turn_handler(*args, **kwargs):
        if turn_error is not None:
            raise turn_error
        if not payloads:
            raise AssertionError("no scripted turn payload left")
        return payloads.pop(0)

    client.send_interview_turn = MagicMock(side_effect=turn_handler)

    client.get_interview = MagicMock(
        return_value=detail_payload
        if detail_payload is not None
        else {
            "plan": {
                "id": 1,
                "quiz_set_id": 1,
                "outline": [{"subtopic": "a", "difficulty": "medium", "focus_prompt": "x"}],
                "target_questions": 1,
                "created_at": "2026-10-14T12:00:00Z",
            },
            "turns": [],
            "summary": None,
            "attempt": {
                "id": 99,
                "quiz_set_id": 1,
                "mode": "interview",
                "started_at": "2026-10-14T12:00:00Z",
                "completed_at": None,
                "score": None,
                "total_questions": 1,
                "cursor_on_plan": 0,
            },
        }
    )
    return client


def _build_interview_driver(voice_quiz_client, **kwargs):
    return voice_quiz_client.run_interview(quiz_set_id=1, **kwargs)


@phase8
def test_run_interview_speaks_first_question_then_records_first_answer(
    recording_tts, tmp_path, voice_quiz_client
):
    api_client = _make_interview_api_client(
        turn_payloads=[
            {
                "score": 70,
                "rationale": "ok",
                "feedback": "good",
                "next_action": "ask_new",
                "question_text": "second q",
                "finished": False,
                "summary": None,
                "transcript_so_far": [],
            },
            {
                "score": 80,
                "rationale": "ok2",
                "feedback": "good2",
                "next_action": "finish",
                "question_text": None,
                "finished": True,
                "summary": "summary text",
                "transcript_so_far": [],
            },
        ],
    )
    recorder = MagicMock(side_effect=[tmp_path / "a.wav", tmp_path / "b.wav"])
    transcriber = MagicMock(side_effect=["first answer", "second answer"])

    result = _build_interview_driver(
        voice_quiz_client,
        api_client=api_client,
        recorder=recorder,
        transcriber=transcriber,
    )

    assert result["score"] == 80
    assert result["answered_count"] == 2
    assert result["remaining_count"] == 0  # target was 1 per detail_payload, two answers consumed
    # First question was spoken
    assert recording_tts.calls[0] == "first q"
    # Feedback spoken after first answer
    assert any("good" in c for c in recording_tts.calls)
    # Summary spoken when finished
    assert recording_tts.calls[-1] == "summary text"


@phase8
def test_run_interview_truncates_long_first_question_to_280_chars(
    recording_tts, tmp_path, voice_quiz_client
):
    long_q = "x" * 400
    api_client = _make_interview_api_client(
        start_payload={"attempt_id": 1, "question_text": long_q, "transcript_so_far": []},
        turn_payloads=[
            {
                "score": 50,
                "rationale": "ok",
                "feedback": "ok",
                "next_action": "finish",
                "question_text": None,
                "finished": True,
                "summary": "short",
                "transcript_so_far": [],
            }
        ],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="the answer")

    _build_interview_driver(
        voice_quiz_client,
        api_client=api_client,
        recorder=recorder,
        transcriber=transcriber,
    )

    first_spoken = recording_tts.calls[0]
    assert len(first_spoken) <= 280 + 1  # 280 + "…"


@phase8
def test_run_interview_returns_score_answered_count_and_remaining_count(
    recording_tts, tmp_path, voice_quiz_client
):
    """Returned shape is documented as ``{score, answered_count, remaining_count}``."""

    api_client = _make_interview_api_client(
        start_payload={"attempt_id": 1, "question_text": "q", "transcript_so_far": []},
        turn_payloads=[
            {
                "score": 70,
                "rationale": "ok",
                "feedback": "good",
                "next_action": "ask_new",
                "question_text": "q2",
                "finished": False,
                "summary": None,
                "transcript_so_far": [],
            },
            {
                "score": 80,
                "rationale": "ok2",
                "feedback": "good2",
                "next_action": "finish",
                "question_text": None,
                "finished": True,
                "summary": "summary",
                "transcript_so_far": [],
            },
        ],
        detail_payload={
            "plan": {
                "id": 1,
                "quiz_set_id": 1,
                "outline": [
                    {"subtopic": "s0", "difficulty": "medium", "focus_prompt": "f0"},
                    {"subtopic": "s1", "difficulty": "medium", "focus_prompt": "f1"},
                    {"subtopic": "s2", "difficulty": "medium", "focus_prompt": "f2"},
                    {"subtopic": "s3", "difficulty": "medium", "focus_prompt": "f3"},
                ],
                "target_questions": 4,
                "created_at": "2026-10-14T12:00:00Z",
            },
            "turns": [],
            "summary": None,
            "attempt": {
                "id": 1,
                "quiz_set_id": 1,
                "mode": "interview",
                "started_at": "2026-10-14T12:00:00Z",
                "completed_at": None,
                "score": None,
                "total_questions": 4,
                "cursor_on_plan": 0,
            },
        },
    )
    recorder = MagicMock(side_effect=[tmp_path / "a.wav", tmp_path / "b.wav"])
    transcriber = MagicMock(side_effect=["a1", "a2"])

    result = _build_interview_driver(
        voice_quiz_client,
        api_client=api_client,
        recorder=recorder,
        transcriber=transcriber,
    )

    assert "score" in result
    assert "answered_count" in result
    assert "remaining_count" in result
    assert result["score"] == 80
    assert result["answered_count"] == 2
    assert result["remaining_count"] == 2  # 4 outline items - 2 answered


@phase8
def test_run_interview_uses_send_interview_turn_with_answer_text(
    recording_tts, tmp_path, voice_quiz_client
):
    api_client = _make_interview_api_client(
        turn_payloads=[
            {
                "score": 70,
                "rationale": "ok",
                "feedback": "good",
                "next_action": "finish",
                "question_text": None,
                "finished": True,
                "summary": "summary",
                "transcript_so_far": [],
            }
        ],
    )
    recorder = MagicMock(return_value=tmp_path / "a.wav")
    transcriber = MagicMock(return_value="the user said this")

    _build_interview_driver(
        voice_quiz_client,
        api_client=api_client,
        recorder=recorder,
        transcriber=transcriber,
    )

    api_client.send_interview_turn.assert_called_once()
    positional = api_client.send_interview_turn.call_args.args
    keyword = api_client.send_interview_turn.call_args.kwargs
    # First positional is attempt_id, second is action, third may be answer_text
    assert "the user said this" in (list(positional) + list(keyword.values()))[2:3] or "the user said this" == positional[2]


@phase8
def test_run_interview_module_exposes_run_interview(voice_quiz_client):
    assert hasattr(voice_quiz_client, "run_interview")
