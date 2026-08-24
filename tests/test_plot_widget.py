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


def test_cursor_text_panel_follows_mouse_and_has_transparent_background():
    application = QApplication.instance() or QApplication([])
    plot = InteractivePlot("跟随鼠标游标测试")
    plot.resize(640, 360)
    plot.set_series(
        [
            CurveSeries("风电", "#2f80ed", [(0.0, 1.0), (10.0, 2.0), (20.0, 3.0)]),
            CurveSeries("负荷", "#eb5757", [(0.0, 3.0), (10.0, 4.0), (20.0, 5.0)]),
        ]
    )
    plot.show()
    application.processEvents()
    try:
        plot_rect = plot.plot_rect()
        first_mouse = QPoint(int(plot_rect.center().x()), int(plot_rect.top() + 45))
        second_mouse = QPoint(int(plot_rect.center().x()), int(plot_rect.top() + 125))

        QTest.mouseMove(plot, first_mouse)
        application.processEvents()
        first_cursor_time = plot.cursor_readout()[0]
        first_panel = plot.cursor_panel_rect()

        QTest.mouseMove(plot, second_mouse)
        application.processEvents()
        second_cursor_time = plot.cursor_readout()[0]
        second_panel = plot.cursor_panel_rect()

        assert first_cursor_time == second_cursor_time == 10.0
        assert first_panel is not None
        assert second_panel is not None
        assert first_panel.top() != second_panel.top()
        assert plot_rect.contains(first_panel)
        assert plot_rect.contains(second_panel)
        assert plot.cursor_panel_background().alpha() == 0
        assert not plot.grab().isNull()
    finally:
        plot.close()
