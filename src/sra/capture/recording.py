"""SRA-204 exercise: own microphone capture and its temporary WAV lifetime.

Work through tests/unit/test_recording.py one behavior at a time. Keep optional
sounddevice imports inside the device adapter so the backend imports offline.
"""

from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

from sra.capture.pipeline import process_recording  # noqa: F401 — dependency seam for the CLI exercise

AudioCallback = Callable[[Any, int, Any, Any], None]


class RecordingError(RuntimeError):
    """A useful device, permission, capture, or recording-state failure."""


class AudioStream(Protocol):
    """The small sounddevice stream boundary replaced by a fake in tests."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


def _open_audio_stream(*, sample_rate: int, channels: int, callback: AudioCallback) -> AudioStream:
    """Lazily import sounddevice and create its RawInputStream without starting it.

    Pass samplerate=sample_rate, channels=channels, dtype='int16', and callback.
    Raw stream callbacks supply a bytes-like buffer, frame count, timing object,
    and status. Recorder owns starting, stopping, and closing this resource.
    """
    raise NotImplementedError("SRA-204: create the optional microphone stream")


class Recorder:
    """Own one capture's device and unique temporary WAV until close/context exit.

    Construction and context entry do not open a microphone. Invalid transitions
    raise RecordingError. close() is safe before start, after failure, and twice;
    a closed recorder cannot restart. Use a fresh instance for the next attempt.
    Never remove another recorder's or the user's existing files.
    """

    def __init__(self, *, temp_dir: Path | None = None, sample_rate: int = 16000, channels: int = 1) -> None:
        """Remember capture options and initialize lifecycle/resource state only.

        Reject nonpositive sample_rate/channels with ValueError before accessing
        the microphone. temp_dir is an optional directory for owned audio.
        """
        raise NotImplementedError("SRA-204: initialize recorder ownership and state")

    def start(self) -> None:
        """Allocate a unique WAV and acquire/start exactly one audio stream.

        Store callback buffers as signed 16-bit PCM using the configured sample
        rate and channel count. Copy bytes that may be reused by the audio API.
        Remember callback overflow/capture errors for stop() to report; a failed
        capture must never be submitted as successful audio. Translate device or
        permission failures into RecordingError with the original cause/message,
        and release partially allocated resources. Reject an active/closed state.
        """
        raise NotImplementedError("SRA-204: start recording and collect PCM frames")

    def stop(self) -> Path:
        """Stop/close the device and finalize a readable PCM WAV, returning its path.

        Require an active capture. Keep the returned audio until close() so the
        downstream pipeline can read it. On stream/callback failure, still close
        the device and discard unusable owned audio, then raise RecordingError.
        A successful stop closes the WAV header before its caller can read it.
        """
        raise NotImplementedError("SRA-204: finalize the WAV without deleting it")

    def close(self) -> None:
        """Idempotently release the device/WAV and delete only this recorder's audio.

        Handle an unfinished recording and partial start/stop failures. Attempt
        remaining cleanup even if device shutdown fails; do not mask an earlier
        pipeline exception or cancellation during context exit.
        """
        raise NotImplementedError("SRA-204: release owned recording resources")

    def __enter__(self) -> Self:
        """Return the recorder without starting it; context exit owns cleanup."""
        raise NotImplementedError("SRA-204: enter recorder context")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Always close owned resources and allow the caller's exception to propagate."""
        raise NotImplementedError("SRA-204: clean up on every context exit")


def main() -> int:
    """Run Enter-to-start, Enter-to-stop capture, then process the WAV once.

    Use Recorder as a context manager; print recording and processing feedback,
    then each returned entry's actual id/topic or an explicit no-speech message.
    Keep the path readable throughout process_recording and close it afterward.
    Return 0 on successful/no-speech completion, 1 on a typed capture/pipeline
    failure, and 130 on KeyboardInterrupt or EOFError. Cancellation must never
    submit unfinished audio. Display original useful error text, including an
    HTTP timeout's unknown-save-outcome wording; never retry an ingestion.
    """
    raise NotImplementedError("SRA-204: drive terminal capture through the pipeline")


if __name__ == "__main__":
    raise SystemExit(main())
