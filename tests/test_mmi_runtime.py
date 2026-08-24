from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from operator_mmi import OperatorMainWindow
from power_operator.database import Database, initialize_database


class RecordingRuntime:
    def __init__(self):
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop_and_wait(self) -> None:
        self.stop_calls += 1


def test_mmi_automatically_starts_and_stops_managed_core_io_threads(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    runtime = RecordingRuntime()

    window = OperatorMainWindow(database, runtime=runtime)
    assert runtime.start_calls == 1
    assert runtime.stop_calls == 0

    window.close()
    application.processEvents()
    assert runtime.stop_calls == 1
