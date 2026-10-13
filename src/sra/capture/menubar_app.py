"""SRA-205 exercise: desktop UI around the recorder and existing pipeline.

The controller is platform independent; its tests describe lifecycle behavior.
The desktop adapter remains a real macOS/manual integration exercise. Importing
this module must neither import desktop libraries nor start an event loop.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

CaptureState = Literal["idle", "recording", "processing", "closed"]
Work = Callable[[], None]
ProcessRecording = Callable[[Path], list[dict[str, Any]]]


class CaptureRecorder(Protocol):
    """The recorder behavior the UI uses; no duplicated audio/file handling."""

    def start(self) -> None: ...

    def stop(self) -> Path: ...

    def close(self) -> None: ...


class CaptureController:
    """Coordinate one capture at a time on the UI thread, with injected adapters.

    submit(work) schedules work on a background worker; dispatch(work) schedules
    a completion on the UI thread. Neither callback executes work synchronously
    in production. show_status(message) updates UI only on its thread. Hotkey
    listeners must dispatch toggle() onto that same thread to serialize state.
    Tests supply queues so timing, WAV ownership, and quit are deterministic.
    """

    state: CaptureState

    def __init__(
        self,
        *,
        recorder_factory: Callable[[], CaptureRecorder],
        process: ProcessRecording,
        submit: Callable[[Work], None],
        dispatch: Callable[[Work], None],
        show_status: Callable[[str], None],
        stop_listener: Work,
    ) -> None:
        """Remember adapters, start idle, and show idle status without opening audio.

        Create a fresh recorder for every capture, including attempts after an
        error. Keep worker and close/quit state explicit enough to prevent an
        overlapping capture and a late completion from reviving a closed app.
        """
        raise NotImplementedError("SRA-205: initialize the capture controller")

    def toggle(self) -> None:
        """Start from idle, or stop/finalize and submit one worker from recording.

        Show recording/processing state. Ignore activations while processing or
        closed. The worker calls process(path) once and closes its recorder in
        finally, only after process has finished reading the file. Dispatch the
        completion to the UI: actual saved count, no speech, or useful error text
        (including unknown save outcome for timeouts), then return to idle.
        Capture/start/stop/worker-scheduling failures also close the recorder and
        return to idle. Never claim a save on error or silently retry a request.
        """
        raise NotImplementedError("SRA-205: coordinate recording and background ingestion")

    def quit(self) -> None:
        """Mark closed and stop the listener exactly once; forbid future toggles.

        Close idle/recording resources immediately without submitting unfinished
        audio. An in-flight worker retains its file until processing ends, then
        closes it in finally. Ignore late UI results after quit. The desktop
        adapter must coordinate worker termination before its event loop exits.
        """
        raise NotImplementedError("SRA-205: coordinate orderly shutdown and cleanup")


class SpacedRepetitionAgentApp:
    """Lazy macOS adapter for CaptureController, rumps, and pynput.

    This import-safe facade's run() should construct the actual rumps.App
    subclass after importing optional desktop packages. Put start/stop and quit
    menu actions on that subclass; connect both menu and configured hotkey to
    controller.toggle through the UI dispatcher. Keep all UI updates on the UI
    thread, run transcription/network calls in a worker, stop/join listeners and
    coordinate worker cleanup before quitting. Use get_client_settings() for
    HOTKEY_COMBO and the existing Recorder/process_recording implementations.

    Explain unsupported platforms, missing desktop extras, invalid shortcuts,
    and denied permissions usefully. Actual hotkey delivery, permission prompts,
    responsive menu behavior, and event-loop shutdown require macOS verification.
    """

    def run(self) -> None:
        """Build the real desktop adapters and launch rumps only when requested."""
        raise NotImplementedError("SRA-205: launch the macOS menu-bar integration")


def main() -> int:
    """Launch the app explicitly; report setup failures with a nonzero exit status."""
    raise NotImplementedError("SRA-205: provide the guarded desktop entry point")


if __name__ == "__main__":
    raise SystemExit(main())
