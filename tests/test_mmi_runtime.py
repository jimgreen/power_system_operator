from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from operator_mmi import OperatorMainWindow
from power_operator.database import Database, initialize_database


class RecordingRuntime:
    def __init__(self):
        self.start_calls = 0
        self.calls: list[str] = []
        self.core_running = False
        self.io_running = False

    def start(self) -> None:
        self.start_calls += 1
        self.core_running = True
        self.io_running = True

    def start_core(self) -> None:
        self.calls.append("start_core")
        self.core_running = True

    def stop_core(self) -> None:
        self.calls.append("stop_core")
        self.core_running = False

    def restart_core(self) -> None:
        self.calls.append("restart_core")

    def start_io(self) -> None:
        self.calls.append("start_io")
        self.io_running = True

    def stop_io(self) -> None:
        self.calls.append("stop_io")
        self.io_running = False

    def restart_io(self) -> None:
        self.calls.append("restart_io")

    def snapshot(self) -> dict[str, dict[str, object]]:
        return {
            "core": {
                "name": "operator_core",
                "running": self.core_running,
                "pid": 101 if self.core_running else None,
                "started_at": 1_787_422_688 if self.core_running else None,
                "script": "operator_core.py",
                "database": "ems.db",
            },
            "io": {
                "name": "operator_io",
                "running": self.io_running,
                "pid": 202 if self.io_running else None,
                "started_at": 1_787_422_689 if self.io_running else None,
                "script": "operator_io.py",
                "database": "ems.db",
            },
        }


def test_mmi_attaches_or_starts_processes_and_leaves_them_running_on_close(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    runtime = RecordingRuntime()

    window = OperatorMainWindow(database, runtime=runtime)
    assert runtime.start_calls == 1
    assert window.ui.processGroup.maximumHeight() <= 98
    assert window.ui.processLayout.itemAtPosition(0, 0).widget() is (
        window.ui.coreProcessCaptionLabel
    )
    assert window.ui.processLayout.itemAtPosition(1, 0).widget() is (
        window.ui.ioProcessCaptionLabel
    )
    assert window.ui.coreProcessStatusLabel.text() == "运行"
    assert window.ui.coreProcessPidLabel.text() == "PID 101"
    assert window.ui.coreProcessStartedLabel.text().startswith("启动时间 2026-")
    assert window.ui.ioProcessStatusLabel.text() == "运行"
    assert window.ui.ioProcessPidLabel.text() == "PID 202"
    assert window.ui.ioProcessStartedLabel.text().startswith("启动时间 2026-")

    window.close()
    application.processEvents()
    assert runtime.calls == []
    assert runtime.core_running is True
    assert runtime.io_running is True


def test_mmi_exposes_separate_process_controls(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    runtime = RecordingRuntime()
    window = OperatorMainWindow(database, runtime=runtime)
    try:
        window.run_process_action("core", "restart")
        window.run_process_action("io", "stop")

        assert runtime.calls == ["restart_core", "stop_io"]
        assert window.ui.coreProcessStatusLabel.text() == "运行"
        assert window.ui.ioProcessStatusLabel.text() == "停止"
        assert window.ui.ioProcessStartButton.isEnabled()
        assert not window.ui.ioProcessStopButton.isEnabled()
    finally:
        window.close()
        application.processEvents()
