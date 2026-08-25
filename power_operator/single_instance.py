from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any


DEFAULT_OPERATOR_MMI_INSTANCE_NAME = "PowerSystemOperator.operator_mmi"
OPERATOR_WINDOW_TITLE = "电力系统操作员人机界面"


@dataclass(frozen=True)
class InstanceOwner:
    """Diagnostic information published by the process holding the lock."""

    pid: int
    executable: str
    argv: tuple[str, ...]
    cwd: str
    started_at: float

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> InstanceOwner:
        pid = int(payload["pid"])
        if pid <= 0:
            raise ValueError("单实例持有者 PID 必须大于 0")
        return cls(
            pid=pid,
            executable=str(payload.get("executable", "")),
            argv=tuple(str(value) for value in payload.get("argv", ())),
            cwd=str(payload.get("cwd", "")),
            started_at=float(payload.get("started_at", 0.0)),
        )


def _owner_record_path(name: str) -> Path:
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"power-operator-{digest}.owner.json"


def _restore_native_window(handle: int) -> bool:
    """Restore one native top-level window, including STARTF-hidden windows."""

    if os.name != "nt":
        return True
    if int(handle) <= 0:
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    show_window = user32.ShowWindow
    show_window.argtypes = (wintypes.HWND, ctypes.c_int)
    show_window.restype = wintypes.BOOL
    is_window_visible = user32.IsWindowVisible
    is_window_visible.argtypes = (wintypes.HWND,)
    is_window_visible.restype = wintypes.BOOL
    set_foreground_window = user32.SetForegroundWindow
    set_foreground_window.argtypes = (wintypes.HWND,)
    set_foreground_window.restype = wintypes.BOOL

    # The first ShowWindow call may be overridden by STARTUPINFO.wShowWindow
    # when a caller accidentally uses ``Start-Process -WindowStyle Hidden``.
    # Calling it again with SW_RESTORE makes the MMI visible instead of leaving
    # an invisible Python process holding the single-instance mutex.
    show_window(int(handle), 9)  # SW_RESTORE
    set_foreground_window(int(handle))
    return bool(is_window_visible(int(handle)))


def _windows_for_process(pid: int) -> list[tuple[int, str, str]]:
    """Return top-level windows for *pid*, including hidden windows."""

    if os.name != "nt" or int(pid) <= 0:
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_windows_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )
    enum_windows = user32.EnumWindows
    enum_windows.argtypes = (enum_windows_proc, wintypes.LPARAM)
    enum_windows.restype = wintypes.BOOL
    get_window_process = user32.GetWindowThreadProcessId
    get_window_process.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    get_window_process.restype = wintypes.DWORD
    get_window_text_length = user32.GetWindowTextLengthW
    get_window_text_length.argtypes = (wintypes.HWND,)
    get_window_text_length.restype = ctypes.c_int
    get_window_text = user32.GetWindowTextW
    get_window_text.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_window_text.restype = ctypes.c_int
    get_class_name = user32.GetClassNameW
    get_class_name.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    get_class_name.restype = ctypes.c_int
    windows: list[tuple[int, str, str]] = []

    @enum_windows_proc
    def collect(handle: int, _parameter: int) -> bool:
        process_id = wintypes.DWORD()
        get_window_process(handle, ctypes.byref(process_id))
        if int(process_id.value) != int(pid):
            return True

        title_length = get_window_text_length(handle)
        title_buffer = ctypes.create_unicode_buffer(max(1, title_length + 1))
        get_window_text(handle, title_buffer, len(title_buffer))
        class_buffer = ctypes.create_unicode_buffer(256)
        get_class_name(handle, class_buffer, len(class_buffer))
        windows.append((int(handle), title_buffer.value, class_buffer.value))
        return True

    enum_windows(collect, 0)
    return windows


def activate_existing_instance(pid: int) -> bool:
    """Bring an existing operator MMI window back when it was hidden."""

    candidates = _windows_for_process(int(pid))
    candidates.sort(
        key=lambda row: (
            row[1] == OPERATOR_WINDOW_TITLE,
            row[2].startswith("Qt") and bool(row[1]),
        ),
        reverse=True,
    )
    for handle, title, class_name in candidates:
        if title != OPERATOR_WINDOW_TITLE and not (
            class_name.startswith("Qt") and bool(title)
        ):
            continue
        if _restore_native_window(handle):
            return True
    return False


def ensure_window_visible(window: Any) -> bool:
    """Show the Qt window twice so a hidden startup flag cannot consume it."""

    window.show()
    window.showNormal()
    window.raise_()
    window.activateWindow()
    return _restore_native_window(int(window.winId()))


class SingleInstanceGuard:
    """Hold an operating-system lock for one application instance.

    Windows uses an atomic named mutex, so two simultaneous launches cannot
    both become the owner.  POSIX systems use ``flock`` on a stable file in the
    system temporary directory.  The operating system releases either lock if
    the process exits unexpectedly.
    """

    def __init__(self, name: str = DEFAULT_OPERATOR_MMI_INSTANCE_NAME):
        normalized_name = str(name).strip()
        if not normalized_name:
            raise ValueError("单实例名称不能为空")
        self.name = normalized_name
        self._lock = threading.RLock()
        self._handle = None
        self._file_descriptor: int | None = None
        self._owner_token = uuid.uuid4().hex
        self._owner_path = _owner_record_path(self.name)

    def acquire(self) -> bool:
        """Acquire the instance lock without waiting.

        Returns ``True`` for the owner and ``False`` when another process
        already owns the same instance name.  Re-acquiring the same guard is
        idempotent.
        """

        with self._lock:
            if self._handle is not None or self._file_descriptor is not None:
                return True
            if os.name == "nt":
                return self._acquire_windows_mutex()
            return self._acquire_posix_file_lock()

    def _acquire_windows_mutex(self) -> bool:
        import ctypes
        from ctypes import wintypes

        error_already_exists = 183
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = create_mutex(None, False, f"Local\\{self.name}")
        last_error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(last_error)
        if last_error == error_already_exists:
            close_handle(handle)
            return False
        self._handle = handle
        self._publish_owner()
        return True

    def _acquire_posix_file_lock(self) -> bool:
        import fcntl

        digest = hashlib.sha256(self.name.encode("utf-8")).hexdigest()[:24]
        lock_path = Path(tempfile.gettempdir()) / f"power-operator-{digest}.lock"
        file_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(file_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(file_descriptor)
            return False
        except Exception:
            os.close(file_descriptor)
            raise

        os.ftruncate(file_descriptor, 0)
        os.write(
            file_descriptor,
            f"pid={os.getpid()} name={self.name}\n".encode("utf-8"),
        )
        self._file_descriptor = file_descriptor
        self._publish_owner()
        return True

    def _publish_owner(self) -> None:
        payload = {
            "pid": os.getpid(),
            "executable": sys.executable,
            "argv": list(sys.argv),
            "cwd": os.getcwd(),
            "started_at": time.time(),
            "token": self._owner_token,
        }
        temporary_path = self._owner_path.with_name(
            f".{self._owner_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary_path, self._owner_path)
        except OSError:
            # The operating-system lock remains authoritative.  Metadata is
            # diagnostic only and must never weaken the atomic lock.
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def existing_owner(self) -> InstanceOwner | None:
        """Read the owner metadata associated with this lock name."""

        try:
            payload = json.loads(self._owner_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            return InstanceOwner.from_payload(payload)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return None

    def _remove_owner_record(self) -> None:
        try:
            payload = json.loads(self._owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict) or payload.get("token") != self._owner_token:
            return
        try:
            self._owner_path.unlink(missing_ok=True)
        except OSError:
            pass

    def release(self) -> None:
        with self._lock:
            if self._handle is not None or self._file_descriptor is not None:
                # Remove the record while the OS lock is still held.  This
                # prevents an old owner from deleting a replacement owner's
                # freshly published metadata during a handover race.
                self._remove_owner_record()

            if self._handle is not None:
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                close_handle = kernel32.CloseHandle
                close_handle.argtypes = (wintypes.HANDLE,)
                close_handle.restype = wintypes.BOOL
                close_handle(self._handle)
                self._handle = None

            if self._file_descriptor is not None:
                import fcntl

                file_descriptor = self._file_descriptor
                self._file_descriptor = None
                try:
                    fcntl.flock(file_descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(file_descriptor)

    def __enter__(self) -> SingleInstanceGuard:
        if not self.acquire():
            raise RuntimeError(f"实例 {self.name} 已经在运行")
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            # Process teardown can unload ctypes/fcntl before object cleanup.
            pass
