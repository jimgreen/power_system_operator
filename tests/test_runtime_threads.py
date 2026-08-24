from __future__ import annotations

import socket
import time

from power_operator.database import Database, initialize_database
from power_operator.runtime_threads import CoreThreadController, OperatorRuntimeThreads


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def test_core_thread_controller_can_stop_and_restart(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.dispose()

    controller = CoreThreadController(
        database_path=tmp_path / "ems.db",
        poll_seconds=0.01,
        startup_timeout=2.0,
        stop_timeout=2.0,
    )

    controller.start()
    try:
        assert controller.is_running()
        assert controller.thread_name == "operator-core"
        assert controller.thread_ident is not None
        assert controller.generation == 1
    finally:
        controller.stop_and_wait()

    assert not controller.is_running()
    controller.start()
    try:
        assert controller.is_running()
        assert controller.generation == 2
    finally:
        controller.stop_and_wait()
    assert not controller.is_running()


def test_operator_runtime_starts_core_and_io_and_stops_in_dependency_order(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.dispose()

    runtime = OperatorRuntimeThreads(
        database_path=tmp_path / "ems.db",
        simulator_host="127.0.0.1",
        simulator_port=_unused_tcp_port(),
        poll_seconds=0.01,
        core_poll_seconds=0.01,
        startup_timeout=2.0,
        stop_timeout=2.0,
    )

    runtime.start()
    try:
        assert runtime.is_running()
        snapshot = runtime.snapshot()
        assert snapshot["core"]["running"] is True
        assert snapshot["core"]["name"] == "operator-core"
        assert snapshot["io"]["running"] is True
        assert snapshot["io"]["name"] == "operator-io"
        assert snapshot["core"]["ident"] is not None
        assert snapshot["io"]["ident"] is not None
        assert snapshot["core"]["ident"] != snapshot["io"]["ident"]
        assert _wait_until(lambda: runtime.io_controller.is_running())
    finally:
        runtime.stop_and_wait()

    assert not runtime.core_controller.is_running()
    assert not runtime.io_controller.is_running()


def test_operator_runtime_calls_controllers_in_dependency_order(tmp_path):
    events: list[str] = []

    class FakeController:
        def __init__(self, name: str):
            self.thread_name = name
            self.thread_ident = 1 if name == "operator-core" else 2
            self.generation = 0
            self.running = False

        def start(self):
            events.append(f"start:{self.thread_name}")
            self.generation += 1
            self.running = True

        def stop_and_wait(self):
            events.append(f"stop:{self.thread_name}")
            self.running = False

        def is_running(self):
            return self.running

    runtime = OperatorRuntimeThreads(database_path=tmp_path / "ems.db")
    runtime.core_controller = FakeController("operator-core")
    runtime.io_controller = FakeController("operator-io")

    runtime.start()
    runtime.stop_and_wait()

    assert events == [
        "start:operator-core",
        "start:operator-io",
        "stop:operator-io",
        "stop:operator-core",
    ]
