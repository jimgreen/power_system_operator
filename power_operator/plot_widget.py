from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PyQt6.QtWidgets import QWidget

from .time_utils import format_float, format_simu_time


@dataclass(frozen=True, slots=True)
class CurveSeries:
    name: str
    color: str
    points: list[tuple[float, float]]
    width: float = 2.0


class InteractivePlot(QWidget):
    """Small dependency-free Qt plot with zoom, panning and a data cursor."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.title = title
        self.series: list[CurveSeries] = []
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self._drag_origin: QPoint | None = None
        self._pan_origin = QPointF(0.0, 0.0)
        self._cursor_time: float | None = None
        self._cursor_samples: tuple[tuple[CurveSeries, float, float], ...] = ()
        self.setMinimumSize(320, 220)
        self.setMouseTracking(True)
        self.setToolTip(
            "移动鼠标查看游标值；滚轮缩放；按住鼠标左键拖拽平移；双击恢复全图"
        )

    def set_series(self, series: list[CurveSeries]) -> None:
        self.series = series
        self._cursor_time = None
        self._cursor_samples = ()
        self.update()

    def reset_view(self) -> None:
        self.zoom = 1.0
        self.pan = QPointF(0.0, 0.0)
        self._cursor_time = None
        self._cursor_samples = ()
        self.update()

    def plot_rect(self) -> QRectF:
        """Return the drawable data area used by painting and cursor hit tests."""

        return QRectF(70, 42, max(1, self.width() - 94), max(1, self.height() - 92))

    def cursor_readout(
        self,
    ) -> tuple[float | None, tuple[tuple[str, float, float], ...]]:
        """Return the cursor time and every visible series' nearest sample."""

        samples = tuple(
            (curve.name, sample_time, sample_value)
            for curve, sample_time, sample_value in self._cursor_samples
        )
        return self._cursor_time, samples

    def _clear_cursor(self) -> None:
        if self._cursor_time is None and not self._cursor_samples:
            return
        self._cursor_time = None
        self._cursor_samples = ()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802 - Qt API
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.zoom = max(0.5, min(20.0, self.zoom * factor))
        self._cursor_time = None
        self._cursor_samples = ()
        self.update()
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self._pan_origin = QPointF(self.pan)
            self._cursor_time = None
            self._cursor_samples = ()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_origin is not None:
            delta = event.position().toPoint() - self._drag_origin
            self.pan = self._pan_origin + QPointF(delta.x(), delta.y())
            self.update()
            event.accept()
            return
        self._update_cursor(event.position())
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self.unsetCursor()
            self._update_cursor(event.position())
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.reset_view()
        event.accept()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_origin is None:
            self._clear_cursor()
        super().leaveEvent(event)

    def _bounds(self) -> tuple[float, float, float, float] | None:
        points = [point for curve in self.series for point in curve.points]
        if not points:
            return None
        x_values = [point[0] for point in points]
        y_values = [point[1] for point in points]
        x_min, x_max = min(x_values), max(x_values)
        y_min, y_max = min(y_values), max(y_values)
        if x_min == x_max:
            x_min -= 0.5
            x_max += 0.5
        if y_min == y_max:
            padding = max(1.0, abs(y_min) * 0.1)
            y_min -= padding
            y_max += padding
        else:
            padding = (y_max - y_min) * 0.08
            y_min -= padding
            y_max += padding
        return x_min, x_max, y_min, y_max

    def _transform_point(
        self,
        x_value: float,
        y_value: float,
        bounds: tuple[float, float, float, float],
        plot_rect: QRectF,
    ) -> QPointF:
        x_min, x_max, y_min, y_max = bounds
        center_x = plot_rect.center().x() + self.pan.x()
        center_y = plot_rect.center().y() + self.pan.y()
        draw_width = plot_rect.width() * self.zoom
        draw_height = plot_rect.height() * self.zoom
        x_ratio = (x_value - x_min) / (x_max - x_min)
        y_ratio = (y_value - y_min) / (y_max - y_min)
        return QPointF(
            center_x + (x_ratio - 0.5) * draw_width,
            center_y - (y_ratio - 0.5) * draw_height,
        )

    def _update_cursor(self, position: QPointF) -> None:
        bounds = self._bounds()
        plot_rect = self.plot_rect()
        if bounds is None or not plot_rect.contains(position):
            self._clear_cursor()
            return

        y_min = bounds[2]
        visible_times = {
            float(x_value)
            for curve in self.series
            for x_value, _value in curve.points
            if plot_rect.left() - 0.5
            <= self._transform_point(float(x_value), y_min, bounds, plot_rect).x()
            <= plot_rect.right() + 0.5
        }
        if not visible_times:
            self._clear_cursor()
            return

        cursor_time = min(
            visible_times,
            key=lambda value: abs(
                self._transform_point(value, y_min, bounds, plot_rect).x()
                - position.x()
            ),
        )
        samples: list[tuple[CurveSeries, float, float]] = []
        for curve in self.series:
            if not curve.points:
                continue
            sample_time, sample_value = min(
                curve.points,
                key=lambda point: (abs(float(point[0]) - cursor_time), float(point[0])),
            )
            samples.append((curve, float(sample_time), float(sample_value)))
        new_samples = tuple(samples)
        if cursor_time == self._cursor_time and new_samples == self._cursor_samples:
            return
        self._cursor_time = cursor_time
        self._cursor_samples = new_samples
        self.update()

    def _draw_cursor(
        self,
        painter: QPainter,
        plot_rect: QRectF,
        bounds: tuple[float, float, float, float],
    ) -> None:
        if self._cursor_time is None or not self._cursor_samples:
            return

        cursor_x = self._transform_point(
            self._cursor_time, bounds[2], bounds, plot_rect
        ).x()
        painter.save()
        painter.setClipRect(plot_rect)
        cursor_pen = QPen(QColor("#355f8a"), 1.2, Qt.PenStyle.DashLine)
        painter.setPen(cursor_pen)
        painter.drawLine(
            QPointF(cursor_x, plot_rect.top()),
            QPointF(cursor_x, plot_rect.bottom()),
        )
        for curve, sample_time, sample_value in self._cursor_samples:
            point = self._transform_point(sample_time, sample_value, bounds, plot_rect)
            if not plot_rect.adjusted(-1, -1, 1, 1).contains(point):
                continue
            painter.setPen(QPen(QColor("#ffffff"), 1.2))
            painter.setBrush(QColor(curve.color))
            painter.drawEllipse(point, 4.0, 4.0)
        painter.restore()

        font_metrics = QFontMetrics(painter.font())
        line_height = max(18, font_metrics.height() + 4)
        padding = 7
        available_height = max(1, int(plot_rect.height()) - 12 - 2 * padding)
        rows_per_column = max(1, (available_height - line_height) // line_height)
        columns = [
            self._cursor_samples[index : index + rows_per_column]
            for index in range(0, len(self._cursor_samples), rows_per_column)
        ]
        labels = [
            f"{curve.name}: {format_float(sample_value)}"
            for curve, _sample_time, sample_value in self._cursor_samples
        ]
        desired_column_width = min(
            230,
            max(130, max(font_metrics.horizontalAdvance(label) + 28 for label in labels)),
        )
        max_panel_width = max(80.0, plot_rect.width() - 12)
        panel_width = min(
            max_panel_width,
            2 * padding + desired_column_width * len(columns),
        )
        column_width = max(1.0, (panel_width - 2 * padding) / len(columns))
        panel_height = min(
            plot_rect.height() - 12,
            2 * padding
            + line_height
            * (1 + max(len(column) for column in columns)),
        )
        panel_x = plot_rect.right() - panel_width - 6
        panel_y = plot_rect.top() + 6
        panel_rect = QRectF(panel_x, panel_y, panel_width, panel_height)

        painter.save()
        painter.setPen(QPen(QColor("#9aabba"), 1))
        painter.setBrush(QColor(255, 255, 255, 232))
        painter.drawRoundedRect(panel_rect, 5, 5)
        painter.setPen(QColor("#263238"))
        painter.drawText(
            QRectF(
                panel_x + padding,
                panel_y + padding,
                panel_width - 2 * padding,
                line_height,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"游标时刻 {format_simu_time(self._cursor_time)}",
        )
        for column_index, column in enumerate(columns):
            text_x = panel_x + padding + column_index * column_width
            for row_index, (curve, _sample_time, sample_value) in enumerate(column):
                text_y = panel_y + padding + line_height * (row_index + 1)
                painter.setPen(QPen(QColor(curve.color), 2.5))
                painter.drawLine(
                    QPointF(text_x + 2, text_y + line_height / 2),
                    QPointF(text_x + 15, text_y + line_height / 2),
                )
                painter.setPen(QColor("#263238"))
                label = f"{curve.name}: {format_float(sample_value)}"
                elided = font_metrics.elidedText(
                    label,
                    Qt.TextElideMode.ElideRight,
                    max(1, int(column_width - 22)),
                )
                painter.drawText(
                    QRectF(
                        text_x + 20,
                        text_y,
                        column_width - 20,
                        line_height,
                    ),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    elided,
                )
        painter.restore()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#fbfcfe"))
        plot_rect = self.plot_rect()
        painter.setPen(QColor("#263238"))
        painter.drawText(QRectF(10, 8, self.width() - 20, 24), Qt.AlignmentFlag.AlignCenter, self.title)
        bounds = self._bounds()
        if bounds is None:
            painter.setPen(QColor("#8a949e"))
            painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, "暂无曲线数据")
            painter.end()
            return

        x_min, x_max, y_min, y_max = bounds

        painter.save()
        painter.setClipRect(plot_rect)
        painter.setPen(QPen(QColor("#e3e8ee"), 1))
        for index in range(6):
            x = plot_rect.left() + plot_rect.width() * index / 5
            y = plot_rect.top() + plot_rect.height() * index / 5
            painter.drawLine(QPointF(x, plot_rect.top()), QPointF(x, plot_rect.bottom()))
            painter.drawLine(QPointF(plot_rect.left(), y), QPointF(plot_rect.right(), y))

        def transform(x_value: float, y_value: float) -> QPointF:
            return self._transform_point(x_value, y_value, bounds, plot_rect)

        for curve in self.series:
            if not curve.points:
                continue
            path = QPainterPath(transform(*curve.points[0]))
            for point in curve.points[1:]:
                path.lineTo(transform(*point))
            painter.setPen(QPen(QColor(curve.color), max(0.5, curve.width)))
            painter.drawPath(path)
        painter.restore()

        painter.setPen(QPen(QColor("#5f6b76"), 1))
        painter.drawRect(plot_rect)
        font_metrics = QFontMetrics(painter.font())
        for index in range(6):
            y_value = y_max - (y_max - y_min) * index / 5
            y = plot_rect.top() + plot_rect.height() * index / 5
            label = format_float(y_value)
            painter.drawText(QRectF(2, y - 9, 62, 18), Qt.AlignmentFlag.AlignRight, label)
            x_value = x_min + (x_max - x_min) * index / 5
            x = plot_rect.left() + plot_rect.width() * index / 5
            x_label = format_simu_time(x_value)
            painter.drawText(
                QRectF(x - 45, plot_rect.bottom() + 4, 90, font_metrics.height()),
                Qt.AlignmentFlag.AlignCenter,
                x_label,
            )

        legend_x = plot_rect.left() + 8
        legend_y = plot_rect.top() + 8
        for curve in self.series[:8]:
            painter.setPen(QPen(QColor(curve.color), max(2.0, curve.width)))
            painter.drawLine(QPointF(legend_x, legend_y + 6), QPointF(legend_x + 22, legend_y + 6))
            painter.setPen(QColor("#263238"))
            painter.drawText(QPointF(legend_x + 28, legend_y + 10), curve.name)
            legend_y += 19
        self._draw_cursor(painter, plot_rect, bounds)
        painter.end()
