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

    def transcribe(self, audio: str, *, vad_filter: bool = False) -> tuple[Iterable[TranscriptSegment], object]: ...


_MIN_REPEAT_SPAN_TOKENS = 4


def _collapse_adjacent_repeated_spans(text: str) -> str:
    """Collapse exact adjacent multi-word loops while preserving natural echoes.

    Whisper can occasionally repeat a whole phrase many times. Compare
    whitespace-delimited tokens case-insensitively (punctuation remains part of
    each token), retain the first copy, and remove consecutive later copies.
    The *smallest* repeating unit decides whether a run is eligible: a run of
    ``very`` or ``study daily`` is preserved even when enough copies could be
    mistaken for a synthetic four-token block.

    Return ``text`` unchanged when no collapse occurs so existing internal
    whitespace is preserved for ordinary transcripts.
    """
    tokens = text.split()
    normalized = [token.casefold() for token in tokens]
    kept: list[str] = []
    collapsed = False
    index = 0

    while index < len(tokens):
        remaining = len(tokens) - index
        repeat_size: int | None = None
        for size in range(1, remaining // 2 + 1):
            if normalized[index : index + size] == normalized[
                index + size : index + (2 * size)
            ]:
                repeat_size = size
                break

        if repeat_size is None or repeat_size < _MIN_REPEAT_SPAN_TOKENS:
            kept.append(tokens[index])
            index += 1
            continue

        block = normalized[index : index + repeat_size]
        kept.extend(tokens[index : index + repeat_size])
        index += repeat_size
        copies = 1
        while normalized[index : index + repeat_size] == block:
            index += repeat_size
            copies += 1
        collapsed = collapsed or copies > 1

    return " ".join(kept) if collapsed else text


def _create_whisper_model(model_size: str) -> WhisperModelLike:
    """Construct the optional faster-whisper model when first requested.

    Learning task: import WhisperModel inside this function, then construct it
    with the requested model size. Do not import it or download weights at
    module import time. This narrow factory is the fake-inference seam used by
    unit and end-to-end tests. The real fixture check opts into the dependency
    and possible first-run download explicitly.
    """
    from faster_whisper import WhisperModel  # pyright: ignore[reportMissingImports]

    return WhisperModel(model_size)


@lru_cache
def load_model(model_size: str) -> WhisperModelLike:
    """Return one cached model per size, wrapping construction failures.

    Learning task: call the factory and translate construction exceptions into
    TranscriptionError with an informative message and exception chaining. The
    decorator supplies a separate cache entry for each size and does not cache
    failed calls. Tests clear it using load_model.cache_clear(). Keep the cache
    on this boundary so different recordings reuse the same expensive model.
    """
    try:
        return _create_whisper_model(model_size)
    except Exception as exc:
        raise TranscriptionError(f"Could not load Whisper model {model_size!r}") from exc


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
    try:
        if not audio_path.exists():
            raise FileNotFoundError(audio_path)
        if not audio_path.is_file():
            raise IsADirectoryError(f"Not a regular file: {audio_path}")
        with audio_path.open("rb"):
            pass
    except (OSError, ValueError) as exc:
        raise TranscriptionError(f"Could not read recording {audio_path}") from exc

    try:
        model = load_model(model_size)
    except TranscriptionError:
        raise

    try:
        # Whisper can hallucinate words on silent input. Filter non-speech before
        # decoding so silence cannot become a spurious study-note submission.
        segments, _ = model.transcribe(str(audio_path), vad_filter=True)
        fragments = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                fragments.append(text)
        return _collapse_adjacent_repeated_spans(" ".join(fragments))
    except Exception as exc:
        raise TranscriptionError(f"Could not transcribe recording {audio_path}") from exc
