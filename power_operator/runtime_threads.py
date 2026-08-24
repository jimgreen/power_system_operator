from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from .core import OperatorCore
from .database import Database
from .io_service import OperatorIoBridge, SimulatorIoClient

LOGGER = logging.getLogger(__name__)


class _ManagedWorkerThread:
    """Own one restartable worker thread and expose bounded lifecycle methods."""

    def __init__(
        self,
        *,
        name: str,
        target: Callable[[threading.Event, threading.Event], None],
        startup_timeout: float,
        stop_timeout: float,
    ):
        self.name = name
        self.target = target
        self.startup_timeout = max(0.1, float(startup_timeout))
        self.stop_timeout = max(0.1, float(stop_timeout))
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._last_error: BaseException | None = None
        self._generation = 0

    def _run(
        self,
        stop_event: threading.Event,
        started_event: threading.Event,
    ) -> None:
        try:
            self.target(stop_event, started_event)
        except BaseException as exc:  # keep worker failures observable by the MMI host
            self._last_error = exc
            LOGGER.exception("%s 子线程异常退出", self.name)
        finally:
            # A target that fails during initialization must still release start().
            started_event.set()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            stop_event = threading.Event()
            started_event = threading.Event()
            self._last_error = None
            self._generation += 1
            thread = threading.Thread(
                target=self._run,
                args=(stop_event, started_event),
                name=self.name,
                daemon=False,
            )
            self._stop_event = stop_event
            self._thread = thread
            thread.start()

        if not started_event.wait(self.startup_timeout):
            stop_event.set()
            thread.join(self.stop_timeout)
            raise RuntimeError(f"{self.name} 子线程启动超时")
        if self._last_error is not None:
            thread.join(self.stop_timeout)
            raise RuntimeError(f"{self.name} 子线程启动失败: {self._last_error}") from self._last_error
        if not thread.is_alive():
            raise RuntimeError(f"{self.name} 子线程启动后提前退出")

    def stop_and_wait(self) -> None:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
        if thread is None:
            return
        if thread is threading.current_thread():
            raise RuntimeError(f"{self.name} 子线程不能等待自身退出")
        if stop_event is not None:
            stop_event.set()
        thread.join(self.stop_timeout)
        if thread.is_alive():
            raise RuntimeError(
                f"{self.name} 子线程未能在 {self.stop_timeout:.1f} 秒内退出"
            )
        with self._lock:
            if self._thread is thread:
                self._thread = None
                self._stop_event = None

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def thread_ident(self) -> int | None:
        with self._lock:
            return None if self._thread is None else self._thread.ident

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error


class CoreThreadController:
    """Restartable Core child thread used by MMI and IO clock rollback recovery."""

    thread_name = "operator-core"

    def __init__(
        self,
        *,
        database_path: str | Path,
        poll_seconds: float = 0.5,
        startup_timeout: float = 10.0,
        stop_timeout: float = 10.0,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._worker = _ManagedWorkerThread(
            name=self.thread_name,
            target=self._run,
            startup_timeout=startup_timeout,
            stop_timeout=stop_timeout,
        )

    def _run(
        self,
        stop_event: threading.Event,
        started_event: threading.Event,
    ) -> None:
        database = Database(self.database_path)
        try:
            core = OperatorCore(database)
            started_event.set()
            LOGGER.info(
                "Core 子线程已启动，数据库 %s，轮询周期 %.3f 秒",
                self.database_path,
                self.poll_seconds,
            )
            core.run_forever(self.poll_seconds, stop_event=stop_event)
        finally:
            database.dispose()
            LOGGER.info("Core 子线程已停止")

    def start(self) -> None:
        self._worker.start()

    def stop_and_wait(self) -> None:
        self._worker.stop_and_wait()

    def is_running(self) -> bool:
        return self._worker.is_running()

    @property
    def thread_ident(self) -> int | None:
        return self._worker.thread_ident

    @property
    def generation(self) -> int:
        return self._worker.generation

    @property
    def last_error(self) -> BaseException | None:
        return self._worker.last_error


class IoThreadController:
    """Persistent simulator Bridge child thread managed by the MMI process."""

    thread_name = "operator-io"

    def __init__(
        self,
        *,
        database_path: str | Path,
        core_controller: CoreThreadController,
        simulator_host: str = "127.0.0.1",
        simulator_port: int = 9001,
        rtu_id: int = 1,
        poll_seconds: float = 0.5,
        startup_timeout: float = 10.0,
        stop_timeout: float = 10.0,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.core_controller = core_controller
        self.simulator_host = str(simulator_host)
        self.simulator_port = int(simulator_port)
        self.rtu_id = int(rtu_id)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._worker = _ManagedWorkerThread(
            name=self.thread_name,
            target=self._run,
            startup_timeout=startup_timeout,
            stop_timeout=stop_timeout,
        )

    def _run(
        self,
        stop_event: threading.Event,
        started_event: threading.Event,
    ) -> None:
        database = Database(self.database_path)
        client = SimulatorIoClient(self.simulator_host, self.simulator_port)
        bridge = OperatorIoBridge(
            database,
            transport=client,
            rtu_id=self.rtu_id,
            peer_ip=self.simulator_host,
            peer_port=self.simulator_port,
            core_process_manager=self.core_controller,
        )
        try:
            started_event.set()
            LOGGER.info(
                "IO 子线程已启动，模拟器 %s:%d，RTU %d，轮询周期 %.3f 秒",
                self.simulator_host,
                self.simulator_port,
                self.rtu_id,
                self.poll_seconds,
            )
            bridge.run_forever(self.poll_seconds, stop_event=stop_event)
        finally:
            try:
                bridge.mark_disconnected()
            except Exception:
                LOGGER.exception("IO 子线程停止时更新 RTU 断开状态失败")
            database.dispose()
            LOGGER.info("IO 子线程已停止")

    def start(self) -> None:
        self._worker.start()

    def stop_and_wait(self) -> None:
        self._worker.stop_and_wait()

    def is_running(self) -> bool:
        return self._worker.is_running()

    @property
    def thread_ident(self) -> int | None:
        return self._worker.thread_ident

    @property
    def generation(self) -> int:
        return self._worker.generation

    @property
    def last_error(self) -> BaseException | None:
        return self._worker.last_error


class OperatorRuntimeThreads:
    """MMI-owned runtime: start Core then IO; stop IO then Core."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        simulator_host: str = "127.0.0.1",
        simulator_port: int = 9001,
        rtu_id: int = 1,
        poll_seconds: float = 0.5,
        core_poll_seconds: float = 0.5,
        startup_timeout: float = 10.0,
        stop_timeout: float = 10.0,
    ):
        self.core_controller = CoreThreadController(
            database_path=database_path,
            poll_seconds=core_poll_seconds,
            startup_timeout=startup_timeout,
            stop_timeout=stop_timeout,
        )
        self.io_controller = IoThreadController(
            database_path=database_path,
            core_controller=self.core_controller,
            simulator_host=simulator_host,
            simulator_port=simulator_port,
            rtu_id=rtu_id,
            poll_seconds=poll_seconds,
            startup_timeout=startup_timeout,
            stop_timeout=stop_timeout,
        )

    def start(self) -> None:
        self.core_controller.start()
        try:
            self.io_controller.start()
        except Exception:
            self.core_controller.stop_and_wait()
            raise
        LOGGER.info("MMI 已自动托管 Core、IO 两个子线程: %s", self.snapshot())

    def stop_and_wait(self) -> None:
        errors: list[Exception] = []
        try:
            self.io_controller.stop_and_wait()
        except Exception as exc:
            errors.append(exc)
        try:
            self.core_controller.stop_and_wait()
        except Exception as exc:
            errors.append(exc)
        if errors:
            raise RuntimeError("；".join(str(error) for error in errors)) from errors[0]
        LOGGER.info("MMI 托管的 Core、IO 子线程均已停止")

    def is_running(self) -> bool:
        return self.core_controller.is_running() and self.io_controller.is_running()

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            "core": {
                "name": self.core_controller.thread_name,
                "ident": self.core_controller.thread_ident,
                "generation": self.core_controller.generation,
                "running": self.core_controller.is_running(),
            },
            "io": {
                "name": self.io_controller.thread_name,
                "ident": self.io_controller.thread_ident,
                "generation": self.io_controller.generation,
                "running": self.io_controller.is_running(),
            },
        }
