from __future__ import annotations

import json
import subprocess
import sys

import psutil
import pytest

from power_operator.core_process import CoreProcessManager, default_core_pid_path
from power_operator.database import Database, initialize_database


def _manager(tmp_path) -> tuple[CoreProcessManager, Database]:
    project_root = __import__("pathlib").Path(__file__).resolve().parents[1]
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    manager = CoreProcessManager(
        database_path=database.path,
        core_script=project_root / "operator_core.py",
        python_executable=sys.executable,
        poll_seconds=0.05,
        pid_file=tmp_path / "operator_core.pid",
        runtime_dir=tmp_path / "runtime",
        stop_timeout=5.0,
        start_timeout=5.0,
    )
    return manager, database


def test_default_core_pid_path_is_database_specific(tmp_path):
    first = default_core_pid_path(tmp_path / "ems.db")
    same = default_core_pid_path((tmp_path / "." / "ems.db").resolve())
    second = default_core_pid_path(tmp_path / "other.db")

    assert first == same
    assert first != second
    assert first.parent == tmp_path / ".runtime"


def test_manager_starts_and_stops_only_the_managed_core(tmp_path):
    manager, database = _manager(tmp_path)
    try:
        manager.start()
        pid = manager.running_pid()

        assert pid is not None
        assert psutil.pid_exists(pid)
        assert manager.pid_file.exists()

        manager.stop_and_wait()

        assert manager.running_pid() is None
        assert not manager.pid_file.exists()
        assert not psutil.pid_exists(pid)
    finally:
        try:
            manager.stop_and_wait()
        finally:
            database.dispose()


def test_manager_removes_stale_pid_file_without_touching_other_processes(tmp_path):
    manager, database = _manager(tmp_path)
    manager.pid_file.write_text(
        json.dumps(
            {
                "pid": 2_147_483_647,
                "database": str(database.path),
                "script": str(manager.core_script),
            }
        ),
        encoding="utf-8",
    )

    manager.stop_and_wait()

    assert not manager.pid_file.exists()
    database.dispose()


def test_manager_refuses_pid_file_that_points_to_an_unrelated_process(tmp_path):
    manager, database = _manager(tmp_path)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        manager.pid_file.write_text(
            json.dumps(
                {
                    "pid": sleeper.pid,
                    "database": str(database.path),
                    "script": str(manager.core_script),
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="不属于目标 operator_core"):
            manager.stop_and_wait()

        assert sleeper.poll() is None
        assert manager.pid_file.exists()
    finally:
        sleeper.terminate()
        sleeper.wait(timeout=5)
        manager.pid_file.unlink(missing_ok=True)
        database.dispose()
