from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from power_operator.core import (
    CONTROL_CLOSED,
    CONTROL_OPEN,
    LOG_DECISION,
    LOG_ERROR,
    LOG_INFO,
    LOG_WARNING,
    OPER_PAUSED,
    OPER_RUNNING,
    OPER_STOPPED,
)
from power_operator.database import Database, initialize_database
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    ScadaRtu,
    ScadaYc,
    ScadaYcHis,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
    ScadaYxHis,
    OperatorHistory,
    OperatorLog,
)
from power_operator.plot_widget import CurveSeries, InteractivePlot
from power_operator.runtime_threads import OperatorRuntimeThreads
from power_operator.single_instance import (
    SingleInstanceGuard,
    activate_existing_instance,
    ensure_window_visible,
)
from power_operator.time_utils import (
    format_float,
    format_simu_time,
    format_wall_time,
    parse_simu_time,
    parse_wall_time,
)
from operator_mmi_qt import Ui_OperatorMainWindow

LOGGER = logging.getLogger(__name__)

STATUS_NAMES = {OPER_STOPPED: "停止", OPER_RUNNING: "运行", OPER_PAUSED: "暂停"}
CONTROL_MODE_NAMES = {CONTROL_OPEN: "开环", CONTROL_CLOSED: "闭环"}
LOG_TYPE_NAMES = {
    LOG_INFO: "信息",
    LOG_WARNING: "警告",
    LOG_ERROR: "错误",
    LOG_DECISION: "控制决策",
}
SCADA_MODELS = {ScadaYc, ScadaYx, ScadaYt, ScadaYk}
DEVICE_EDITABLE_FIELDS = {
    DevDiesalGen: frozenset({"name", "p_rated", "p_max", "p_min", "p_coeff"}),
    DevWindGen: frozenset(
        {"name", "p_rated", "wind_in", "wind_rated", "wind_cut"}
    ),
    DevSolarGen: frozenset({"name", "p_rated"}),
    DevEstore: frozenset(
        {
            "name",
            "p_charge_max",
            "p_charge_eff",
            "p_discharge_max",
            "p_discharge_eff",
            "battery_capacity",
            "soc_max",
            "soc_min",
        }
    ),
    DevLoad: frozenset({"name"}),
}
HISTORY_LABELS = {
    "simu_time": "运行时刻",
    "wind_speed": "风速 (m/s)",
    "solar_radiation": "太阳辐照 (W/m²)",
    "amb_temp": "环境温度 (°C)",
    "diesal_power_curr_sum": "柴油当前总出力 (kW)",
    "diesal_power_set_sum": "柴油设定总出力 (kW)",
    "diesal_curr_sum": "柴油累计消耗 (kg)",
    "wind_power_curr_sum": "风电当前总出力 (kW)",
    "wind_power_max_sum": "风电理论最大出力 (kW)",
    "wind_power_set_sum": "风电设定总出力 (kW)",
    "solar_power_curr_sum": "光伏当前总出力 (kW)",
    "solar_power_max_sum": "光伏理论最大出力 (kW)",
    "solar_power_set_sum": "光伏设定总出力 (kW)",
    "load_power_curr_sum": "负荷总功率 (kW)",
    "estore_power_curr_sum": "储能当前总出力 (kW)",
    "estore_power_set_sum": "储能设定总出力 (kW)",
    "estore_power_soc_sum": "储能总 SOC",
}


@dataclass(frozen=True)
class HomeCurveDef:
    id: int
    group: str
    name: str
    source_table: str
    source_field: str
    pnt_no: int | None
    color: str
    line_width: float = 2.0
    visible: int = 0


HOME_CURVES = (
    HomeCurveDef(1, "环境", "风速 (m/s)", "operator_history", "wind_speed", None, "#2d9cdb"),
    HomeCurveDef(2, "环境", "太阳辐照 (W/m²)", "operator_history", "solar_radiation", None, "#00a8a8"),
    HomeCurveDef(16, "环境", "环境温度 (°C)", "operator_history", "amb_temp", None, "#7b61a8"),
    HomeCurveDef(3, "柴油", "柴油当前总出力 (kW)", "operator_history", "diesal_power_curr_sum", None, "#8d6e63", visible=1),
    HomeCurveDef(4, "柴油", "柴油设定总出力 (kW)", "operator_history", "diesal_power_set_sum", None, "#c7793f"),
    HomeCurveDef(5, "柴油", "柴油累计消耗 (kg)", "operator_history", "diesal_curr_sum", None, "#d9a441"),
    HomeCurveDef(6, "风机", "风电当前总出力 (kW)", "operator_history", "wind_power_curr_sum", None, "#2f80ed", visible=1),
    HomeCurveDef(7, "风机", "风电理论最大出力 (kW)", "operator_history", "wind_power_max_sum", None, "#56a8f5"),
    HomeCurveDef(8, "风机", "风电设定总出力 (kW)", "operator_history", "wind_power_set_sum", None, "#185abd"),
    HomeCurveDef(9, "光伏", "光伏当前总出力 (kW)", "operator_history", "solar_power_curr_sum", None, "#e3a008", visible=1),
    HomeCurveDef(10, "光伏", "光伏理论最大出力 (kW)", "operator_history", "solar_power_max_sum", None, "#f2c94c"),
    HomeCurveDef(11, "光伏", "光伏设定总出力 (kW)", "operator_history", "solar_power_set_sum", None, "#f2994a"),
    HomeCurveDef(12, "储能", "储能当前总出力 (kW)", "operator_history", "estore_power_curr_sum", None, "#27ae60", visible=1),
    HomeCurveDef(13, "储能", "储能设定总出力 (kW)", "operator_history", "estore_power_set_sum", None, "#6fcf97"),
    HomeCurveDef(14, "储能", "储能总 SOC", "operator_history", "estore_power_soc_sum", None, "#9b51e0"),
    HomeCurveDef(15, "负荷", "负荷总功率 (kW)", "operator_history", "load_power_curr_sum", None, "#eb5757", visible=1),
)

MODIFIED_ITEM_COLOR = "#ffe0a3"


@dataclass(frozen=True)
class EditorSpec:
    table: QTableWidget
    model: type


class OperatorMainWindow(QMainWindow):
    def __init__(
        self,
        database: Database,
        runtime: OperatorRuntimeThreads | None = None,
    ):
        super().__init__()
        self.database = database
        self.runtime = runtime
        self._runtime_started = False
        self.current_value_edits: dict[str, QLineEdit] = {}
        self.current_value_cards: dict[str, QFrame] = {}
        self._home_curve_tree_signature: tuple[tuple[Any, ...], ...] = ()
        self._dirty_control_widgets: set[Any] = set()
        self._dirty_editor_tables: set[QTableWidget] = set()
        self.history_start_seconds = 0
        self.history_end_seconds: int | None = None
        self.log_type_filter: int | None = None
        self.log_start_seconds = 0
        self.log_end_seconds: int | None = None
        self.log_keyword = ""
        self.log_page_size = 100
        self.log_page = 1
        self.log_total_count = 0
        self.log_total_pages = 1
        application = QApplication.instance()
        if application is not None:
            installed = set(QFontDatabase.families())
            candidates = (
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "DengXian",
                "Noto Sans CJK SC",
                "WenQuanYi Micro Hei",
            )
            for family in candidates:
                if family in installed:
                    application.setFont(QFont(family, 9))
                    break
            else:
                windows_directory = Path(os.environ.get("WINDIR", r"C:\Windows"))
                font_path = windows_directory / "Fonts" / "msyh.ttc"
                if font_path.is_file():
                    font_id = QFontDatabase.addApplicationFont(str(font_path))
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        application.setFont(QFont(families[0], 9))
        self.ui = Ui_OperatorMainWindow()
        self.ui.setupUi(self)
        self.ui.homeCurveSplitter.setSizes([340, 1100])
        self.ui.historySplitter.setSizes([300, 1100])
        self.ui.logSplitter.setSizes([820, 560])
        self._install_plots()
        self._configure_tables()
        self._connect_signals()
        self._apply_style()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_live_data)
        self.periodic_page_timer = QTimer(self)
        self.periodic_page_timer.timeout.connect(self.refresh_periodic_page)
        self.refresh_all()
        self.refresh_timer.start(1000)
        if self.ui.mainTabs.currentIndex() in (1, 2, 3, 4):
            self.periodic_page_timer.start()
        if self.runtime is not None:
            self.runtime.start()
            self._runtime_started = True

    def _install_plots(self) -> None:
        self.home_plot = InteractivePlot("系统运行曲线", self.ui.homePlotFrame)
        layout = QVBoxLayout(self.ui.homePlotFrame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.home_plot)
        self.history_plot = InteractivePlot("四遥历史曲线", self.ui.historyPlotFrame)
        layout = QVBoxLayout(self.ui.historyPlotFrame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.history_plot)

    @property
    def device_specs(self) -> list[EditorSpec]:
        return [
            EditorSpec(self.ui.diesalTable, DevDiesalGen),
            EditorSpec(self.ui.windTable, DevWindGen),
            EditorSpec(self.ui.solarTable, DevSolarGen),
            EditorSpec(self.ui.estoreTable, DevEstore),
            EditorSpec(self.ui.loadTable, DevLoad),
        ]

    @property
    def scada_specs(self) -> list[EditorSpec]:
        return [
            EditorSpec(self.ui.rtuTable, ScadaRtu),
            EditorSpec(self.ui.ycTable, ScadaYc),
            EditorSpec(self.ui.yxTable, ScadaYx),
            EditorSpec(self.ui.ytTable, ScadaYt),
            EditorSpec(self.ui.ykTable, ScadaYk),
        ]

    def _configure_tables(self) -> None:
        self.ui.logTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        device_edit_triggers = (
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        for spec in self.device_specs:
            spec.table.setEditTriggers(device_edit_triggers)
        for table in self.findChildren(QTableWidget):
            table.setSortingEnabled(False)
            header = table.horizontalHeader()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            table.verticalHeader().setVisible(False)
        for tree in self.findChildren(QTreeWidget):
            header = tree.header()
            header.setStretchLastSection(False)
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

    def _connect_signals(self) -> None:
        self.ui.startButton.clicked.connect(lambda: self.set_control_status(OPER_RUNNING))
        self.ui.pauseButton.clicked.connect(lambda: self.set_control_status(OPER_PAUSED))
        self.ui.stopButton.clicked.connect(lambda: self.set_control_status(OPER_STOPPED))
        self.ui.operStatusCombo.activated.connect(self.set_control_status)
        self.ui.controlModeCombo.currentIndexChanged.connect(
            lambda *_: self._mark_control_parameter_modified(
                self.ui.controlModeCombo
            )
        )
        self.ui.dataPeriodSpin.valueChanged.connect(
            lambda *_: self._mark_control_parameter_modified(
                self.ui.dataPeriodSpin
            )
        )
        self.ui.operPeriodSpin.valueChanged.connect(
            lambda *_: self._mark_control_parameter_modified(
                self.ui.operPeriodSpin
            )
        )
        self.ui.saveControlButton.clicked.connect(self.save_control_parameters)
        self.ui.refreshControlButton.clicked.connect(
            self.manual_refresh_control_parameters
        )
        self.ui.connectSimulatorButton.clicked.connect(
            lambda: self.set_io_connection_enabled(True)
        )
        self.ui.disconnectSimulatorButton.clicked.connect(
            lambda: self.set_io_connection_enabled(False)
        )
        self.ui.saveDevicesButton.clicked.connect(self.save_devices)
        self.ui.refreshDevicesButton.clicked.connect(self.manual_refresh_devices)
        self.ui.saveScadaButton.clicked.connect(self.save_scada)
        self.ui.refreshScadaButton.clicked.connect(self.manual_refresh_scada)
        self.ui.homeCurveTree.itemChanged.connect(self._on_home_curve_item_changed)
        self.ui.homeCurveSelectAllButton.clicked.connect(lambda: self.set_all_home_curves(True))
        self.ui.homeCurveClearButton.clicked.connect(lambda: self.set_all_home_curves(False))
        self.ui.historyTree.itemChanged.connect(lambda *_: self.refresh_history_plot())
        self.ui.historyRefreshTreeButton.clicked.connect(self.populate_history_tree)
        self.ui.historyQueryButton.clicked.connect(self.apply_history_filter)
        self.ui.logQueryButton.clicked.connect(self.apply_log_filters)
        self.ui.logResetButton.clicked.connect(self.reset_log_filters)
        self.ui.logPageSizeCombo.currentTextChanged.connect(
            self.change_log_page_size
        )
        self.ui.logPreviousPageButton.clicked.connect(
            lambda: self.change_log_page(-1)
        )
        self.ui.logNextPageButton.clicked.connect(lambda: self.change_log_page(1))
        self.ui.logTable.currentCellChanged.connect(
            lambda *_: self.show_selected_log_detail()
        )
        self.ui.mainTabs.currentChanged.connect(self.on_main_tab_changed)
        for spec in [*self.device_specs, *self.scada_specs]:
            spec.table.itemChanged.connect(
                lambda item, table=spec.table, model=spec.model: self._on_editor_item_changed(
                    table, model, item
                )
            )

    def _on_editor_item_changed(
        self,
        table: QTableWidget,
        model: type,
        item: QTableWidgetItem,
    ) -> None:
        if model in DEVICE_EDITABLE_FIELDS and not (
            item.flags() & Qt.ItemFlag.ItemIsEditable
        ):
            return
        self._dirty_editor_tables.add(table)
        self._set_modified_property(table, True)
        item.setBackground(QColor(MODIFIED_ITEM_COLOR))
        if "未保存修改" not in item.toolTip():
            existing_tooltip = item.toolTip().strip()
            item.setToolTip(
                f"{existing_tooltip}\n未保存修改：自动刷新不会覆盖此值".strip()
            )
        if model in DEVICE_EDITABLE_FIELDS:
            self._update_device_save_state()
        else:
            self._update_scada_save_state()

    @staticmethod
    def _set_modified_property(widget: Any, modified: bool) -> None:
        widget.setProperty("modified", bool(modified))
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    @property
    def control_parameter_widgets(self) -> tuple[Any, ...]:
        return (
            self.ui.controlModeCombo,
            self.ui.dataPeriodSpin,
            self.ui.operPeriodSpin,
        )

    def _mark_control_parameter_modified(self, widget: Any) -> None:
        self._dirty_control_widgets.add(widget)
        self._set_modified_property(widget, True)
        self.ui.saveControlButton.setEnabled(True)

    def _clear_control_parameter_modifications(self) -> None:
        for widget in self.control_parameter_widgets:
            self._set_modified_property(widget, False)
        self._dirty_control_widgets.clear()
        self.ui.saveControlButton.setEnabled(False)

    def _update_device_save_state(self) -> None:
        has_changes = any(
            spec.table in self._dirty_editor_tables for spec in self.device_specs
        )
        self.ui.saveDevicesButton.setEnabled(has_changes)

    def _update_scada_save_state(self) -> None:
        has_changes = any(
            spec.table in self._dirty_editor_tables for spec in self.scada_specs
        )
        self.ui.saveScadaButton.setEnabled(has_changes)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f7fa; color: #26364a; }
            QGroupBox { background: white; border: 1px solid #dce3eb; border-radius: 8px;
                        margin-top: 9px; padding-top: 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
            QPushButton { background: white; border: 1px solid #bdc8d6; border-radius: 5px;
                          padding: 6px 13px; min-width: 72px; }
            QPushButton:hover { border-color: #3e7bfa; color: #245edb; }
            QPushButton#startButton { background: #3e7bfa; color: white; border-color: #3e7bfa; }
            QPushButton#connectSimulatorButton { color: #1b5e20; border-color: #81c784; }
            QPushButton#disconnectSimulatorButton { color: #b42318; border-color: #ef9a9a; }
            QPushButton#connectSimulatorButton:disabled,
            QPushButton#disconnectSimulatorButton:disabled {
                background: #f3f5f7; color: #9aa5b1; border-color: #d5dde7;
            }
            QTabWidget::pane { border: 1px solid #dce3eb; background: white; }
            QTabBar::tab { background: #e9eef5; padding: 8px 20px; margin-right: 2px; }
            QTabBar::tab:selected { background: white; color: #245edb; font-weight: 600; }
            QTableWidget, QTreeWidget, QListWidget { background: white; alternate-background-color: #f7f9fc;
                                                     border: 1px solid #dce3eb; gridline-color: #e4e9ef; }
            QHeaderView::section { background: #eaf0f7; border: 0; border-right: 1px solid #d5dde7;
                                   padding: 6px; font-weight: 600; }
            QLabel#dataTimeValue, QLabel#operTimeValue {
                font-size: 17px; color: #245edb; font-weight: 700;
            }
            QFrame#simulatorConnectionFrame {
                background: #f8fafc; border: 1px solid #dce3eb; border-radius: 7px;
            }
            QFrame#deviceEditFrame {
                background: #fffdf2; border: 1px solid #eadb91; border-radius: 6px;
            }
            QLabel#deviceEditHintLabel {
                background: transparent; border: 0; color: #6b5a18;
            }
            QPushButton#saveDevicesButton:enabled {
                background: #3e7bfa; color: white; border-color: #3e7bfa;
            }
            QLabel#simulatorConnectionCaptionLabel {
                background: transparent; border: 0; color: #52657d; font-weight: 600;
            }
            QLabel#controlHintLabel, QLabel#homeCurveStatusLabel,
            QLabel#historyStatusLabel { color: #66758a; }
            QFrame#currentValueCard { background: transparent; border: 0; }
            QLabel#currentValueNameLabel {
                background: #eef2f7; border: 0; color: #31445b; padding: 2px 3px;
            }
            QLineEdit#currentValueEdit {
                background: #f3f6fa; border: 0; border-radius: 0; color: #245edb;
                font-size: 14px; font-weight: 600; padding: 3px;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: white; border: 1px solid #c9d2de;
                                                            border-radius: 4px; padding: 4px; }
            QComboBox[modified="true"], QSpinBox[modified="true"],
            QDoubleSpinBox[modified="true"], QLineEdit[modified="true"] {
                background: #fff0c7; color: #714800; border: 2px solid #e0a000;
            }
            QTableWidget[modified="true"] { border: 2px solid #e0a000; }
            QPushButton#saveControlButton:enabled,
            QPushButton#saveScadaButton:enabled {
                background: #e58a00; color: white; border-color: #c97700;
            }
            QSplitter::handle { background: #e4e9ef; }
            QSplitter::handle:horizontal { width: 5px; }
            """
        )

    def show_error(self, title: str, exc: Exception) -> None:
        LOGGER.exception(title, exc_info=exc)
        self.statusBar().showMessage(f"{title}: {exc}", 10_000)
        QMessageBox.critical(self, title, str(exc))

    def show_success(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def _confirm_parameter_refresh(self, description: str, has_changes: bool) -> bool:
        if not has_changes:
            return True
        answer = QMessageBox.question(
            self,
            "放弃未保存修改",
            f"{description}存在未保存修改。是否放弃这些修改并从数据库重新加载？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def set_control_status(self, status: int) -> None:
        try:
            def update(session: Session) -> None:
                control = session.get(OperatorControl, 1)
                control.oper_status = status

            self.database.write(update)
            self.refresh_control()
            self.show_success(f"运行状态已设为{STATUS_NAMES[status]}")
        except Exception as exc:
            self.show_error("更新运行状态失败", exc)

    def save_control_parameters(self) -> None:
        try:
            mode = self.ui.controlModeCombo.currentIndex()
            data_period = self.ui.dataPeriodSpin.value()
            oper_period = self.ui.operPeriodSpin.value()

            def update(session: Session) -> None:
                control = session.get(OperatorControl, 1)
                control.control_status = mode
                control.data_period = data_period
                control.oper_period = oper_period

            self.database.write(update)
            self._clear_control_parameter_modifications()
            self.refresh_control(force_parameters=True)
            self.show_success(
                f"控制参数已保存：{CONTROL_MODE_NAMES[mode]}，"
                f"数据周期 {data_period} 秒，决策周期 {oper_period} 秒"
            )
        except Exception as exc:
            self.show_error("保存控制参数失败", exc)

    def manual_refresh_control_parameters(self) -> None:
        try:
            if not self._confirm_parameter_refresh(
                "运行控制参数",
                bool(self._dirty_control_widgets),
            ):
                return
            self.refresh_control(force_parameters=True)
            self._clear_control_parameter_modifications()
            self.show_success("运行控制参数已从数据库刷新")
        except Exception as exc:
            self.show_error("刷新运行控制参数失败", exc)

    def _set_periodic_page_interval(self, data_period: int) -> None:
        interval_ms = max(1, int(data_period)) * 1000
        if self.periodic_page_timer.interval() != interval_ms:
            self.periodic_page_timer.setInterval(interval_ms)

    def refresh_control(self, *, force_parameters: bool = False) -> int:
        with self.database.session() as session:
            control = session.get(OperatorControl, 1)
            if control is None:
                return max(1, self.ui.dataPeriodSpin.value())
            data_period = max(1, int(control.data_period))
            oper_period = max(1, int(control.oper_period))
            previous_status_signals = self.ui.operStatusCombo.blockSignals(True)
            self.ui.operStatusCombo.setCurrentIndex(
                max(0, min(2, control.oper_status))
            )
            self.ui.operStatusCombo.blockSignals(previous_status_signals)
            parameter_values = (
                (
                    self.ui.controlModeCombo,
                    max(0, min(1, control.control_status)),
                ),
                (self.ui.dataPeriodSpin, data_period),
                (self.ui.operPeriodSpin, oper_period),
            )
            for widget, value in parameter_values:
                if not force_parameters and widget in self._dirty_control_widgets:
                    continue
                previous_signals = widget.blockSignals(True)
                widget.setValue(value) if hasattr(widget, "setValue") else widget.setCurrentIndex(value)
                widget.blockSignals(previous_signals)
            self.ui.dataTimeValue.setText(format_simu_time(control.data_time_curr))
            self.ui.operTimeValue.setText(format_simu_time(control.oper_time_curr))
        self._set_periodic_page_interval(data_period)
        return data_period

    @staticmethod
    def _configure_device_table_item(
        item: QTableWidgetItem,
        model: type,
        column: Any,
        *,
        is_new_row: bool,
    ) -> None:
        is_editable = column.name in DEVICE_EDITABLE_FIELDS[model] or (
            is_new_row and column.primary_key
        )
        if is_editable:
            item.setBackground(QColor("#fff8d6"))
            if column.primary_key:
                item.setToolTip("可编辑：请输入新增设备 ID")
            elif column.name == "name":
                item.setToolTip(
                    "可编辑静态参数；设备名称用于匹配系统预定义四遥点名，"
                    "改名后请同步维护相关点名"
                )
            else:
                item.setToolTip(
                    "可编辑静态参数，修改后点击“保存设备修改”写入数据库"
                )
            return
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setBackground(QColor("#f1f3f5"))
        if column.primary_key:
            item.setToolTip("设备 ID 只读；请在末尾空白行新增设备")
        else:
            item.setToolTip("实时字段，只读，由 YC/YX、控制内核或电网模拟器更新")

    def _fill_table(self, table: QTableWidget, model: type, rows: list[Any], blank_row: bool = False) -> None:
        columns = list(model.__table__.columns)
        previous_signal_state = table.blockSignals(True)
        try:
            table.clear()
            table.setColumnCount(len(columns))
            headers = []
            for column in columns:
                if model is ScadaRtu and column.name == "refresh_time":
                    headers.append("刷新时刻")
                elif model in SCADA_MODELS and column.name == "time":
                    headers.append("刷新时刻")
                else:
                    headers.append(column.name)
            table.setHorizontalHeaderLabels(headers)
            table.setRowCount(len(rows) + (1 if blank_row else 0))
            for row_index, row in enumerate(rows):
                for column_index, column in enumerate(columns):
                    value = getattr(row, column.name)
                    if value is None:
                        display = ""
                    elif model is ScadaRtu and column.name == "refresh_time":
                        display = format_wall_time(value)
                    elif (model in SCADA_MODELS and column.name == "time") or (
                        model is OperatorLog and column.name == "simu_time"
                    ):
                        display = format_simu_time(value)
                    elif isinstance(value, float):
                        display = format_float(value)
                    else:
                        display = str(value)
                    item = QTableWidgetItem(display)
                    if model in DEVICE_EDITABLE_FIELDS:
                        self._configure_device_table_item(
                            item,
                            model,
                            column,
                            is_new_row=False,
                        )
                    table.setItem(row_index, column_index, item)
            if blank_row:
                row_index = len(rows)
                for column_index, column in enumerate(columns):
                    item = QTableWidgetItem("")
                    if model in DEVICE_EDITABLE_FIELDS:
                        self._configure_device_table_item(
                            item,
                            model,
                            column,
                            is_new_row=True,
                        )
                    table.setItem(row_index, column_index, item)
        finally:
            table.blockSignals(previous_signal_state)
        self._dirty_editor_tables.discard(table)
        self._set_modified_property(table, False)

    def _table_values(self, table: QTableWidget, model: type) -> list[dict[str, Any]]:
        columns = list(model.__table__.columns)
        rows: list[dict[str, Any]] = []
        for row_index in range(table.rowCount()):
            raw_values = [
                table.item(row_index, column_index).text().strip()
                if table.item(row_index, column_index) is not None
                else ""
                for column_index in range(len(columns))
            ]
            if not any(raw_values):
                continue
            if not raw_values[0]:
                raise ValueError(f"第 {row_index + 1} 行缺少主键 {columns[0].name}")
            values: dict[str, Any] = {}
            for column, raw in zip(columns, raw_values):
                if not raw:
                    if column.nullable:
                        values[column.name] = None
                        continue
                    if column.default is not None and not column.primary_key:
                        continue
                    raise ValueError(f"第 {row_index + 1} 行字段 {column.name} 不能为空")
                python_type = column.type.python_type
                if model is ScadaRtu and column.name == "refresh_time":
                    values[column.name] = parse_wall_time(raw)
                elif model in SCADA_MODELS and column.name == "time":
                    values[column.name] = parse_simu_time(raw)
                else:
                    values[column.name] = raw if python_type is str else python_type(raw)
            rows.append(values)
        return rows

    def _load_editors(self, specs: list[EditorSpec]) -> None:
        with self.database.session() as session:
            for spec in specs:
                pk = list(spec.model.__table__.primary_key.columns)[0]
                rows = session.scalars(select(spec.model).order_by(pk)).all()
                self._fill_table(spec.table, spec.model, rows, blank_row=True)

    def _save_editors(self, specs: list[EditorSpec]) -> None:
        parsed = [(spec.model, self._table_values(spec.table, spec.model)) for spec in specs]

        def save(session: Session) -> None:
            for model, rows in parsed:
                for values in rows:
                    session.merge(model(**values))

        self.database.write(save)

    def _device_table_values(
        self,
        table: QTableWidget,
        model: type,
    ) -> list[tuple[int, dict[str, Any]]]:
        columns_by_name = {
            column.name: column for column in model.__table__.columns
        }
        primary_key = list(model.__table__.primary_key.columns)[0]
        field_names = (primary_key.name, *DEVICE_EDITABLE_FIELDS[model])
        column_indexes = {
            column.name: index
            for index, column in enumerate(model.__table__.columns)
        }
        rows: list[tuple[int, dict[str, Any]]] = []
        for row_index in range(table.rowCount()):
            raw_by_name = {
                field_name: (
                    table.item(row_index, column_indexes[field_name]).text().strip()
                    if table.item(row_index, column_indexes[field_name]) is not None
                    else ""
                )
                for field_name in field_names
            }
            if not any(raw_by_name.values()):
                continue
            if not raw_by_name[primary_key.name]:
                raise ValueError(
                    f"第 {row_index + 1} 行缺少主键 {primary_key.name}"
                )
            values: dict[str, Any] = {}
            for field_name, raw in raw_by_name.items():
                if not raw:
                    continue
                column = columns_by_name[field_name]
                python_type = column.type.python_type
                try:
                    values[field_name] = (
                        raw if python_type is str else python_type(raw)
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"第 {row_index + 1} 行字段 {field_name} 的值“{raw}”无效"
                    ) from exc
            rows.append((row_index, values))
        return rows

    def _save_device_editors(self) -> None:
        dirty_specs = [
            spec
            for spec in self.device_specs
            if spec.table in self._dirty_editor_tables
        ]
        parsed = [
            (spec.model, self._device_table_values(spec.table, spec.model))
            for spec in dirty_specs
        ]

        def save(session: Session) -> None:
            for model, rows in parsed:
                primary_key = list(model.__table__.primary_key.columns)[0]
                editable_fields = DEVICE_EDITABLE_FIELDS[model]
                for row_index, values in rows:
                    identity = values[primary_key.name]
                    existing = session.get(model, identity)
                    if existing is None:
                        if not values.get("name"):
                            raise ValueError(
                                f"第 {row_index + 1} 行字段 name 不能为空"
                            )
                        session.add(model(**values))
                        continue
                    for field_name in editable_fields:
                        if field_name not in values:
                            raise ValueError(
                                f"第 {row_index + 1} 行字段 {field_name} 不能为空"
                            )
                        setattr(existing, field_name, values[field_name])

        self.database.write(save)

    def load_devices(self) -> None:
        self._load_editors(self.device_specs)
        self._update_device_save_state()

    def manual_refresh_devices(self) -> None:
        try:
            if not self._confirm_parameter_refresh(
                "设备参数",
                self._editor_page_has_pending_changes(self.device_specs),
            ):
                return
            self.load_devices()
            self.show_success("设备参数已从数据库刷新")
        except Exception as exc:
            self.show_error("刷新设备参数失败", exc)

    def save_devices(self) -> None:
        try:
            self._save_device_editors()
            self.load_devices()
            self.show_success("设备定义已保存")
        except Exception as exc:
            self.show_error("保存设备定义失败", exc)

    def load_scada(self) -> None:
        self._load_editors(self.scada_specs)
        self._update_scada_save_state()
        self.refresh_io_connection_status()

    def manual_refresh_scada(self) -> None:
        try:
            if not self._confirm_parameter_refresh(
                "RTU 与四遥参数",
                self._editor_page_has_pending_changes(self.scada_specs),
            ):
                return
            self.load_scada()
            self.populate_history_tree()
            self.show_success("RTU 与四遥参数已从数据库刷新")
        except Exception as exc:
            self.show_error("刷新 RTU 与四遥参数失败", exc)

    def refresh_io_connection_status(self) -> None:
        with self.database.session() as session:
            control = session.get(OperatorControl, 1)
            connection_enabled = bool(
                control is not None and int(control.io_connect_enabled) == 1
            )
            rows = session.scalars(
                select(ScadaRtu).order_by(
                    ScadaRtu.status.desc(),
                    ScadaRtu.refresh_time.desc(),
                    ScadaRtu.id,
                )
            ).all()
        self.ui.connectSimulatorButton.setEnabled(not connection_enabled)
        self.ui.disconnectSimulatorButton.setEnabled(connection_enabled)
        connected = any(int(row.status) == 1 for row in rows)
        self.ui.simulatorConnectionStatusValue.setText("正常" if connected else "中断")
        if connected:
            colors = ("#e8f5e9", "#1b5e20", "#81c784")
        else:
            colors = ("#ffebee", "#b42318", "#ef9a9a")
        badge_style = (
            f"background: {colors[0]}; color: {colors[1]}; border: 1px solid {colors[2]}; "
            "border-radius: 5px; padding: 4px 12px; font-weight: 700;"
        )
        self.ui.simulatorConnectionStatusValue.setStyleSheet(badge_style)

    def set_io_connection_enabled(self, enabled: bool) -> None:
        """Request operator_io to establish or suspend its TCP connection."""

        try:
            def update(session: Session) -> None:
                control = session.get(OperatorControl, 1)
                if control is None:
                    raise RuntimeError("缺少 operator_control 控制记录")
                control.io_connect_enabled = int(enabled)
                if not enabled:
                    for rtu in session.scalars(select(ScadaRtu)).all():
                        rtu.status = 0

            self.database.write(update)
            self.refresh_io_connection_status()
            action = "建立连接" if enabled else "中断连接"
            self.show_success(f"已请求{action}")
        except Exception as exc:
            self.show_error("更新电网模拟器连接请求失败", exc)

    def save_scada(self) -> None:
        try:
            self._save_editors(self.scada_specs)
            self.load_scada()
            self.populate_history_tree()
            self.show_success("RTU 与四遥定义已保存")
        except Exception as exc:
            self.show_error("保存四遥定义失败", exc)

    def refresh_history_home(self) -> None:
        with self.database.session() as session:
            history_rows = session.scalars(
                select(OperatorHistory).order_by(OperatorHistory.simu_time)
            ).all()
            latest = history_rows[-1] if history_rows else None
            curves = list(HOME_CURVES)

        self._set_current_value_edits(latest)
        self._sync_home_curve_tree(curves)
        selected_ids = self._selected_home_curve_ids()
        selected_curves = [curve for curve in curves if curve.id in selected_ids]
        self.home_plot.set_series(self._curve_series(selected_curves, history_rows))
        if selected_curves:
            self.ui.homeCurveStatusLabel.setText(
                f"已选择 {len(selected_curves)} 条曲线，显示 {len(history_rows)} 个历史断面；"
                "滚轮缩放，按住左键拖拽平移。"
            )
        else:
            self.ui.homeCurveStatusLabel.setText(
                f"请选择左侧待显示曲线；当前共有 {len(history_rows)} 个历史断面。"
            )

    def _sync_home_curve_tree(self, curves: list[HomeCurveDef]) -> None:
        tree = self.ui.homeCurveTree
        signature = tuple(
            (
                curve.id,
                curve.group,
                curve.name,
                curve.source_table,
                curve.source_field,
                curve.pnt_no,
                curve.color,
            )
            for curve in curves
        )
        if self._home_curve_tree_signature == signature and tree.topLevelItemCount():
            return
        previous = {
            child.data(0, Qt.ItemDataRole.UserRole): child.checkState(0)
            for group_index in range(tree.topLevelItemCount())
            for group in [tree.topLevelItem(group_index)]
            for child_index in range(group.childCount())
            for child in [group.child(child_index)]
        }
        expanded = {
            tree.topLevelItem(index).text(0): tree.topLevelItem(index).isExpanded()
            for index in range(tree.topLevelItemCount())
        }
        first_load = not previous
        previous_signal_state = tree.blockSignals(True)
        try:
            tree.clear()
            groups: dict[str, QTreeWidgetItem] = {}
            for curve in curves:
                group = groups.get(curve.group)
                if group is None:
                    group = QTreeWidgetItem(tree, [curve.group])
                    group.setFlags(
                        group.flags()
                        | Qt.ItemFlag.ItemIsUserCheckable
                        | Qt.ItemFlag.ItemIsAutoTristate
                    )
                    group.setCheckState(0, Qt.CheckState.Unchecked)
                    font = group.font(0)
                    font.setBold(True)
                    group.setFont(0, font)
                    groups[curve.group] = group

                item = QTreeWidgetItem(group, [curve.name])
                item.setData(0, Qt.ItemDataRole.UserRole, curve.id)
                item.setToolTip(0, f"{curve.source_table}.{curve.source_field}")
                item.setForeground(0, QColor(curve.color))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                state = previous.get(
                    curve.id,
                    Qt.CheckState.Checked
                    if first_load and curve.visible
                    else Qt.CheckState.Unchecked,
                )
                item.setCheckState(0, state)

            for group_name, group in groups.items():
                group.setExpanded(expanded.get(group_name, True))
            self._home_curve_tree_signature = signature
        finally:
            tree.blockSignals(previous_signal_state)

    def _selected_home_curve_ids(self) -> set[int]:
        tree = self.ui.homeCurveTree
        return {
            int(child.data(0, Qt.ItemDataRole.UserRole))
            for group_index in range(tree.topLevelItemCount())
            for group in [tree.topLevelItem(group_index)]
            for child_index in range(group.childCount())
            for child in [group.child(child_index)]
            if child.checkState(0) == Qt.CheckState.Checked
            and child.data(0, Qt.ItemDataRole.UserRole) is not None
        }

    def _on_home_curve_item_changed(
        self, item: QTreeWidgetItem, _column: int
    ) -> None:
        if item.parent() is None and item.checkState(0) != Qt.CheckState.PartiallyChecked:
            tree = self.ui.homeCurveTree
            previous_signal_state = tree.blockSignals(True)
            try:
                for child_index in range(item.childCount()):
                    item.child(child_index).setCheckState(0, item.checkState(0))
            finally:
                tree.blockSignals(previous_signal_state)
        self.refresh_history_home()

    def set_all_home_curves(self, checked: bool) -> None:
        tree = self.ui.homeCurveTree
        previous_signal_state = tree.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        try:
            for group_index in range(tree.topLevelItemCount()):
                group = tree.topLevelItem(group_index)
                for child_index in range(group.childCount()):
                    group.child(child_index).setCheckState(0, state)
        finally:
            tree.blockSignals(previous_signal_state)
        self.refresh_history_home()

    def _set_current_value_edits(self, latest: OperatorHistory | None) -> None:
        layout = self.ui.currentValuesGrid
        columns = list(OperatorHistory.__table__.columns)
        field_names = [column.name for column in columns]
        if list(self.current_value_edits) != field_names:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget() is not None:
                    item.widget().deleteLater()
            self.current_value_edits.clear()
            self.current_value_cards.clear()
            for index, column in enumerate(columns):
                card = QFrame(self.ui.currentValuesGroup)
                card.setObjectName("currentValueCard")
                card_layout = QVBoxLayout(card)
                card_layout.setContentsMargins(0, 0, 0, 0)
                card_layout.setSpacing(4)

                label = QLabel(HISTORY_LABELS.get(column.name, column.name), card)
                label.setObjectName("currentValueNameLabel")
                label.setToolTip(column.name)
                editor = QLineEdit(card)
                editor.setObjectName("currentValueEdit")
                editor.setReadOnly(True)
                editor.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                editor.setMinimumHeight(30)
                editor.setToolTip(column.name)
                card_layout.addWidget(label)
                card_layout.addWidget(editor)

                layout.addWidget(card, index // 9, index % 9)
                layout.setColumnStretch(index % 9, 1)
                self.current_value_cards[column.name] = card
                self.current_value_edits[column.name] = editor

        for column in columns:
            value = getattr(latest, column.name) if latest is not None else None
            if value is None:
                display = "--"
            elif column.name == "simu_time":
                display = format_simu_time(value)
            else:
                display = format_float(value) if isinstance(value, float) else str(value)
            self.current_value_edits[column.name].setText(display)

    def _curve_series(
        self, curves: list[HomeCurveDef], rows: list[OperatorHistory]
    ) -> list[CurveSeries]:
        result: list[CurveSeries] = []
        valid_fields = {column.name for column in OperatorHistory.__table__.columns}
        scada_models = {
            "scada_yc_his": ScadaYcHis,
            "scada_yx_his": ScadaYxHis,
            "scada_yt_his": ScadaYtHis,
            "scada_yk_his": ScadaYkHis,
        }
        with self.database.session() as session:
            for curve in curves:
                if curve.source_table == "operator_history" and curve.source_field in valid_fields:
                    points = [
                        (
                            float(row.simu_time),
                            round(float(getattr(row, curve.source_field)), 3),
                        )
                        for row in rows
                    ]
                elif curve.source_table in scada_models and curve.source_field == "value" and curve.pnt_no:
                    model = scada_models[curve.source_table]
                    history = session.execute(
                        select(model.time, model.value)
                        .where(model.pnt_no == curve.pnt_no)
                        .order_by(model.time)
                    ).all()
                    points = [
                        (float(row.time), round(float(row.value), 3))
                        for row in history
                    ]
                else:
                    continue
                result.append(CurveSeries(curve.name, curve.color, points, curve.line_width))
        return result

    def _log_filter_clauses(self) -> list[Any]:
        clauses: list[Any] = []
        if self.log_type_filter is not None:
            clauses.append(OperatorLog.log_type == self.log_type_filter)
        if self.log_start_seconds > 0:
            clauses.append(OperatorLog.simu_time >= self.log_start_seconds)
        if self.log_end_seconds is not None:
            clauses.append(OperatorLog.simu_time <= self.log_end_seconds)
        if self.log_keyword:
            clauses.append(OperatorLog.log_info.contains(self.log_keyword))
        return clauses

    def refresh_logs(self, *, preserve_selection: bool = True) -> None:
        table = self.ui.logTable
        current_item = table.item(table.currentRow(), 0) if table.currentRow() >= 0 else None
        selected_id = (
            int(current_item.data(Qt.ItemDataRole.UserRole))
            if preserve_selection
            and current_item is not None
            and current_item.data(Qt.ItemDataRole.UserRole) is not None
            else None
        )
        horizontal_scroll = table.horizontalScrollBar().value()
        vertical_scroll = table.verticalScrollBar().value()
        clauses = self._log_filter_clauses()
        with self.database.session() as session:
            self.log_total_count = int(
                session.scalar(
                    select(func.count())
                    .select_from(OperatorLog)
                    .where(*clauses)
                )
                or 0
            )
            self.log_total_pages = max(
                1,
                (self.log_total_count + self.log_page_size - 1)
                // self.log_page_size,
            )
            if selected_id is not None:
                selected_exists = session.scalar(
                    select(OperatorLog.id)
                    .where(OperatorLog.id == selected_id, *clauses)
                    .limit(1)
                )
                if selected_exists is not None:
                    newer_count = int(
                        session.scalar(
                            select(func.count())
                            .select_from(OperatorLog)
                            .where(OperatorLog.id > selected_id, *clauses)
                        )
                        or 0
                    )
                    self.log_page = newer_count // self.log_page_size + 1
            self.log_page = min(max(1, self.log_page), self.log_total_pages)
            offset = (self.log_page - 1) * self.log_page_size
            rows = session.scalars(
                select(OperatorLog)
                .where(*clauses)
                .order_by(OperatorLog.id.desc())
                .limit(self.log_page_size)
                .offset(offset)
            ).all()

        self.ui.logTotalLabel.setText(f"共 {self.log_total_count} 条")
        self.ui.logPageInfoLabel.setText(
            f"第 {self.log_page} / {self.log_total_pages} 页"
        )
        self.ui.logPreviousPageButton.setEnabled(self.log_page > 1)
        self.ui.logNextPageButton.setEnabled(self.log_page < self.log_total_pages)

        previous_signal_state = table.blockSignals(True)
        selected_row = -1
        try:
            table.clear()
            table.setColumnCount(5)
            table.setHorizontalHeaderLabels(
                ["墙钟时刻", "运行时刻", "类型", "决策 ID", "摘要"]
            )
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                parsed = self._parse_log_payload(row.log_info)
                decision_id = (
                    str(parsed.get("decision_id", ""))
                    if isinstance(parsed, dict)
                    else ""
                )
                values = [
                    format_wall_time(row.log_time),
                    format_simu_time(row.simu_time),
                    LOG_TYPE_NAMES.get(int(row.log_type), f"类型 {row.log_type}"),
                    decision_id,
                    self._log_summary(row, parsed),
                ]
                for column_index, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, int(row.id))
                    table.setItem(row_index, column_index, item)
                if selected_id == int(row.id):
                    selected_row = row_index
        finally:
            table.blockSignals(previous_signal_state)
        if rows:
            if selected_row < 0:
                selected_row = 0
            table.setCurrentCell(selected_row, 0)
        else:
            self.ui.logDetailTitleLabel.setText("日志详情")
            self.ui.logDetailTree.clear()
            self.ui.logRawText.clear()
        table.horizontalScrollBar().setValue(horizontal_scroll)
        table.verticalScrollBar().setValue(vertical_scroll)
        if rows:
            self.show_selected_log_detail()

    def change_log_page(self, offset: int) -> None:
        target_page = min(
            max(1, self.log_page + int(offset)),
            self.log_total_pages,
        )
        if target_page == self.log_page:
            return
        self.log_page = target_page
        self.refresh_logs(preserve_selection=False)

    def change_log_page_size(self, text: str) -> None:
        try:
            page_size = int(text)
        except (TypeError, ValueError):
            return
        if page_size not in (50, 100, 200, 500):
            return
        self.log_page_size = page_size
        self.log_page = 1
        self.refresh_logs(preserve_selection=False)

    @staticmethod
    def _parse_log_payload(log_info: str) -> Any:
        try:
            return json.loads(log_info)
        except (TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _log_summary(row: OperatorLog, parsed: Any) -> str:
        if int(row.log_type) == LOG_DECISION and isinstance(parsed, dict):
            mode = "闭环" if parsed.get("mode") == "closed" else "开环"
            totals = parsed.get("outputs", {}).get("totals", {})
            try:
                return (
                    f"{mode}决策 | 负荷 {format_float(totals.get('load_kw', 0.0))} kW | "
                    f"弃电 {format_float(totals.get('curtailment_kw', 0.0))} kW | "
                    f"失供 {format_float(totals.get('unserved_kw', 0.0))} kW"
                )
            except (TypeError, ValueError):
                return f"{mode}决策"
        return " ".join(str(row.log_info).splitlines())[:240]

    def apply_log_filters(self) -> None:
        try:
            start_text = self.ui.logStartEdit.text().strip()
            end_text = self.ui.logEndEdit.text().strip()
            start = parse_simu_time(start_text) if start_text else 0
            end = parse_simu_time(end_text) if end_text else None
            if end is not None and end < start:
                raise ValueError("运行结束时刻不能早于开始时刻")
            type_by_index = {
                0: None,
                1: LOG_INFO,
                2: LOG_WARNING,
                3: LOG_ERROR,
                4: LOG_DECISION,
            }
            self.log_type_filter = type_by_index[self.ui.logTypeCombo.currentIndex()]
            self.log_start_seconds = start
            self.log_end_seconds = end
            self.log_keyword = self.ui.logKeywordEdit.text().strip()
            self.log_page = 1
            self.refresh_logs(preserve_selection=False)
            self.show_success(
                f"运行日志查询完成，共 {self.log_total_count} 条，"
                f"当前第 {self.log_page}/{self.log_total_pages} 页"
            )
        except Exception as exc:
            self.show_error("运行日志查询条件无效", exc)

    def reset_log_filters(self) -> None:
        self.ui.logTypeCombo.setCurrentIndex(0)
        self.ui.logStartEdit.clear()
        self.ui.logEndEdit.clear()
        self.ui.logKeywordEdit.clear()
        self.log_type_filter = None
        self.log_start_seconds = 0
        self.log_end_seconds = None
        self.log_keyword = ""
        self.log_page = 1
        self.refresh_logs(preserve_selection=False)

    @staticmethod
    def _display_log_value(value: Any) -> str:
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, float):
            return format_float(value)
        if value is None:
            return ""
        return str(value)

    def _append_log_tree_value(
        self,
        parent: QTreeWidgetItem,
        key: str,
        value: Any,
    ) -> None:
        item = QTreeWidgetItem([str(key), ""])
        parent.addChild(item)
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                self._append_log_tree_value(item, str(child_key), child_value)
        elif isinstance(value, list):
            for index, child_value in enumerate(value, start=1):
                label = f"[{index}]"
                if isinstance(child_value, dict) and "name" in child_value:
                    label = f"[{index}] {child_value['name']}"
                self._append_log_tree_value(item, label, child_value)
        else:
            item.setText(1, self._display_log_value(value))

    @staticmethod
    def iter_tree_items(tree):
        def walk(item):
            yield item
            for child_index in range(item.childCount()):
                yield from walk(item.child(child_index))

        for top_index in range(tree.topLevelItemCount()):
            yield from walk(tree.topLevelItem(top_index))

    def show_selected_log_detail(self) -> None:
        table = self.ui.logTable
        item = table.item(table.currentRow(), 0) if table.currentRow() >= 0 else None
        if item is None or item.data(Qt.ItemDataRole.UserRole) is None:
            return
        log_id = int(item.data(Qt.ItemDataRole.UserRole))
        with self.database.session() as session:
            row = session.get(OperatorLog, log_id)
            if row is None:
                return
            log_info = str(row.log_info)
            log_type = int(row.log_type)
        parsed = self._parse_log_payload(log_info)
        tree = self.ui.logDetailTree
        tree.clear()
        if log_type == LOG_DECISION and isinstance(parsed, dict):
            decision_id = str(parsed.get("decision_id", ""))
            self.ui.logDetailTitleLabel.setText(f"控制决策详情  {decision_id}")
            groups = [
                ("触发条件", parsed.get("trigger", {})),
                ("输入", parsed.get("inputs", {})),
                ("决策过程", parsed.get("process", [])),
                ("输出", parsed.get("outputs", {})),
                ("平衡校验 / 警告", parsed.get("validation", {})),
            ]
            for label, value in groups:
                root = QTreeWidgetItem([label, ""])
                tree.addTopLevelItem(root)
                if isinstance(value, dict):
                    for key, child in value.items():
                        self._append_log_tree_value(root, str(key), child)
                elif isinstance(value, list):
                    for index, child in enumerate(value, start=1):
                        self._append_log_tree_value(root, f"[{index}]", child)
                else:
                    root.setText(1, self._display_log_value(value))
                root.setExpanded(True)
            raw_text = json.dumps(parsed, ensure_ascii=False, allow_nan=False, indent=2)
        else:
            self.ui.logDetailTitleLabel.setText(
                f"{LOG_TYPE_NAMES.get(log_type, f'类型 {log_type}')}日志详情"
            )
            tree.addTopLevelItem(QTreeWidgetItem(["日志内容", log_info]))
            raw_text = (
                json.dumps(parsed, ensure_ascii=False, allow_nan=False, indent=2)
                if parsed is not None
                else log_info
            )
        raw_scroll = self.ui.logRawText.verticalScrollBar().value()
        self.ui.logRawText.setPlainText(raw_text)
        self.ui.logRawText.verticalScrollBar().setValue(raw_scroll)

    def populate_history_tree(self) -> None:
        definitions = [
            ("遥测 YC", ScadaYc, "scada_yc_his", "#2f80ed"),
            ("遥信 YX", ScadaYx, "scada_yx_his", "#27ae60"),
            ("遥调 YT", ScadaYt, "scada_yt_his", "#f2994a"),
            ("遥控 YK", ScadaYk, "scada_yk_his", "#9b51e0"),
        ]
        tree = self.ui.historyTree
        checked: set[tuple[str, int]] = set()
        for root_index in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(root_index)
            if root is None:
                continue
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                if child is None or child.checkState(0) != Qt.CheckState.Checked:
                    continue
                key = child.data(0, Qt.ItemDataRole.UserRole)
                if key:
                    checked.add((str(key[0]), int(key[1])))
        tree.blockSignals(True)
        tree.clear()
        with self.database.session() as session:
            for label, current_model, history_table, color in definitions:
                root = QTreeWidgetItem([label])
                tree.addTopLevelItem(root)
                points = session.scalars(select(current_model).order_by(current_model.pnt_no)).all()
                for point in points:
                    child = QTreeWidgetItem([f"{point.pnt_no} - {point.name}"])
                    key = (history_table, point.pnt_no, point.name, color)
                    identity = (history_table, int(point.pnt_no))
                    child.setData(0, Qt.ItemDataRole.UserRole, key)
                    child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    child.setCheckState(
                        0,
                        Qt.CheckState.Checked
                        if identity in checked
                        else Qt.CheckState.Unchecked,
                    )
                    root.addChild(child)
                root.setExpanded(True)
        tree.blockSignals(False)

    def apply_history_filter(self) -> None:
        try:
            start_text = self.ui.historyStartEdit.text().strip()
            end_text = self.ui.historyEndEdit.text().strip()
            start = parse_simu_time(start_text) if start_text else 0
            end = parse_simu_time(end_text) if end_text else None
            if end is not None and end < start:
                raise ValueError("结束时刻不能早于开始时刻")
            self.history_start_seconds = start
            self.history_end_seconds = end
            self.refresh_history_plot()
            end_label = format_simu_time(end) if end is not None else "全部"
            self.show_success(f"历史查询范围：{format_simu_time(start)} 至 {end_label}")
        except Exception as exc:
            self.show_error("历史查询条件无效", exc)

    def refresh_history_plot(self) -> None:
        model_by_table = {
            "scada_yc_his": ScadaYcHis,
            "scada_yx_his": ScadaYxHis,
            "scada_yt_his": ScadaYtHis,
            "scada_yk_his": ScadaYkHis,
        }
        keys = []
        tree = self.ui.historyTree
        for root_index in range(tree.topLevelItemCount()):
            root = tree.topLevelItem(root_index)
            for child_index in range(root.childCount()):
                child = root.child(child_index)
                if child.checkState(0) == Qt.CheckState.Checked:
                    keys.append(tuple(child.data(0, Qt.ItemDataRole.UserRole)))
        series = []
        with self.database.session() as session:
            for table_name, pnt_no, point_name, color in keys:
                model = model_by_table[table_name]
                statement = select(model.time, model.value).where(
                    model.pnt_no == pnt_no,
                    model.time >= self.history_start_seconds,
                )
                if self.history_end_seconds is not None:
                    statement = statement.where(model.time <= self.history_end_seconds)
                rows = session.execute(
                    statement.order_by(model.time)
                ).all()
                points = [
                    (float(row.time), round(float(row.value), 3))
                    for row in rows
                ]
                series.append(CurveSeries(f"{pnt_no} {point_name}", color, points, 2.0))
        self.history_plot.set_series(series)
        if series:
            point_count = sum(len(curve.points) for curve in series)
            self.ui.historyStatusLabel.setText(
                f"已绘制 {len(series)} 条曲线，共 {point_count} 个历史点。"
            )
        else:
            self.ui.historyStatusLabel.setText("请在左侧勾选四遥点")

    @staticmethod
    def _table_is_being_edited(table: QTableWidget) -> bool:
        return table.state() == QAbstractItemView.State.EditingState

    def _editor_page_has_pending_changes(self, specs: list[EditorSpec]) -> bool:
        return any(
            spec.table in self._dirty_editor_tables
            or self._table_is_being_edited(spec.table)
            for spec in specs
        )

    def _refresh_periodic_page(
        self,
        index: int,
        *,
        protect_edits: bool,
        refresh_history_tree: bool,
    ) -> None:
        if index == 1:
            if protect_edits and self._editor_page_has_pending_changes(self.device_specs):
                self.statusBar().showMessage("设备定义存在未保存编辑，已跳过自动刷新", 5000)
                return
            self.load_devices()
        elif index == 2:
            if protect_edits and self._editor_page_has_pending_changes(self.scada_specs):
                self.statusBar().showMessage("四遥定义存在未保存编辑，已跳过自动刷新", 5000)
                return
            self.load_scada()
        elif index == 3:
            if protect_edits and self._table_is_being_edited(self.ui.logTable):
                self.statusBar().showMessage("运行日志表格正在编辑，已跳过自动刷新", 5000)
                return
            self.refresh_logs()
        elif index == 4:
            if refresh_history_tree:
                self.populate_history_tree()
            self.refresh_history_plot()

    def on_main_tab_changed(self, index: int) -> None:
        try:
            if index == 0:
                self.refresh_history_home()
                self.periodic_page_timer.stop()
            else:
                self._refresh_periodic_page(
                    index,
                    protect_edits=True,
                    refresh_history_tree=True,
                )
                self.periodic_page_timer.start()
        except Exception as exc:
            self.show_error("刷新页面失败", exc)

    def refresh_periodic_page(self) -> None:
        try:
            self._refresh_periodic_page(
                self.ui.mainTabs.currentIndex(),
                protect_edits=True,
                refresh_history_tree=False,
            )
        except Exception as exc:
            LOGGER.exception("当前页面运行周期刷新失败", exc_info=exc)
            self.statusBar().showMessage(f"页面刷新失败: {exc}", 5000)

    def refresh_live_data(self) -> None:
        try:
            self.refresh_control()
            self.refresh_io_connection_status()
            current = self.ui.mainTabs.currentIndex()
            if current == 0:
                self.refresh_history_home()
        except Exception as exc:
            LOGGER.exception("界面周期刷新失败", exc_info=exc)
            self.statusBar().showMessage(f"刷新失败: {exc}", 5000)

    def refresh_all(self) -> None:
        self.refresh_control()
        self.refresh_history_home()
        self.load_devices()
        self.load_scada()
        self.refresh_logs()
        self.populate_history_tree()
        self.refresh_history_plot()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.refresh_timer.stop()
        self.periodic_page_timer.stop()
        try:
            if self.runtime is not None and self._runtime_started:
                self.runtime.stop_and_wait()
                self._runtime_started = False
        except Exception:
            LOGGER.exception("关闭 MMI 时停止 Core/IO 子线程失败")
        finally:
            self.database.dispose()
            event.accept()


def main() -> None:
    parser = argparse.ArgumentParser(description="电力系统操作员 MMI")
    parser.add_argument("--db", default="ems.db", help="SQLite 数据库文件，默认 ems.db")
    parser.add_argument("--simulator-host", default="127.0.0.1")
    parser.add_argument("--simulator-port", type=int, default=9001)
    parser.add_argument("--rtu-id", type=int, default=1)
    parser.add_argument("--poll", type=float, default=0.5, help="IO 子线程轮询周期（秒）")
    parser.add_argument("--core-poll", type=float, default=0.5, help="Core 子线程轮询周期（秒）")
    parser.add_argument("--worker-start-timeout", type=float, default=10.0)
    parser.add_argument("--worker-stop-timeout", type=float, default=10.0)
    parser.add_argument(
        "--no-workers",
        action="store_true",
        help="仅启动界面，不启动 Core/IO 子线程（仅测试或维护使用）",
    )
    args = parser.parse_args()
    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        owner_reader = getattr(instance_guard, "existing_owner", None)
        owner = owner_reader() if callable(owner_reader) else None
        activated = bool(owner and activate_existing_instance(owner.pid))
        owner_text = f"（PID {owner.pid}）" if owner is not None else ""
        recovery_text = (
            "，已恢复并显示现有窗口"
            if activated
            else "；现有实例可能仍在启动或没有可见窗口"
        )
        print(
            f"operator_mmi 已经在运行{owner_text}{recovery_text}，"
            "禁止重复启动第二个实例。",
            file=sys.stderr,
            flush=True,
        )
        if activated:
            return
        raise SystemExit(2)

    database = None
    runtime = None
    try:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s",
        )
        database = Database(args.db)
        initialize_database(database)
        application = QApplication(sys.argv)
        application.setApplicationName("Power System Operator")
        if not args.no_workers:
            runtime = OperatorRuntimeThreads(
                database_path=database.path,
                simulator_host=args.simulator_host,
                simulator_port=args.simulator_port,
                rtu_id=args.rtu_id,
                poll_seconds=max(0.05, args.poll),
                core_poll_seconds=max(0.05, args.core_poll),
                startup_timeout=max(0.1, args.worker_start_timeout),
                stop_timeout=max(0.1, args.worker_stop_timeout),
            )
        window = OperatorMainWindow(database, runtime=runtime)
        if not ensure_window_visible(window):
            LOGGER.warning("MMI 原生窗口暂未可见，将在事件循环启动后再次恢复")
        QTimer.singleShot(250, lambda: ensure_window_visible(window))
        exit_code = application.exec()
    finally:
        if runtime is not None:
            try:
                runtime.stop_and_wait()
            except Exception:
                LOGGER.exception("MMI 退出清理 Core/IO 子线程失败")
        if database is not None:
            database.dispose()
        instance_guard.release()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
