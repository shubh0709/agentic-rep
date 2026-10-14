"""Import-safe macOS menu-bar capture with a platform-independent controller."""

import ctypes
import ctypes.util
import importlib
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Any, Literal, Protocol

CaptureState = Literal["idle", "recording", "processing", "closed"]
Work = Callable[[], None]
ProcessRecording = Callable[[Path], list[dict[str, Any]]]


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("event_kind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("hotkey_id", ctypes.c_uint32)]


_EventHandler = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

# Carbon modifier masks and hardware-independent ANSI key positions. RegisterEventHotKey
# consumes a virtual key code, so keeping the supported set explicit also prevents a
# configured shortcut from silently registering the wrong physical key.
_MODIFIERS = {
    "cmd": 1 << 8,
    "command": 1 << 8,
    "shift": 1 << 9,
    "alt": 1 << 11,
    "option": 1 << 11,
    "ctrl": 1 << 12,
    "control": 1 << 12,
}
_KEY_CODES = {
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "9": 25,
    "7": 26,
    "8": 28,
    "0": 29,
    "o": 31,
    "u": 32,
    "i": 34,
    "p": 35,
    "return": 36,
    "enter": 36,
    "l": 37,
    "j": 38,
    "k": 40,
    "n": 45,
    "m": 46,
    "tab": 48,
    "space": 49,
    "escape": 53,
    "esc": 53,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}


def _parse_hotkey(combo: str) -> tuple[int, int]:
    """Translate ``<ctrl>+<alt>+r`` into Carbon modifier and key codes."""
    tokens = [token.strip().lower().removeprefix("<").removesuffix(">") for token in combo.split("+")]
    if any(not token for token in tokens):
        raise ValueError("empty shortcut part")

    modifiers = 0
    key_code: int | None = None
    seen_modifiers: set[int] = set()
    for token in tokens:
        if token in _MODIFIERS:
            modifier = _MODIFIERS[token]
            if modifier in seen_modifiers:
                raise ValueError(f"duplicate modifier {token!r}")
            seen_modifiers.add(modifier)
            modifiers |= modifier
        elif token in _KEY_CODES and key_code is None:
            key_code = _KEY_CODES[token]
        else:
            raise ValueError(f"unsupported key or extra non-modifier {token!r}")
    if not modifiers or key_code is None:
        raise ValueError("shortcut requires at least one modifier and one supported key")
    return modifiers, key_code


class _MacOSHotKey:
    """Register one exact shortcut without observing general keyboard input."""

    _KEYBOARD_EVENT_CLASS = int.from_bytes(b"keyb", "big")
    _HOTKEY_PRESSED = 6
    _SIGNATURE = int.from_bytes(b"SRA1", "big")

    def __init__(self, combo: str, callback: Work) -> None:
        self._modifiers, self._key_code = _parse_hotkey(combo)
        self._callback = callback
        self._carbon: Any | None = None
        self._handler_ref = ctypes.c_void_p()
        self._hotkey_ref = ctypes.c_void_p()
        self._handler: Any | None = None
        self._active = False

    @staticmethod
    def _configure(carbon: Any) -> None:
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            _EventHandler,
            ctypes.c_uint32,
            ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            _EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.restype = ctypes.c_int32

    def start(self) -> None:
        if self._active:
            return
        library = ctypes.util.find_library("Carbon")
        if not library:
            raise RuntimeError("macOS Carbon hotkey services are unavailable")
        carbon = ctypes.CDLL(library)
        self._configure(carbon)

        def handle_event(_next_handler: Any, _event: Any, _data: Any) -> int:
            self._callback()
            return 0

        self._handler = _EventHandler(handle_event)
        event_type = _EventTypeSpec(self._KEYBOARD_EVENT_CLASS, self._HOTKEY_PRESSED)
        target = carbon.GetApplicationEventTarget()
        status = carbon.InstallEventHandler(
            target, self._handler, 1, ctypes.byref(event_type), None, ctypes.byref(self._handler_ref)
        )
        if status != 0:
            raise RuntimeError(f"could not install the global-hotkey handler (macOS error {status})")

        hotkey_id = _EventHotKeyID(self._SIGNATURE, 1)
        status = carbon.RegisterEventHotKey(
            self._key_code, self._modifiers, hotkey_id, target, 0, ctypes.byref(self._hotkey_ref)
        )
        if status != 0:
            carbon.RemoveEventHandler(self._handler_ref)
            self._handler_ref = ctypes.c_void_p()
            raise RuntimeError(f"shortcut is unavailable or already in use (macOS error {status})")
        self._carbon = carbon
        self._active = True

    def stop(self) -> None:
        if not self._active or self._carbon is None:
            return
        self._carbon.UnregisterEventHotKey(self._hotkey_ref)
        self._carbon.RemoveEventHandler(self._handler_ref)
        self._hotkey_ref = ctypes.c_void_p()
        self._handler_ref = ctypes.c_void_p()
        self._handler = None
        self._carbon = None
        self._active = False

    def is_alive(self) -> bool:
        return self._active

    def join(self) -> None:
        """Match the former listener lifecycle; Carbon callbacks use the app loop."""


class CaptureRecorder(Protocol):
    """The recorder behavior the UI uses; no duplicated audio/file handling."""

    def start(self) -> None: ...

    def stop(self) -> Path: ...

    def close(self) -> None: ...


class CaptureController:
    """Serialize capture on the UI thread; transfer WAV ownership to the worker.

    submit and dispatch must queue callbacks, not execute them synchronously.
    Hotkey listeners dispatch toggle onto the UI thread as well.
    """

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
        self.state: CaptureState = "idle"
        self._factory = recorder_factory
        self._process = process
        self._submit = submit
        self._dispatch = dispatch
        self._show = show_status
        self._stop_listener = stop_listener
        self._recorder: CaptureRecorder | None = None
        self._show("Idle — ready to record")

    @staticmethod
    def _close(recorder: CaptureRecorder | None, message: str) -> str:
        if recorder is not None:
            try:
                recorder.close()
            except Exception as exc:
                message += f"; cleanup failed: {exc}"
        return message

    def _complete(self, message: str) -> None:
        if self.state != "closed":
            self.state = "idle"
            self._show(message)

    def toggle(self) -> None:
        """Start or finalize one recording; ignore toggles until its work completes."""
        if self.state in {"processing", "closed"}:
            return
        try:
            if self.state == "idle":
                self._recorder = self._factory()
                self._recorder.start()
                self.state = "recording"
                self._show("Recording — toggle again to save")
                return

            assert self._recorder is not None
            recorder = self._recorder
            path = recorder.stop()
            self.state = "processing"
            self._show("Processing — transcribing and submitting")

            def work() -> None:
                message = "Processing interrupted; save outcome may be unknown; check search before retrying"
                try:
                    entries = self._process(path)
                    if entries:
                        ids = ", ".join(str(entry["id"]) for entry in entries)
                        message = f"Saved {len(entries)} entries (IDs: {ids})"
                    else:
                        message = "No speech detected — nothing submitted"
                except Exception as exc:
                    message = f"Capture failed: {exc}"
                finally:
                    message = self._close(recorder, message)
                    self._dispatch(lambda: self._complete(message))

            self._submit(work)
            # Only the worker owns the finalized WAV after successful scheduling.
            self._recorder = None
        except Exception as exc:
            message = self._close(self._recorder, f"Capture failed: {exc}")
            self._recorder = None
            self._complete(message)

    def quit(self) -> None:
        """Stop accepting input; a queued/running worker keeps its WAV until done."""
        if self.state == "closed":
            return
        self.state = "closed"
        try:
            self._stop_listener()
        finally:
            message = self._close(self._recorder, "Closing")
            self._recorder = None
            self._show(message)


class SpacedRepetitionAgentApp:
    """Load desktop dependencies only when explicitly launched on macOS."""

    def run(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("The menu-bar app requires macOS; use the HTTP capture client on other platforms.")
        try:
            rumps = importlib.import_module("rumps")
        except ImportError as exc:
            raise RuntimeError('Desktop support is missing; run pip install -e ".[desktop]".') from exc

        from sra.capture.pipeline import process_recording
        from sra.capture.recording import Recorder
        from sra.client.auth_client import (
            DesktopAuthError,
            MacOSKeychainCredentialStore,
            load_default_authenticated_account,
            sign_in,
            sign_out,
        )
        from sra.config import get_client_settings

        settings = get_client_settings()
        combo = settings.hotkey_combo
        try:
            _parse_hotkey(combo)
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid HOTKEY_COMBO {combo!r}: {exc}; use a shortcut such as <ctrl>+<alt>+r."
            ) from exc

        class DesktopApp(rumps.App):
            def __init__(self) -> None:
                super().__init__("Spaced Repetition Agent", title="SRA", quit_button=None)
                self.callbacks: Queue[Work] = Queue()
                self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sra-capture")
                self.pending: Future[None] | None = None
                self.account: Any | None = None
                self.auth_busy = False
                self.auth_cancel = Event()
                self.hotkey_attempted = False
                self.status_item = rumps.MenuItem("Idle")
                self.toggle_item = rumps.MenuItem("Start recording", callback=self.toggle_capture)
                self.sign_in_item = rumps.MenuItem("Sign in", callback=self.begin_sign_in)
                self.restore_item = rumps.MenuItem("Use saved sign-in", callback=self.begin_restore)
                self.sign_out_item = rumps.MenuItem("Sign out", callback=self.begin_sign_out)
                self.cancel_sign_in_item = rumps.MenuItem("Cancel sign-in", callback=self.cancel_sign_in)
                self.account_item = rumps.MenuItem("Account: signed out")
                # Keep Quit in the established position for existing desktop
                # adapters while adding authentication actions after it.
                self.menu = [
                    self.status_item,
                    self.toggle_item,
                    rumps.MenuItem("Quit", callback=self.quit_capture),
                    self.account_item,
                    self.sign_in_item,
                    self.restore_item,
                    self.sign_out_item,
                    self.cancel_sign_in_item,
                ]
                self.keychain = MacOSKeychainCredentialStore(service_prefix=settings.keychain_service_prefix)
                self.hotkey = _MacOSHotKey(combo, lambda: self.callbacks.put(self.request_capture))

                def authenticated_process(path: Path) -> list[dict[str, Any]]:
                    if self.account is None:
                        return process_recording(path)
                    return process_recording(path, credential=self.account.credential)

                self.controller = CaptureController(
                    recorder_factory=Recorder, process=authenticated_process, submit=self.submit,
                    dispatch=self.callbacks.put, show_status=self.show_status, stop_listener=self.hotkey.stop,
                )
                # Construct/start the timer on the main thread: rumps attaches it to
                # that thread's Cocoa run loop. Background threads only enqueue work.
                self.timer = rumps.Timer(self.tick, 0.05)

            def show_status(self, message: str) -> None:
                self.status_item.title = message
                state = self.controller.state if hasattr(self, "controller") else "idle"
                self.title = {"idle": "SRA", "recording": "SRA ●", "processing": "SRA …", "closed": "SRA …"}[state]
                self.toggle_item.title = {
                    "idle": "Start recording", "recording": "Stop and save",
                    "processing": "Processing…", "closed": "Waiting for cleanup…",
                }[state]
                print(message, flush=True)

            def submit(self, work: Work) -> None:
                self.pending = self.executor.submit(work)

            def toggle_capture(self, _sender: Any) -> None:
                self.callbacks.put(self.request_capture)

            def request_capture(self) -> None:
                if self.auth_busy:
                    self.show_status("Sign-in work is still in progress")
                    return
                self.controller.toggle()

            def _complete_auth(self, message: str) -> None:
                if self.controller.state == "closed":
                    return
                self.auth_busy = False
                if self.account is None:
                    self.account_item.title = "Account: signed out"
                else:
                    self.account_item.title = f"Account: {self.account.email}"
                self.show_status(message)

            def begin_sign_in(self, _sender: Any) -> None:
                self.callbacks.put(self._start_sign_in)

            def _start_sign_in(self) -> None:
                if self.auth_busy or self.controller.state != "idle":
                    self.show_status("Finish the current operation before signing in")
                    return
                self.auth_busy = True
                self.auth_cancel.clear()
                self.show_status("Sign in in your browser")

                def work() -> None:
                    try:
                        self.account = sign_in(
                            api_base_url=settings.api_base_url,
                            timeout_seconds=settings.desktop_login_timeout_seconds,
                            store=self.keychain,
                            cancel_event=self.auth_cancel,
                        )
                        message = f"Signed in as {self.account.email}"
                    except DesktopAuthError as exc:
                        self.account = None
                        message = f"Sign-in failed: {exc}"
                    except Exception:
                        self.account = None
                        message = "Sign-in failed unexpectedly. Please try again."
                    self.callbacks.put(lambda: self._complete_auth(message))

                self.submit(work)

            def cancel_sign_in(self, _sender: Any) -> None:
                self.auth_cancel.set()
                if self.auth_busy:
                    self.show_status("Cancelling sign-in")

            def begin_sign_out(self, _sender: Any) -> None:
                self.callbacks.put(self._start_sign_out)

            def begin_restore(self, _sender: Any) -> None:
                self.callbacks.put(self._start_restore)

            def _start_restore(self) -> None:
                if self.auth_busy or self.controller.state != "idle":
                    self.show_status("Finish the current operation before restoring sign-in")
                    return
                self.auth_busy = True
                self.show_status("Checking saved sign-in")

                def work() -> None:
                    try:
                        self.account = load_default_authenticated_account(
                            api_base_url=settings.api_base_url,
                            store=self.keychain,
                        )
                        message = f"Signed in as {self.account.email}"
                    except DesktopAuthError as exc:
                        self.account = None
                        message = f"Saved sign-in unavailable: {exc}"
                    except Exception:
                        self.account = None
                        message = "Saved sign-in could not be checked safely. Please sign in again."
                    self.callbacks.put(lambda: self._complete_auth(message))

                self.submit(work)

            def _start_sign_out(self) -> None:
                if self.auth_busy or self.controller.state != "idle":
                    self.show_status("Finish the current operation before signing out")
                    return
                if self.account is None:
                    self.show_status("Already signed out")
                    return
                account = self.account
                self.auth_busy = True
                self.show_status("Signing out")

                def work() -> None:
                    try:
                        sign_out(api_base_url=settings.api_base_url, account=account.subject, store=self.keychain)
                        self.account = None
                        message = "Signed out"
                    except DesktopAuthError as exc:
                        message = f"Sign-out failed: {exc}"
                    except Exception:
                        message = "Sign-out failed unexpectedly. Please try again."
                    self.callbacks.put(lambda: self._complete_auth(message))

                self.submit(work)

            def quit_capture(self, _sender: Any) -> None:
                self.auth_cancel.set()
                self.controller.quit()
                self.show_status("Closing — waiting for any submission and audio cleanup")

            def tick(self, _sender: Any) -> None:
                # rumps creates NSApplication inside App.run(). Carbon needs that
                # application target, so registration belongs on the first loop tick.
                if not self.hotkey_attempted:
                    self.hotkey_attempted = True
                    try:
                        self.hotkey.start()
                    except RuntimeError as exc:
                        self.show_status(f"Global hotkey unavailable: {exc}; the recording menu remains available")
                # Bound each drain so key-repeat cannot starve the event loop.
                for _ in range(100):
                    try:
                        callback = self.callbacks.get_nowait()
                    except Empty:
                        break
                    callback()
                if self.controller.state == "closed":
                    if (self.pending is None or self.pending.done()) and not self.hotkey.is_alive():
                        rumps.quit_application()

            def launch(self) -> None:
                try:
                    self.timer.start()
                    self.run()
                finally:
                    self.auth_cancel.set()
                    try:
                        self.controller.quit()
                    finally:
                        self.timer.stop()
                        self.executor.shutdown(wait=True)
                        self.hotkey.join()

        DesktopApp().launch()


def main() -> int:
    """Launch explicitly and report setup failures with a nonzero exit status."""
    try:
        SpacedRepetitionAgentApp().run()
    except Exception as exc:
        print(f"Could not run menu-bar capture: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
