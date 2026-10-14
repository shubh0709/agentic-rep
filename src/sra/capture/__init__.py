"""Local voice capture exercises (Phase 2); optional devices/models load lazily."""

from sra.capture.tts import reset_backend, set_backend, speak
from sra.capture.voice_quiz_client import run_voice_quiz
from sra.capture.wakeup import deliver_digest, poll_digest_ready, run_wakeup_loop

__all__ = [
    "deliver_digest",
    "poll_digest_ready",
    "reset_backend",
    "run_voice_quiz",
    "run_wakeup_loop",
    "set_backend",
    "speak",
]