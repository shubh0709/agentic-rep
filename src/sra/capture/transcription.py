"""SRA-202 exercise: turn a recording into text without taking file ownership."""

from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Protocol


class TranscriptionError(RuntimeError):
    """A recording could not be read or transcribed; retain the original cause."""


class TranscriptSegment(Protocol):
    """The small part of a Whisper segment that this application needs."""

    @property
    def text(self) -> str: ...


class WhisperModelLike(Protocol):
    """Keep inference replaceable without importing the optional Whisper package."""

    def transcribe(self, audio: str) -> tuple[Iterable[TranscriptSegment], object]: ...


def _create_whisper_model(model_size: str) -> WhisperModelLike:
    """Construct the optional faster-whisper model when first requested.

    Learning task: import WhisperModel inside this function, then construct it
    with the requested model size. Do not import it or download weights at
    module import time. This narrow factory is the fake-inference seam used by
    unit and end-to-end tests. The real fixture check opts into the dependency
    and possible first-run download explicitly.
    """
    raise NotImplementedError("SRA-202: lazily construct the local Whisper model")


@lru_cache
def load_model(model_size: str) -> WhisperModelLike:
    """Return one cached model per size, wrapping construction failures.

    Learning task: call the factory and translate construction exceptions into
    TranscriptionError with an informative message and exception chaining. The
    decorator supplies a separate cache entry for each size and does not cache
    failed calls. Tests clear it using load_model.cache_clear(). Keep the cache
    on this boundary so different recordings reuse the same expensive model.
    """
    raise NotImplementedError("SRA-202: load and cache a model by size")


def transcribe(audio_path: Path, model_size: str = "base") -> str:
    """Read a recording and return ordered speech text; silence returns "".

    Learning task:
    - Validate that the path is an existing, regular, readable file before
      loading a model. Briefly open it in binary read mode; avoid permission-bit
      guesses and leave decoding of audio formats to Whisper.
    - Obtain the configured model and pass str(audio_path) to its transcribe
      method. It returns (segments, metadata); segments may be a lazy iterator.
    - Strip each segment's text, omit blank fragments, and join the remaining
      fragments in order with one space. Preserve whitespace inside a fragment.
    - Cover both inference startup and iteration with error handling. Translate
      file/decoder/model errors to TranscriptionError, keeping the cause through
      `raise ... from ...`; preserve a TranscriptionError already raised by the
      model-loading boundary. Never return a partial transcript after a failure.
    Never modify or remove audio_path: its creator owns cleanup.
    """
    raise NotImplementedError("SRA-202: validate and transcribe the recording")
