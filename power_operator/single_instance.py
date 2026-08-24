from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from types import TracebackType


DEFAULT_OPERATOR_MMI_INSTANCE_NAME = "PowerSystemOperator.operator_mmi"


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
        return True

    def release(self) -> None:
        with self._lock:
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
