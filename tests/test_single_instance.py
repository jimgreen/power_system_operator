from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


def unique_instance_name() -> str:
    return f"power-system-operator-test-{uuid.uuid4().hex}"


def test_single_instance_guard_rejects_second_owner_and_releases_lock():
    from power_operator.single_instance import SingleInstanceGuard

    name = unique_instance_name()
    first = SingleInstanceGuard(name)
    second = SingleInstanceGuard(name)

    try:
        assert first.acquire() is True
        assert first.acquire() is True
        assert second.acquire() is False

        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()


def test_single_instance_guard_is_atomic_across_processes():
    from power_operator.single_instance import SingleInstanceGuard

    name = unique_instance_name()
    script = """
import sys
from power_operator.single_instance import SingleInstanceGuard

guard = SingleInstanceGuard(sys.argv[1])
if not guard.acquire():
    raise SystemExit(4)
print("ready", flush=True)
sys.stdin.readline()
guard.release()
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script, name],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    contender = SingleInstanceGuard(name)
    replacement = SingleInstanceGuard(name)

    try:
        assert owner.stdout is not None
        assert owner.stdout.readline().strip() == "ready"
        assert contender.acquire() is False

        assert owner.stdin is not None
        owner.stdin.write("stop\n")
        owner.stdin.flush()
        assert owner.wait(timeout=5) == 0

        assert replacement.acquire() is True
    finally:
        contender.release()
        replacement.release()
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=5)


def test_mmi_main_rejects_duplicate_before_opening_database(monkeypatch, capsys):
    import operator_mmi

    events: list[str] = []

    class RejectingGuard:
        def acquire(self) -> bool:
            events.append("acquire")
            return False

        def release(self) -> None:
            events.append("release")

    def unexpected_database(*_args, **_kwargs):
        pytest.fail("重复实例不得初始化数据库")

    monkeypatch.setattr(operator_mmi, "SingleInstanceGuard", RejectingGuard)
    monkeypatch.setattr(operator_mmi, "Database", unexpected_database)
    monkeypatch.setattr(sys, "argv", ["operator_mmi.py", "--no-workers"])

    with pytest.raises(SystemExit) as raised:
        operator_mmi.main()

    assert raised.value.code == 2
    assert events == ["acquire"]
    assert "operator_mmi 已经在运行" in capsys.readouterr().err


def test_operator_mmi_subprocess_rejects_duplicate_without_creating_database(tmp_path):
    from power_operator.single_instance import (
        DEFAULT_OPERATOR_MMI_INSTANCE_NAME,
        SingleInstanceGuard,
    )

    project_root = Path(__file__).resolve().parents[1]
    database_path = tmp_path / "must-not-be-created.db"
    owner = SingleInstanceGuard(DEFAULT_OPERATOR_MMI_INSTANCE_NAME)
    # An already running real MMI may own the default mutex while the suite is
    # executed.  In that case it is itself the first instance; otherwise this
    # test process becomes the owner for the duration of the assertion.
    owner.acquire()

    try:
        environment = os.environ.copy()
        environment.setdefault("QT_QPA_PLATFORM", "offscreen")
        duplicate = subprocess.run(
            [
                sys.executable,
                str(project_root / "operator_mmi.py"),
                "--db",
                str(database_path),
                "--no-workers",
            ],
            cwd=project_root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert duplicate.returncode == 2
        assert "operator_mmi 已经在运行" in duplicate.stderr
        assert not database_path.exists()
    finally:
        owner.release()
