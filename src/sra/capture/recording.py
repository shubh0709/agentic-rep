"""Own microphone capture and its temporary WAV lifetime.

Work through tests/unit/test_recording.py one behavior at a time. Keep optional
sounddevice imports inside the device adapter so the backend imports offline.
"""

import importlib
import sys
import tempfile
import wave
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

from sra.capture.pipeline import process_recording
from sra.capture.transcription import TranscriptionError
from sra.client.api_client import ApiClientError

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
    try:
        sounddevice = importlib.import_module("sounddevice")
    except ImportError as exc:
        raise RecordingError('Microphone support is missing; install with pip install -e ".[desktop]".') from exc
    return sounddevice.RawInputStream(samplerate=sample_rate, channels=channels, dtype="int16", callback=callback)


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
        if sample_rate <= 0 or channels <= 0:
            raise ValueError("sample_rate and channels must be positive")
        self._temp_dir = temp_dir
        self._sample_rate = sample_rate
        self._channels = channels
        self._state = "new"
        self._stream: AudioStream | None = None
        self._path: Path | None = None
        self._chunks: list[bytes] = []
        self._capture_error: Exception | None = None

    def _capture(self, data: Any, frames: int, time: Any, status: Any) -> None:
        # PortAudio cannot propagate callback exceptions to the calling thread.
        # Copy reusable buffers here; disk I/O happens after device shutdown.
        if self._capture_error is not None:
            return
        try:
            if status:
                raise RecordingError(f"Microphone capture failed: {status}")
            chunk = bytes(data)
            if len(chunk) != frames * self._channels * 2:
                raise RecordingError("Microphone capture returned incomplete PCM frames")
            self._chunks.append(chunk)
        except Exception as exc:
            self._capture_error = exc

    def _release_stream(self) -> None:
        stream = self._stream
        if stream is None:
            return
        error: BaseException | None = None
        try:
            stream.stop()
        except BaseException as exc:
            error = exc
        try:
            stream.close()
            self._stream = None
        except BaseException as exc:
            if error is None:
                error = exc
        if error is not None:
            raise error

    def start(self) -> None:
        """Allocate a unique WAV and acquire/start exactly one audio stream.

        Store callback buffers as signed 16-bit PCM using the configured sample
        rate and channel count. Copy bytes that may be reused by the audio API.
        Remember callback overflow/capture errors for stop() to report; a failed
        capture must never be submitted as successful audio. Translate device or
        permission failures into RecordingError with the original cause/message,
        and release partially allocated resources. Reject an active/closed state.
        """
        if self._state != "new":
            raise RecordingError(f"Cannot start a {self._state} recorder; use a fresh Recorder")
        self._state = "starting"
        try:
            with tempfile.NamedTemporaryFile(
                prefix="sra-recording-", suffix=".wav", dir=self._temp_dir, delete=False,
            ) as audio:
                self._path = Path(audio.name)
            self._stream = _open_audio_stream(
                sample_rate=self._sample_rate, channels=self._channels, callback=self._capture,
            )
            self._stream.start()
            self._state = "recording"
        except BaseException as exc:
            with suppress(BaseException):
                self.close()
            if isinstance(exc, Exception):
                raise RecordingError(
                    f"Could not start microphone recording: {exc}. "
                    "Check the default input device and microphone permission for the app running Python."
                ) from exc
            raise

    def stop(self) -> Path:
        """Stop/close the device and finalize a readable PCM WAV, returning its path.

        Require an active capture. Keep the returned audio until close() so the
        downstream pipeline can read it. On stream/callback failure, still close
        the device and discard unusable owned audio, then raise RecordingError.
        A successful stop closes the WAV header before its caller can read it.
        """
        if self._state != "recording":
            raise RecordingError("Cannot stop: recorder is not recording")
        self._state = "stopping"
        try:
            self._release_stream()
            if self._capture_error is not None:
                raise self._capture_error
            assert self._path is not None
            with wave.open(str(self._path), "wb") as wav:
                wav.setparams((self._channels, 2, self._sample_rate, 0, "NONE", "not compressed"))
                for chunk in self._chunks:
                    wav.writeframesraw(chunk)
            self._chunks.clear()
            self._state = "stopped"
            return self._path
        except BaseException as exc:
            with suppress(BaseException):
                self.close()
            if isinstance(exc, Exception):
                raise RecordingError(f"Could not finalize microphone recording: {exc}") from exc
            raise

    def close(self) -> None:
        """Idempotently release the device/WAV and delete only this recorder's audio.

        Handle an unfinished recording and partial start/stop failures. Attempt
        remaining cleanup even if device shutdown fails; do not mask an earlier
        pipeline exception or cancellation during context exit.
        """
        self._state = "closed"
        error: BaseException | None = None
        try:
            self._release_stream()
        except BaseException as exc:
            error = exc
        self._chunks.clear()
        try:
            if self._path is not None:
                self._path.unlink(missing_ok=True)
                self._path = None
        except BaseException as exc:
            if error is None:
                error = exc
        if isinstance(error, Exception):
            raise RecordingError(f"Could not clean up microphone recording: {error}") from error
        if error is not None:
            raise error

    def __enter__(self) -> Self:
        """Return the recorder without starting it; context exit owns cleanup."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Always close owned resources and allow the caller's exception to propagate."""
        if exc_value is None:
            self.close()
        else:
            # Cleanup must not replace a processing error or interruption.
            with suppress(BaseException):
                self.close()


def record_audio() -> Path:
    """Record one capture from the microphone and return its finalized WAV path.

    Blocks on Enter to start, Enter to stop. The WAV lifecycle is owned by
    the :class:`Recorder` context manager; cleanup happens on every exit
    path. Callers own any downstream transcription/ingestion -- this
    helper only produces a readable PCM WAV path.
    """
    input("Press Enter to start recording (Ctrl-C to cancel): ")
    with Recorder() as recorder:
        recorder.start()
        print("Recording…", flush=True)
        input("Press Enter to stop recording: ")
        return recorder.stop()


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
    try:
        input("Press Enter to start recording (Ctrl-C to cancel): ")
        with Recorder() as recorder:
            recorder.start()
            print("Recording…", flush=True)
            input("Press Enter to stop recording: ")
            path = recorder.stop()
            print(f"Processing recording: {path}", flush=True)
            entries = process_recording(path)
        if entries:
            for entry in entries:
                print(f"Saved {entry['id']}: {entry['topic']}")
        else:
            print("No speech detected; no entries submitted.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Recording cancelled.", file=sys.stderr)
        return 130
    except (RecordingError, TranscriptionError, ApiClientError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
