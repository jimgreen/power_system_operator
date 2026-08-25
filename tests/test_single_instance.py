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


def test_single_instance_guard_reports_the_real_owner_process():
    from power_operator.single_instance import SingleInstanceGuard

    name = unique_instance_name()
    owner_guard = SingleInstanceGuard(name)
    contender = SingleInstanceGuard(name)

    try:
        assert owner_guard.acquire() is True
        assert contender.acquire() is False

        owner = contender.existing_owner()
        assert owner is not None
        assert owner.pid == os.getpid()
        assert Path(owner.executable).resolve() == Path(sys.executable).resolve()
        assert owner.argv == tuple(sys.argv)
        assert Path(owner.cwd).resolve() == Path.cwd().resolve()

        owner_guard.release()
        assert contender.existing_owner() is None
    finally:
        owner_guard.release()
        contender.release()


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
        owner_info = contender.existing_owner()
        assert owner_info is not None
        assert owner_info.pid == owner.pid

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


def test_abrupt_owner_exit_cannot_leave_a_blocking_fake_lock():
    from power_operator.single_instance import SingleInstanceGuard

    name = unique_instance_name()
    script = """
import sys
import time
from power_operator.single_instance import SingleInstanceGuard

guard = SingleInstanceGuard(sys.argv[1])
if not guard.acquire():
    raise SystemExit(4)
print("ready", flush=True)
time.sleep(60)
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script, name],
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
        assert contender.existing_owner() is not None

        owner.kill()
        assert owner.wait(timeout=5) != 4

        # The owner JSON may survive an abrupt TerminateProcess, but it is
        # diagnostic only.  The operating system releases the real lock, so a
        # new process must acquire it and atomically replace the stale record.
        assert replacement.acquire() is True
        replacement_owner = replacement.existing_owner()
        assert replacement_owner is not None
        assert replacement_owner.pid == os.getpid()
    finally:
        contender.release()
        replacement.release()
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)


@pytest.mark.parametrize(("activated", "exit_code"), [(True, 0), (False, 2)])
def test_mmi_main_handles_duplicate_before_opening_database(
    monkeypatch,
    capsys,
    activated,
    exit_code,
):
    import operator_mmi
    from power_operator.single_instance import InstanceOwner

    events: list[str] = []

    class RejectingGuard:
        def acquire(self) -> bool:
            events.append("acquire")
            return False

        def release(self) -> None:
            events.append("release")

        def existing_owner(self) -> InstanceOwner:
            events.append("owner")
            return InstanceOwner(
                pid=4321,
                executable=sys.executable,
                argv=(sys.executable, "operator_mmi.py"),
                cwd=str(Path.cwd()),
                started_at=1.0,
            )

    def unexpected_database(*_args, **_kwargs):
        pytest.fail("重复实例不得初始化数据库")

    monkeypatch.setattr(operator_mmi, "SingleInstanceGuard", RejectingGuard)
    monkeypatch.setattr(
        operator_mmi,
        "activate_existing_instance",
        lambda pid: events.append(f"activate:{pid}") or activated,
    )
    monkeypatch.setattr(operator_mmi, "Database", unexpected_database)
    monkeypatch.setattr(sys, "argv", ["operator_mmi.py", "--no-workers"])

    if exit_code == 0:
        assert operator_mmi.main() is None
    else:
        with pytest.raises(SystemExit) as raised:
            operator_mmi.main()
        assert raised.value.code == exit_code
    assert events == ["acquire", "owner", "activate:4321"]
    message = capsys.readouterr().err
    assert "operator_mmi 已经在运行" in message
    assert "PID 4321" in message
    if activated:
        assert "已恢复并显示现有窗口" in message
    else:
        assert "现有实例可能仍在启动或没有可见窗口" in message


def test_ensure_window_visible_repeats_the_native_show(monkeypatch):
    from power_operator import single_instance

    events: list[object] = []

    class FakeWindow:
        def show(self) -> None:
            events.append("show")

        def showNormal(self) -> None:  # noqa: N802
            events.append("showNormal")

        def raise_(self) -> None:
            events.append("raise")

        def activateWindow(self) -> None:  # noqa: N802
            events.append("activate")

        def winId(self) -> int:  # noqa: N802
            events.append("winId")
            return 9876

    monkeypatch.setattr(
        single_instance,
        "_restore_native_window",
        lambda handle: events.append(("native", handle)) or True,
    )

    assert single_instance.ensure_window_visible(FakeWindow()) is True
    assert events == [
        "show",
        "showNormal",
        "raise",
        "activate",
        "winId",
        ("native", 9876),
    ]


def test_activate_existing_instance_prefers_the_operator_window(monkeypatch):
    from power_operator import single_instance

    restored: list[int] = []
    monkeypatch.setattr(
        single_instance,
        "_windows_for_process",
        lambda pid: [
            (101, "D:\\anaconda3\\python.exe", "ConsoleWindowClass"),
            (202, "电力系统操作员人机界面", "Qt673QWindowIcon"),
        ],
    )
    monkeypatch.setattr(
        single_instance,
        "_restore_native_window",
        lambda handle: restored.append(handle) or True,
    )

    assert single_instance.activate_existing_instance(4321) is True
    assert restored == [202]


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

        assert duplicate.returncode in (0, 2)
        assert "operator_mmi 已经在运行" in duplicate.stderr
        assert not database_path.exists()
    finally:
        owner.release()
