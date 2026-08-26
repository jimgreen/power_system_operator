from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psutil

from .core_process import CoreProcessManager, default_core_pid_path


def _resolved(path: str | Path, base: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    return candidate.resolve()


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(_resolved(left))) == os.path.normcase(str(_resolved(right)))


def _argument_value(command: list[str], option: str) -> str | None:
    for index, argument in enumerate(command):
        if argument == option and index + 1 < len(command):
            return command[index + 1]
        prefix = f"{option}="
        if argument.startswith(prefix):
            return argument[len(prefix) :]
    return None


def default_io_pid_path(database_path: str | Path) -> Path:
    database = _resolved(database_path)
    digest = hashlib.sha256(os.path.normcase(str(database)).encode("utf-8")).hexdigest()[:12]
    safe_stem = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in database.stem
    )
    return database.parent / ".runtime" / f"operator_io_{safe_stem}_{digest}.pid"


def _read_pid_record(pid_file: Path, service_name: str) -> dict[str, object]:
    try:
        record = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取 {service_name} PID 文件 {pid_file}: {exc}") from exc
    if not isinstance(record, dict):
        raise RuntimeError(f"{service_name} PID 文件格式错误: {pid_file}")
    try:
        record["pid"] = int(record["pid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"{service_name} PID 文件缺少有效 pid: {pid_file}") from exc
    return record


def _process_matches(
    process: psutil.Process,
    *,
    script_path: Path,
    database_path: Path,
    python_executable: Path,
) -> bool:
    try:
        command = process.cmdline()
        working_directory = Path(process.cwd())
        executable = Path(process.exe()).resolve()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        return False
    if len(command) < 2 or not _same_path(executable, python_executable):
        return False
    script_matches = any(
        argument.lower().endswith(script_path.name.lower())
        and _same_path(_resolved(argument, working_directory), script_path)
        for argument in command[1:]
    )
    database_argument = _argument_value(command, "--db") or "ems.db"
    return script_matches and _same_path(
        _resolved(database_argument, working_directory), database_path
    )


@contextmanager
def service_pid_file(
    pid_file: str | Path,
    *,
    service_name: str,
    script_path: str | Path,
    database_path: str | Path,
) -> Iterator[None]:
    """Publish one independently runnable service process."""

    path = _resolved(pid_file)
    script = _resolved(script_path)
    database = _resolved(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_pid_record(path, service_name)
        existing_pid = int(existing["pid"])
        if psutil.pid_exists(existing_pid):
            raise RuntimeError(f"{service_name} 已在运行，PID={existing_pid}")
        path.unlink(missing_ok=True)

    record = {
        "pid": os.getpid(),
        "database": str(database),
        "script": str(script),
        "python": str(Path(sys.executable).resolve()),
        "started_at": int(time.time()),
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    try:
        yield
    finally:
        try:
            current = _read_pid_record(path, service_name)
        except RuntimeError:
            current = None
        if current is not None and int(current["pid"]) == os.getpid():
            path.unlink(missing_ok=True)


class ServiceProcessManager:
    """Start, attach to, monitor and stop one exact Python service."""

    def __init__(
        self,
        *,
        service_name: str,
        script_path: str | Path,
        database_path: str | Path,
        python_executable: str | Path,
        arguments: list[str],
        pid_file: str | Path,
        runtime_dir: str | Path,
        start_timeout: float,
        stop_timeout: float,
    ):
        self.service_name = str(service_name)
        self.script_path = _resolved(script_path)
        self.database_path = _resolved(database_path)
        self.python_executable = _resolved(python_executable)
        self.arguments = list(arguments)
        self.pid_file = _resolved(pid_file)
        self.runtime_dir = _resolved(runtime_dir)
        self.start_timeout = max(0.1, float(start_timeout))
        self.stop_timeout = max(0.1, float(stop_timeout))

    def _validated_process(self) -> psutil.Process | None:
        if not self.pid_file.exists():
            return None
        record = _read_pid_record(self.pid_file, self.service_name)
        if (
            not isinstance(record.get("script"), str)
            or not isinstance(record.get("database"), str)
            or not isinstance(record.get("python"), str)
            or not _same_path(str(record["script"]), self.script_path)
            or not _same_path(str(record["database"]), self.database_path)
            or not _same_path(str(record["python"]), self.python_executable)
        ):
            raise RuntimeError(f"PID 文件不属于目标 {self.service_name}: {self.pid_file}")
        pid = int(record["pid"])
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self.pid_file.unlink(missing_ok=True)
            return None
        if not _process_matches(
            process,
            script_path=self.script_path,
            database_path=self.database_path,
            python_executable=self.python_executable,
        ):
            raise RuntimeError(f"PID {pid} 不属于目标 {self.service_name}，拒绝管理该进程")
        return process

    def running_pid(self) -> int | None:
        process = self._validated_process()
        return None if process is None else int(process.pid)

    def process_info(self) -> dict[str, object]:
        process = self._validated_process()
        if process is None:
            return {
                "name": self.service_name,
                "pid": None,
                "running": False,
                "started_at": None,
                "script": str(self.script_path),
                "database": str(self.database_path),
                "python": str(self.python_executable),
            }
        record = _read_pid_record(self.pid_file, self.service_name)
        started_at = record.get("started_at")
        if not isinstance(started_at, (int, float)):
            try:
                started_at = int(process.create_time())
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                started_at = None
        return {
            "name": self.service_name,
            "pid": int(process.pid),
            "running": True,
            "started_at": int(started_at) if started_at is not None else None,
            "script": str(self.script_path),
            "database": str(self.database_path),
            "python": str(self.python_executable),
        }

    def start(self) -> None:
        if self._validated_process() is not None:
            return
        if not self.script_path.is_file():
            raise RuntimeError(f"{self.service_name} 入口不存在: {self.script_path}")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_path = self.runtime_dir / f"{self.pid_file.stem}.stdout.log"
        stderr_path = self.runtime_dir / f"{self.pid_file.stem}.stderr.log"
        command = [
            str(self.python_executable),
            str(self.script_path),
            *self.arguments,
            "--pid-file",
            str(self.pid_file),
        ]
        creation_flags = 0
        if os.name == "nt":
            creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
            child = subprocess.Popen(
                command,
                cwd=str(self.script_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )

        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            exit_code = child.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"{self.service_name} 启动失败，退出码 {exit_code}；错误日志: {stderr_path}"
                )
            if self.running_pid() == child.pid:
                return
            time.sleep(0.05)
        child.terminate()
        try:
            child.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=self.stop_timeout)
        self.pid_file.unlink(missing_ok=True)
        raise RuntimeError(f"{self.service_name} 启动超时: {self.pid_file}")

    def stop(self) -> None:
        process = self._validated_process()
        if process is None:
            return
        pid = int(process.pid)
        process.terminate()
        try:
            process.wait(timeout=self.stop_timeout)
        except psutil.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=self.stop_timeout)
            except psutil.TimeoutExpired as exc:
                raise RuntimeError(f"{self.service_name} PID {pid} 未能在超时内退出") from exc
        self.pid_file.unlink(missing_ok=True)


class OperatorProcessRuntime:
    """MMI-side supervisor for two independent resident processes."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        project_root: str | Path | None = None,
        python_executable: str | Path = sys.executable,
        simulator_host: str = "127.0.0.1",
        simulator_port: int = 9001,
        rtu_id: int = 1,
        poll_seconds: float = 0.5,
        core_poll_seconds: float = 0.5,
        runtime_dir: str | Path | None = None,
        start_timeout: float = 10.0,
        stop_timeout: float = 10.0,
    ):
        self.database_path = _resolved(database_path)
        self.project_root = _resolved(project_root or Path(__file__).resolve().parents[1])
        self.python_executable = _resolved(python_executable)
        self.simulator_host = str(simulator_host)
        self.simulator_port = int(simulator_port)
        self.rtu_id = int(rtu_id)
        self.runtime_dir = _resolved(runtime_dir or self.database_path.parent / ".runtime")
        self.core_pid_file = default_core_pid_path(self.database_path)
        self.io_pid_file = default_io_pid_path(self.database_path)
        if runtime_dir is not None:
            self.core_pid_file = self.runtime_dir / self.core_pid_file.name
            self.io_pid_file = self.runtime_dir / self.io_pid_file.name
        self.core_manager = CoreProcessManager(
            database_path=self.database_path,
            core_script=self.project_root / "operator_core.py",
            python_executable=self.python_executable,
            poll_seconds=core_poll_seconds,
            pid_file=self.core_pid_file,
            runtime_dir=self.runtime_dir,
            start_timeout=start_timeout,
            stop_timeout=stop_timeout,
        )
        self.io_manager = ServiceProcessManager(
            service_name="operator_io",
            script_path=self.project_root / "operator_io.py",
            database_path=self.database_path,
            python_executable=self.python_executable,
            arguments=[
                "--db", str(self.database_path),
                "--poll", str(max(0.05, float(poll_seconds))),
                "--core-poll", str(max(0.05, float(core_poll_seconds))),
                "--simulator-host", self.simulator_host,
                "--simulator-port", str(self.simulator_port),
                "--rtu-id", str(self.rtu_id),
                "--core-pid-file", str(self.core_pid_file),
            ],
            pid_file=self.io_pid_file,
            runtime_dir=self.runtime_dir,
            start_timeout=start_timeout,
            stop_timeout=stop_timeout,
        )

    def start_core(self) -> None:
        self.core_manager.start()

    def stop_core(self) -> None:
        self.core_manager.stop_and_wait()

    def restart_core(self) -> None:
        self.stop_core()
        self.start_core()

    def start_io(self) -> None:
        self.io_manager.start()

    def stop_io(self) -> None:
        self.io_manager.stop()

    def restart_io(self) -> None:
        self.stop_io()
        self.start_io()

    def start(self) -> None:
        core_was_running = self.core_manager.running_pid() is not None
        self.start_core()
        try:
            self.start_io()
        except Exception:
            if not core_was_running:
                self.stop_core()
            raise

    def stop(self) -> None:
        errors: list[Exception] = []
        for operation in (self.stop_io, self.stop_core):
            try:
                operation()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("；".join(str(error) for error in errors)) from errors[0]

    def is_running(self) -> bool:
        snapshot = self.snapshot()
        return bool(snapshot["core"]["running"] and snapshot["io"]["running"])

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            "core": self.core_manager.process_info(),
            "io": self.io_manager.process_info(),
        }
