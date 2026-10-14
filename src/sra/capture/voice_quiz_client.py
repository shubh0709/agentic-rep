"""SRA-508 — Voice quiz client driver: speak, record, transcribe, send, repeat.

The driver is pure composition. It speaks each question, records one answer,
transcribes the WAV, sends the transcript to the server, and speaks the
feedback + next question until the server reports ``finished=True``. All
grading and state lives server-side in ``quiz_conversation_graph.process_voice_turn``
(SRA-504); this module only owns audio I/O and HTTP turn-taking.

The function returns ``{score, total_questions}``. ``total_questions`` is the
final-score-plus-one fallback used while the voice-turn endpoint only
returns ``score``; the server remains the canonical owner of quiz state, so
callers should treat the number as best-effort.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from sra.capture import tts
from sra.capture.recording import record_audio
from sra.capture.transcription import transcribe


class VoiceQuizError(RuntimeError):
    """A typed failure during a voice quiz attempt.

    Carries a ``code`` attribute so callers can distinguish recorder,
    transcription, server, attempt-already-completed, and interpretation
    failures without parsing the exception message.
    """

    code: str

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        if message is None:
            message = code
        super().__init__(message)


class _VoiceQuizApiClient(Protocol):
    """Duck-typed shape ``run_voice_quiz`` accepts as ``api_client``.

    Tests substitute a ``SimpleNamespace`` with the same methods; the real
    desktop caller passes the module-level ``sra.client.api_client``
    helpers, which expose the same callables. ``credential`` is
    keyword-only on the real helpers but is not required here, so callers
    invoke the helpers positionally with no credential.
    """

    def start_voice_quiz_attempt(self, quiz_set_id: int) -> dict[str, Any]: ...

    def send_voice_turn(
        self, attempt_id: int, answer_text: str
    ) -> dict[str, Any]: ...


Recorder = Callable[[], Path]
Transcriber = Callable[[Path], str]


def record_answer(*, recorder: Recorder | None = None) -> Path:
    """Capture one spoken answer and return the finalized WAV path.

    Wraps the existing recorder (defaults to ``capture.recording.record_audio``).
    Raises ``VoiceQuizError("recorder_failed")`` on any recorder exception;
    the underlying error is chained via ``__cause__``. The WAV is owned by
    the recorder's own lifecycle -- the driver never deletes the file.
    """
    if recorder is None:
        recorder = record_audio
    try:
        return recorder()
    except Exception as exc:
        raise VoiceQuizError("recorder_failed", str(exc)) from exc


def run_voice_quiz(
    quiz_set_id: int,
    *,
    api_client: _VoiceQuizApiClient | None = None,
    recorder: Recorder | None = None,
    transcriber: Transcriber | None = None,
) -> dict[str, int]:
    """Drive a full voice quiz attempt end-to-end; return ``{score, total_questions}``.

    Speaks the first question, then loops: record one answer, transcribe the
    WAV, send the transcript to the server, speak the feedback + next
    question (or the final summary when ``finished=True``). Raises
    ``VoiceQuizError`` on any recorder, transcription, or server failure
    inside the loop; the underlying error is chained via ``__cause__``.
    """
    if api_client is None:
        from sra.client import api_client as _api_module

        client: _VoiceQuizApiClient = _api_module
    else:
        client = api_client

    if transcriber is None:
        transcriber = transcribe

    start = client.start_voice_quiz_attempt(quiz_set_id)
    attempt_id = start["attempt_id"]
    question_text = start["question_text"]

    tts.speak(question_text)

    while True:
        wav_path = record_answer(recorder=recorder)

        try:
            answer_text = transcriber(wav_path)
        except Exception as exc:
            raise VoiceQuizError("transcription_failed", str(exc)) from exc

        try:
            response = client.send_voice_turn(attempt_id, answer_text)
        except Exception as exc:
            code = _classify_server_error(exc)
            raise VoiceQuizError(code, str(exc)) from exc

        if response.get("finished"):
            final_score = int(response.get("score") or 0)
            total_questions = final_score + 1
            tts.speak(f"You scored {final_score} out of {total_questions}.")
            return {"score": final_score, "total_questions": total_questions}

        feedback_text = response.get("feedback_text", "")
        next_question_text = response.get("next_question_text", "")
        tts.speak(f"{feedback_text}. {next_question_text}")


def _classify_server_error(exc: BaseException) -> str:
    """Map an api_client exception to a ``VoiceQuizError.code`` value."""
    message = str(exc).lower()
    if "attempt_completed" in message:
        return "attempt_completed"
    if "interpretation" in message:
        return "interpretation_failed"
    return "server_error"