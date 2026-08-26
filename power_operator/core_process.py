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


def _resolved(path: str | Path, base: Path | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    return candidate.resolve()


def _same_path(left: str | Path, right: str | Path) -> bool:
    return os.path.normcase(str(_resolved(left))) == os.path.normcase(str(_resolved(right)))


def default_core_pid_path(database_path: str | Path) -> Path:
    database = _resolved(database_path)
    digest = hashlib.sha256(os.path.normcase(str(database)).encode("utf-8")).hexdigest()[:12]
    safe_stem = "".join(char if char.isalnum() or char in "-_" else "_" for char in database.stem)
    return database.parent / ".runtime" / f"operator_core_{safe_stem}_{digest}.pid"


def _read_pid_record(pid_file: Path) -> dict[str, object]:
    record = None
    last_error: Exception | None = None
    # Windows security/indexing software can briefly hold a newly replaced
    # small file.  Retry that transient sharing denial without weakening the
    # later process-identity checks.
    for attempt in range(10):
        try:
            record = json.loads(pid_file.read_text(encoding="utf-8"))
            break
        except PermissionError as exc:
            last_error = exc
            if attempt == 9:
                break
            time.sleep(0.02)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            last_error = exc
            break
    if record is None:
        raise RuntimeError(
            f"无法读取 operator_core PID 文件 {pid_file}: {last_error}"
        ) from last_error
    if not isinstance(record, dict):
        raise RuntimeError(f"operator_core PID 文件格式错误: {pid_file}")
    try:
        record["pid"] = int(record["pid"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"operator_core PID 文件缺少有效 pid: {pid_file}") from exc
    return record


def _argument_value(command: list[str], option: str) -> str | None:
    for index, argument in enumerate(command):
        if argument == option and index + 1 < len(command):
            return command[index + 1]
        prefix = f"{option}="
        if argument.startswith(prefix):
            return argument[len(prefix) :]
    return None


def _process_matches(
    process: psutil.Process,
    *,
    core_script: Path,
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

    script_matches = False
    for argument in command[1:]:
        if not argument.lower().endswith("operator_core.py"):
            continue
        if _same_path(_resolved(argument, working_directory), core_script):
            script_matches = True
            break
    database_argument = _argument_value(command, "--db")
    if database_argument is None:
        database_argument = "ems.db"
    return script_matches and _same_path(
        _resolved(database_argument, working_directory), database_path
    )


@contextmanager
def core_pid_file(
    pid_file: str | Path,
    *,
    core_script: str | Path,
    database_path: str | Path,
) -> Iterator[None]:
    """Publish this Core instance and remove its PID file on graceful exit."""

    path = _resolved(pid_file)
    script = _resolved(core_script)
    database = _resolved(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_pid_record(path)
        existing_pid = int(existing["pid"])
        if psutil.pid_exists(existing_pid):
            raise RuntimeError(f"operator_core 已在运行，PID={existing_pid}")
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
            current = _read_pid_record(path)
        except RuntimeError:
            current = None
        if current is not None and int(current["pid"]) == os.getpid():
            path.unlink(missing_ok=True)


class CoreProcessManager:
    """Start/stop the Core identified by one script and one database file."""

    def __init__(
        self,
        *,
        database_path: str | Path,
        core_script: str | Path,
        python_executable: str | Path = sys.executable,
        poll_seconds: float = 0.5,
        pid_file: str | Path | None = None,
        runtime_dir: str | Path | None = None,
        stop_timeout: float = 10.0,
        start_timeout: float = 10.0,
    ):
        self.database_path = _resolved(database_path)
        self.core_script = _resolved(core_script)
        self.python_executable = _resolved(python_executable)
        self.poll_seconds = max(0.05, float(poll_seconds))
        self.pid_file = _resolved(pid_file or default_core_pid_path(self.database_path))
        self.runtime_dir = _resolved(runtime_dir or self.pid_file.parent)
        self.stop_timeout = max(0.1, float(stop_timeout))
        self.start_timeout = max(0.1, float(start_timeout))

    def _validated_process(self) -> psutil.Process | None:
        if not self.pid_file.exists():
            return None
        record = _read_pid_record(self.pid_file)
        record_script = record.get("script")
        record_database = record.get("database")
        if (
            not isinstance(record_script, str)
            or not isinstance(record_database, str)
            or not _same_path(record_script, self.core_script)
            or not _same_path(record_database, self.database_path)
        ):
            raise RuntimeError(f"PID 文件不属于目标 operator_core: {self.pid_file}")
        pid = int(record["pid"])
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            self.pid_file.unlink(missing_ok=True)
            return None
        if not _process_matches(
            process,
            core_script=self.core_script,
            database_path=self.database_path,
            python_executable=self.python_executable,
        ):
            raise RuntimeError(
                f"PID {pid} 不属于目标 operator_core，拒绝停止或覆盖该进程"
            )
        return process

    def running_pid(self) -> int | None:
        process = self._validated_process()
        return None if process is None else int(process.pid)

    def process_info(self) -> dict[str, object]:
        process = self._validated_process()
        if process is None:
            return {
                "name": "operator_core",
                "pid": None,
                "running": False,
                "started_at": None,
                "script": str(self.core_script),
                "database": str(self.database_path),
                "python": str(self.python_executable),
            }
        record = _read_pid_record(self.pid_file)
        started_at = record.get("started_at")
        if not isinstance(started_at, (int, float)):
            try:
                started_at = int(process.create_time())
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                started_at = None
        return {
            "name": "operator_core",
            "pid": int(process.pid),
            "running": True,
            "started_at": int(started_at) if started_at is not None else None,
            "script": str(self.core_script),
            "database": str(self.database_path),
            "python": str(self.python_executable),
        }

    def stop_and_wait(self) -> None:
        process = self._validated_process()
        if process is None:
            return
        pid = process.pid
        process.terminate()
        try:
            process.wait(timeout=self.stop_timeout)
        except psutil.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=self.stop_timeout)
            except psutil.TimeoutExpired as exc:
                raise RuntimeError(f"operator_core PID {pid} 未能在超时内退出") from exc
        if psutil.pid_exists(pid):
            raise RuntimeError(f"operator_core PID {pid} 退出确认失败")
        self.pid_file.unlink(missing_ok=True)

    def start(self) -> None:
        if self._validated_process() is not None:
            return
        if not self.core_script.is_file():
            raise RuntimeError(f"operator_core 入口不存在: {self.core_script}")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_path = self.runtime_dir / f"{self.pid_file.stem}.stdout.log"
        stderr_path = self.runtime_dir / f"{self.pid_file.stem}.stderr.log"
        command = [
            str(self.python_executable),
            str(self.core_script),
            "--db",
            str(self.database_path),
            "--poll",
            str(self.poll_seconds),
            "--pid-file",
            str(self.pid_file),
        ]
        creationflags = 0
        if os.name == "nt":
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        with stdout_path.open("ab") as stdout_file, stderr_path.open("ab") as stderr_file:
            child = subprocess.Popen(
                command,
                cwd=str(self.core_script.parent),
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )

        deadline = time.monotonic() + self.start_timeout
        while time.monotonic() < deadline:
            exit_code = child.poll()
            if exit_code is not None:
                raise RuntimeError(
                    f"operator_core 启动失败，退出码 {exit_code}；错误日志: {stderr_path}"
                )
            try:
                running_pid = self.running_pid()
            except RuntimeError:
                child.terminate()
                child.wait(timeout=self.stop_timeout)
                raise
            if running_pid == child.pid:
                return
            time.sleep(0.05)

        child.terminate()
        try:
            child.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=self.stop_timeout)
        self.pid_file.unlink(missing_ok=True)
        raise RuntimeError(
            f"operator_core 启动超时，未生成有效 PID 文件: {self.pid_file}"
        )
