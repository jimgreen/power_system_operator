from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from power_operator.plot_widget import CurveSeries, InteractivePlot


def test_plot_cursor_tracks_nearest_time_and_reports_every_curve():
    application = QApplication.instance() or QApplication([])
    plot = InteractivePlot("游标测试")
    plot.resize(640, 360)
    plot.set_series(
        [
            CurveSeries("风电", "#2f80ed", [(0.0, 1.23456), (10.0, 2.34567), (20.0, 3.45678)]),
            CurveSeries("光伏", "#f2994a", [(0.0, 4.56789), (10.0, 5.67891), (20.0, 6.78912)]),
            CurveSeries("负荷", "#9b51e0", [(0.0, 7.89123), (10.0, 8.91234), (20.0, 9.12345)]),
        ]
    )
    plot.show()
    application.processEvents()
    try:
        assert plot.cursor_readout() == (None, ())
        QTest.mouseMove(plot, plot.plot_rect().center().toPoint() + QPoint(2, 0))
        application.processEvents()

        cursor_time, samples = plot.cursor_readout()
        assert cursor_time == 10.0
        assert samples == (
            ("风电", 10.0, 2.34567),
            ("光伏", 10.0, 5.67891),
            ("负荷", 10.0, 8.91234),
        )
        assert not plot.grab().isNull()

        QApplication.sendEvent(plot, QEvent(QEvent.Type.Leave))
        assert plot.cursor_readout() == (None, ())
    finally:
        plot.close()


def test_plot_cursor_is_cleared_when_data_or_view_changes():
    application = QApplication.instance() or QApplication([])
    plot = InteractivePlot("游标复位测试")
    plot.resize(640, 360)
    plot.set_series([CurveSeries("功率", "#2f80ed", [(0.0, 1.0), (10.0, 2.0)])])
    plot.show()
    application.processEvents()
    try:
        QTest.mouseMove(plot, plot.plot_rect().center().toPoint())
        application.processEvents()
        assert plot.cursor_readout()[0] is not None

        plot.reset_view()
        assert plot.cursor_readout() == (None, ())

        QTest.mouseMove(plot, plot.plot_rect().center().toPoint() + QPoint(2, 0))
        application.processEvents()
        assert plot.cursor_readout()[0] is not None
        plot.set_series([CurveSeries("新功率", "#27ae60", [(20.0, 3.0)])])
        assert plot.cursor_readout() == (None, ())
    finally:
        plot.close()
