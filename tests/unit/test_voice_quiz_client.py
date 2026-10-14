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
