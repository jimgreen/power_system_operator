from __future__ import annotations

import os
import socket
import sys

import psutil

from power_operator.database import Database, initialize_database
from power_operator.runtime_processes import OperatorProcessRuntime


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_runtime_starts_independent_core_and_io_processes_and_can_attach(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.dispose()

    project_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    runtime = OperatorProcessRuntime(
        database_path=tmp_path / "ems.db",
        project_root=project_root,
        python_executable=sys.executable,
        simulator_port=_unused_tcp_port(),
        poll_seconds=0.05,
        core_poll_seconds=0.05,
        runtime_dir=tmp_path / "runtime",
        start_timeout=5.0,
        stop_timeout=5.0,
    )
    attached = OperatorProcessRuntime(
        database_path=tmp_path / "ems.db",
        project_root=project_root,
        python_executable=sys.executable,
        simulator_port=runtime.simulator_port,
        poll_seconds=0.05,
        core_poll_seconds=0.05,
        runtime_dir=tmp_path / "runtime",
        start_timeout=5.0,
        stop_timeout=5.0,
    )

    try:
        runtime.start()
        snapshot = runtime.snapshot()

        assert snapshot["core"]["running"] is True
        assert snapshot["io"]["running"] is True
        assert snapshot["core"]["pid"] not in (None, os.getpid())
        assert snapshot["io"]["pid"] not in (None, os.getpid())
        assert snapshot["core"]["pid"] != snapshot["io"]["pid"]
        assert psutil.pid_exists(int(snapshot["core"]["pid"]))
        assert psutil.pid_exists(int(snapshot["io"]["pid"]))

        attached.start()
        assert attached.snapshot() == snapshot
    finally:
        attached.stop()

    assert runtime.snapshot()["core"]["running"] is False
    assert runtime.snapshot()["io"]["running"] is False


def test_runtime_exposes_independent_service_controls(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.dispose()
    project_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    runtime = OperatorProcessRuntime(
        database_path=tmp_path / "ems.db",
        project_root=project_root,
        python_executable=sys.executable,
        simulator_port=_unused_tcp_port(),
        poll_seconds=0.05,
        core_poll_seconds=0.05,
        runtime_dir=tmp_path / "runtime",
        start_timeout=5.0,
        stop_timeout=5.0,
    )

    try:
        runtime.start_core()
        assert runtime.snapshot()["core"]["running"] is True
        assert runtime.snapshot()["io"]["running"] is False

        runtime.start_io()
        assert runtime.is_running()

        runtime.stop_io()
        assert runtime.snapshot()["core"]["running"] is True
        assert runtime.snapshot()["io"]["running"] is False

        runtime.start_io()
        runtime.restart_core()
        assert runtime.is_running()
    finally:
        runtime.stop()
