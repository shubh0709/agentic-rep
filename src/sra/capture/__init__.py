"""Local voice capture exercises (Phase 2); optional devices/models load lazily."""

from sra.capture.tts import reset_backend, set_backend, speak

__all__ = [
    "reset_backend",
    "set_backend",
    "speak",
]