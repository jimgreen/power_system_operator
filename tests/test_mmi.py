from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTreeWidget,
)
from sqlalchemy import event

from power_operator.core import CONTROL_CLOSED
from power_operator.database import Database, initialize_database
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    OperatorHistory,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYcHis,
)
from power_operator.time_utils import format_float, format_wall_time
from operator_mmi import OperatorMainWindow


def home_curve_leaf_items(tree):
    return [
        group.child(child_index)
        for group_index in range(tree.topLevelItemCount())
        for group in [tree.topLevelItem(group_index)]
        for child_index in range(group.childCount())
    ]


def layout_item_index(layout, target):
    return next(
        index
        for index in range(layout.count())
        if layout.itemAt(index).widget() is target
        or layout.itemAt(index).layout() is target
    )


def test_mmi_constructs_all_pages_and_editors(tmp_path):
    application = QApplication.instance() or QApplication([])
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(
        lambda session: (
            session.add(
                ScadaRtu(
                    id=1,
                    ip="127.0.0.1",
                    port=9200,
                    status=1,
                    refresh_time=1_787_422_688,
                )
            ),
            session.add(ScadaYc(pnt_no=1, name="simu.wind", value=8.5, time=3661)),
            setattr(session.get(OperatorControl, 1), "control_status", CONTROL_CLOSED),
            setattr(session.get(OperatorControl, 1), "data_period", 3),
            setattr(session.get(OperatorControl, 1), "oper_period", 15),
            setattr(session.get(OperatorControl, 1), "data_time_curr", 3661),
            setattr(session.get(OperatorControl, 1), "oper_time_curr", 7200),
        )
    )
    window = OperatorMainWindow(db)
    try:
        assert window.ui.mainTabs.count() == 5
        assert [
            window.ui.mainTabs.tabText(index)
            for index in range(window.ui.mainTabs.count())
        ] == ["系统主页", "设备定义", "RTU / 四遥定义", "运行日志", "历史曲线"]
        assert not hasattr(window.ui, "curvePage")
        assert window.ui.deviceTabs.count() == 5
        assert window.ui.scadaTabs.count() == 5
        assert window.ui.scadaTabs.widget(0).objectName() == "rtuPage"
        assert window.ui.ioConnectionStatusValue.text() == "连接成功"
        assert window.ui.simulatorConnectionCaptionLabel.text() == "电网模拟器连接"
        assert window.ui.simulatorConnectionStatusValue.text() == "正常"
        assert "#1b5e20" in window.ui.simulatorConnectionStatusValue.styleSheet()
        assert window.ui.connectSimulatorButton.text() == "建立连接"
        assert window.ui.disconnectSimulatorButton.text() == "中断连接"
        assert not window.ui.connectSimulatorButton.isEnabled()
        assert window.ui.disconnectSimulatorButton.isEnabled()
        assert layout_item_index(
            window.ui.controlLayout, window.ui.controlButtonLayout
        ) < layout_item_index(
            window.ui.controlLayout, window.ui.simulatorConnectionFrame
        )
        assert layout_item_index(
            window.ui.controlLayout, window.ui.controlButtonLayout
        ) < layout_item_index(
            window.ui.controlLayout, window.ui.simulatorConnectionButtonLayout
        )
        all_tables = window.findChildren(QTableWidget)
        assert {table.objectName() for table in all_tables} == {
            "diesalTable",
            "windTable",
            "solarTable",
            "estoreTable",
            "loadTable",
            "rtuTable",
            "ycTable",
            "yxTable",
            "ytTable",
            "ykTable",
            "logTable",
        }
        assert all(table.columnCount() > 0 for table in all_tables)
        assert "soc_init" not in {
            window.ui.estoreTable.horizontalHeaderItem(column).text()
            for column in range(window.ui.estoreTable.columnCount())
        }
        assert all(
            not table.horizontalHeader().stretchLastSection()
            for table in all_tables
        )
        assert all(
            table.horizontalHeader().sectionResizeMode(column)
            == QHeaderView.ResizeMode.Stretch
            for table in all_tables
            for column in range(table.columnCount())
        )
        all_trees = window.findChildren(QTreeWidget)
        assert {tree.objectName() for tree in all_trees} == {
            "homeCurveTree",
            "logDetailTree",
            "historyTree",
        }
        assert all(
            not tree.header().stretchLastSection()
            for tree in all_trees
        )
        assert all(
            tree.header().sectionResizeMode(column)
            == QHeaderView.ResizeMode.Stretch
            for tree in all_trees
            for column in range(tree.columnCount())
        )
        window.show()

        def assert_equal_widths(widget):
            application.processEvents()
            widths = [
                widget.columnWidth(column)
                for column in range(widget.columnCount())
            ]
            assert max(widths) - min(widths) <= 1, (
                f"{widget.objectName()} column widths are not equal: {widths}"
            )

        for width, height in [(1050, 680), (1480, 920), (1870, 920)]:
            window.resize(width, height)
            window.ui.mainTabs.setCurrentWidget(window.ui.devicePage)
            for index, spec in enumerate(window.device_specs):
                window.ui.deviceTabs.setCurrentIndex(index)
                assert_equal_widths(spec.table)
            window.ui.mainTabs.setCurrentWidget(window.ui.scadaPage)
            for index, spec in enumerate(window.scada_specs):
                window.ui.scadaTabs.setCurrentIndex(index)
                assert_equal_widths(spec.table)
            window.ui.mainTabs.setCurrentWidget(window.ui.logPage)
            assert_equal_widths(window.ui.logTable)
            assert_equal_widths(window.ui.logDetailTree)
        assert "RTU 1" in window.ui.ioConnectionDetailLabel.text()
        assert "127.0.0.1:9200" in window.ui.ioConnectionDetailLabel.text()
        assert format_wall_time(1_787_422_688) in window.ui.ioConnectionDetailLabel.text()
        assert not hasattr(window.ui, "currentValuesScroll")
        assert len(window.current_value_edits) == 17
        assert all(editor.text() == "--" for editor in window.current_value_edits.values())
        assert window.ui.operStatusCombo.count() == 3
        assert window.ui.controlModeCombo.count() == 2
        assert window.ui.controlModeCombo.currentIndex() == CONTROL_CLOSED
        assert window.ui.dataPeriodSpin.value() == 3
        assert window.ui.operPeriodSpin.value() == 15
        assert window.ui.labelOperPeriod.text() == "决策周期（墙钟秒）"
        assert window.ui.labelDataTime.text() == "数据时刻"
        assert window.ui.labelOperTime.text() == "控制时刻"
        assert window.ui.dataTimeValue.text() == "01:01:01"
        assert window.ui.operTimeValue.text() == "02:00:00"
        window.ui.controlModeCombo.setCurrentIndex(0)
        window.ui.dataPeriodSpin.setValue(7)
        window.ui.operPeriodSpin.setValue(21)
        window.save_control_parameters()
        assert window.periodic_page_timer.interval() == 7000
        with db.session() as session:
            saved_control = session.get(OperatorControl, 1)
            assert saved_control.control_status == 0
            assert saved_control.data_period == 7
            assert saved_control.oper_period == 21
        visible_text = " ".join(
            [
                window.windowTitle(),
                window.ui.controlGroup.title(),
                window.ui.labelDataPeriod.text(),
                window.ui.labelOperPeriod.text(),
                window.ui.labelDataTime.text(),
                window.ui.labelOperTime.text(),
                window.ui.currentValuesGroup.title(),
            ]
        )
        forbidden_label = "\u4eff\u771f"
        assert forbidden_label not in visible_text
        assert not window.ui.homeCurveSplitter.childrenCollapsible()
        assert not window.ui.historySplitter.childrenCollapsible()
        assert window.ui.homeCurveListPanel.minimumWidth() >= 300
        assert window.ui.homeCurveListPanel.maximumWidth() <= 420
        assert [
            window.ui.homeCurveTree.topLevelItem(index).text(0)
            for index in range(window.ui.homeCurveTree.topLevelItemCount())
        ] == ["环境", "柴油", "风机", "光伏", "储能", "负荷"]
        home_curve_items = home_curve_leaf_items(window.ui.homeCurveTree)
        assert len(home_curve_items) == 16
        assert window.ui.homeCurveTree.topLevelItem(0).childCount() == 3
        assert sum(
            item.checkState(0) == Qt.CheckState.Checked
            for item in home_curve_items
        ) == 5
        window.set_all_home_curves(False)
        assert all(
            item.checkState(0) == Qt.CheckState.Unchecked
            for item in home_curve_items
        )
        window.set_all_home_curves(True)
        assert all(
            item.checkState(0) == Qt.CheckState.Checked
            for item in home_curve_leaf_items(window.ui.homeCurveTree)
        )
        assert window.ui.historyStartEdit.text() == "00:00:00"
        window.ui.mainTabs.setCurrentIndex(4)
        application.processEvents()
        assert window.ui.historyTree.topLevelItemCount() == 4
        window.ui.historyStartEdit.setText("01:00:00")
        window.ui.historyEndEdit.setText("25:00:00")
        window.apply_history_filter()
        assert window.history_start_seconds == 3600
        assert window.history_end_seconds == 25 * 3600
        time_column = next(
            index
            for index in range(window.ui.ycTable.columnCount())
            if window.ui.ycTable.horizontalHeaderItem(index).text() == "刷新时刻"
        )
        assert window.ui.ycTable.item(0, time_column).text() == "01:01:01"
        refresh_column = next(
            index
            for index in range(window.ui.rtuTable.columnCount())
            if window.ui.rtuTable.horizontalHeaderItem(index).text() == "刷新时刻"
        )
        assert window.ui.rtuTable.item(0, refresh_column).text() == format_wall_time(
            1_787_422_688
        )
        window.ui.disconnectSimulatorButton.click()
        application.processEvents()
        with db.session() as session:
            assert session.get(OperatorControl, 1).io_connect_enabled == 0
            assert session.get(ScadaRtu, 1).status == 0
        assert window.ui.ioConnectionStatusValue.text() == "连接中断"
        assert window.ui.simulatorConnectionStatusValue.text() == "中断"
        assert "#b42318" in window.ui.simulatorConnectionStatusValue.styleSheet()
        assert window.ui.connectSimulatorButton.isEnabled()
        assert not window.ui.disconnectSimulatorButton.isEnabled()
        window.ui.connectSimulatorButton.click()
        application.processEvents()
        with db.session() as session:
            assert session.get(OperatorControl, 1).io_connect_enabled == 1
            assert session.get(ScadaRtu, 1).status == 0
        assert window.ui.simulatorConnectionStatusValue.text() == "中断"
        assert not window.ui.connectSimulatorButton.isEnabled()
        assert window.ui.disconnectSimulatorButton.isEnabled()
        assert "游标" in window.home_plot.toolTip()
        assert "游标" in window.history_plot.toolTip()
        assert application.font().family()
        application.processEvents()
    finally:
        window.close()


def test_secondary_pages_refresh_on_data_period_not_fast_timer(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    def set_periods(session, data_period, oper_period):
        control = session.get(OperatorControl, 1)
        control.data_period = data_period
        control.oper_period = oper_period

    database.write(lambda session: set_periods(session, 6, 17))
    window = OperatorMainWindow(database)
    calls = {
        "devices": 0,
        "scada": 0,
        "logs": 0,
        "home": 0,
        "history_tree": 0,
        "history_plot": 0,
    }

    def count(name):
        def invoke():
            calls[name] += 1

        return invoke

    window.load_devices = count("devices")
    window.load_scada = count("scada")
    window.refresh_logs = count("logs")
    window.refresh_history_home = count("home")
    window.populate_history_tree = count("history_tree")
    window.refresh_history_plot = count("history_plot")
    try:
        assert window.refresh_timer.interval() == 1000
        assert window.periodic_page_timer.interval() == 6000

        expected = {
            1: ("devices",),
            2: ("scada",),
            3: ("logs",),
            4: ("history_plot",),
        }
        for index, refreshed_names in expected.items():
            for name in calls:
                calls[name] = 0
            window.ui.mainTabs.setCurrentIndex(index)
            application.processEvents()
            switched_names = (
                ("history_tree", "history_plot") if index == 4 else refreshed_names
            )
            for name, count_value in calls.items():
                assert count_value == (1 if name in switched_names else 0)
            for name in calls:
                calls[name] = 0

            window.refresh_live_data()
            assert calls == {name: 0 for name in calls}

            window.refresh_periodic_page()
            for name, count_value in calls.items():
                assert count_value == (1 if name in refreshed_names else 0)

        for name in calls:
            calls[name] = 0
        window.ui.mainTabs.setCurrentIndex(0)
        application.processEvents()
        assert calls["home"] == 1
        assert all(value == 0 for name, value in calls.items() if name != "home")

        database.write(lambda session: set_periods(session, 9, 23))
        window.refresh_live_data()
        assert window.periodic_page_timer.interval() == 9000
    finally:
        window.close()


def test_float_displays_use_three_decimals_and_auto_refresh_preserves_edits(
    tmp_path,
):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevWindGen(
                    id=1,
                    name="W1",
                    p_rated=123.45678,
                    wind_in=3.12345,
                ),
                ScadaYc(
                    pnt_no=1,
                    name="simu.wind",
                    value=8.12345,
                    time=1,
                ),
                OperatorHistory(
                    simu_time=1,
                    wind_speed=8.12345,
                    amb_temp=22.34567,
                ),
                ScadaYcHis(time=1, pnt_no=1, value=8.12345),
            ]
        )

    database.write(seed)
    window = OperatorMainWindow(database)
    try:
        assert format_float(1.23456) == "1.235"
        p_rated_column = next(
            index
            for index in range(window.ui.windTable.columnCount())
            if window.ui.windTable.horizontalHeaderItem(index).text() == "p_rated"
        )
        yc_value_column = next(
            index
            for index in range(window.ui.ycTable.columnCount())
            if window.ui.ycTable.horizontalHeaderItem(index).text() == "value"
        )
        assert window.ui.windTable.item(0, p_rated_column).text() == "123.457"
        assert window.ui.ycTable.item(0, yc_value_column).text() == "8.123"
        assert window.current_value_edits["wind_speed"].text() == "8.123"
        assert window.current_value_edits["amb_temp"].text() == "22.346"
        assert len(window.current_value_edits) == 17
        assert len(window.current_value_cards) == 17
        for index, field_name in enumerate(OperatorHistory.__table__.columns.keys()):
            card = window.current_value_cards[field_name]
            card_index = window.ui.currentValuesGrid.indexOf(card)
            row, column, row_span, column_span = (
                window.ui.currentValuesGrid.getItemPosition(card_index)
            )
            assert (row, column, row_span, column_span) == (
                index // 9,
                index % 9,
                1,
                1,
            )

        home_curve_items = home_curve_leaf_items(window.ui.homeCurveTree)
        wind_current = next(
            item for item in home_curve_items if item.text(0) == "风电当前总出力 (kW)"
        )
        assert wind_current.checkState(0) == Qt.CheckState.Checked
        wind_current.setCheckState(0, Qt.CheckState.Unchecked)
        window.refresh_history_home()
        refreshed_wind_current = next(
            item
            for item in home_curve_leaf_items(window.ui.homeCurveTree)
            if item.text(0) == "风电当前总出力 (kW)"
        )
        assert refreshed_wind_current.checkState(0) == Qt.CheckState.Unchecked
        assert "已选择 4 条曲线，显示 1 个历史断面" in window.ui.homeCurveStatusLabel.text()

        environment_group = window.ui.homeCurveTree.topLevelItem(0)
        environment_group.setCheckState(0, Qt.CheckState.Checked)
        application.processEvents()
        assert all(
            environment_group.child(index).checkState(0) == Qt.CheckState.Checked
            for index in range(environment_group.childCount())
        )
        with database.session() as session:
            assert session.get(DevWindGen, 1).p_rated == 123.45678
            assert session.get(ScadaYc, 1).value == 8.12345

        window.ui.mainTabs.setCurrentIndex(1)
        application.processEvents()
        edited_item = window.ui.windTable.item(0, p_rated_column)
        edited_item.setText("999.999")
        application.processEvents()
        database.write(
            lambda session: setattr(
                session.get(DevWindGen, 1), "p_rated", 77.7777
            )
        )

        window.refresh_periodic_page()
        assert window.ui.windTable.item(0, p_rated_column).text() == "999.999"
        with database.session() as session:
            assert session.get(DevWindGen, 1).p_rated == 77.7777

        window.ui.mainTabs.setCurrentIndex(0)
        window.ui.mainTabs.setCurrentIndex(1)
        application.processEvents()
        assert window.ui.windTable.item(0, p_rated_column).text() == "999.999"

        window.ui.mainTabs.setCurrentIndex(4)
        application.processEvents()
        yc_root = window.ui.historyTree.topLevelItem(0)
        selected = yc_root.child(0)
        selected.setCheckState(0, Qt.CheckState.Checked)
        selected_text = selected.text(0)
        database.write(
            lambda session: setattr(
                session.get(ScadaYc, 1), "name", "simu.wind.renamed"
            )
        )

        window.refresh_periodic_page()
        assert yc_root.child(0).text(0) == selected_text
        assert yc_root.child(0).checkState(0) == Qt.CheckState.Checked
        assert window.history_plot.series[0].points[-1][1] == 8.123

        window.ui.mainTabs.setCurrentIndex(0)
        window.ui.mainTabs.setCurrentIndex(4)
        application.processEvents()
        refreshed = window.ui.historyTree.topLevelItem(0).child(0)
        assert refreshed.text(0) == "1 - simu.wind.renamed"
        assert refreshed.checkState(0) == Qt.CheckState.Checked
    finally:
        window.close()


def test_control_parameter_edits_are_highlighted_and_survive_auto_refresh(
    tmp_path,
    monkeypatch,
):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.control_status = 1
        control.data_period = 3
        control.oper_period = 15
        control.data_time_curr = 10
        control.oper_time_curr = 5

    database.write(seed)
    window = OperatorMainWindow(database)
    try:
        assert not window.ui.saveControlButton.isEnabled()
        assert window.ui.refreshControlButton.text() == "手动刷新参数"

        window.ui.controlModeCombo.setCurrentIndex(0)
        window.ui.dataPeriodSpin.setValue(7)
        window.ui.operPeriodSpin.setValue(21)
        application.processEvents()

        for widget in (
            window.ui.controlModeCombo,
            window.ui.dataPeriodSpin,
            window.ui.operPeriodSpin,
        ):
            assert widget.property("modified") is True
            assert widget in window._dirty_control_widgets
        assert window.ui.saveControlButton.isEnabled()

        def update_database(session):
            control = session.get(OperatorControl, 1)
            control.control_status = 1
            control.data_period = 9
            control.oper_period = 23
            control.data_time_curr = 30
            control.oper_time_curr = 25

        database.write(update_database)
        window.refresh_live_data()

        assert window.ui.controlModeCombo.currentIndex() == 0
        assert window.ui.dataPeriodSpin.value() == 7
        assert window.ui.operPeriodSpin.value() == 21
        assert window.ui.dataTimeValue.text() == "00:00:30"
        assert window.ui.operTimeValue.text() == "00:00:25"
        assert window.periodic_page_timer.interval() == 9000

        # A runtime status action must not silently save pending parameters.
        window.set_control_status(1)
        with database.session() as session:
            control = session.get(OperatorControl, 1)
            assert control.oper_status == 1
            assert control.control_status == 1
            assert control.data_period == 9
            assert control.oper_period == 23

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        window.ui.refreshControlButton.click()
        application.processEvents()

        assert window.ui.controlModeCombo.currentIndex() == 1
        assert window.ui.dataPeriodSpin.value() == 9
        assert window.ui.operPeriodSpin.value() == 23
        assert not window.ui.saveControlButton.isEnabled()
        assert not window._dirty_control_widgets
        assert all(
            widget.property("modified") is not True
            for widget in (
                window.ui.controlModeCombo,
                window.ui.dataPeriodSpin,
                window.ui.operPeriodSpin,
            )
        )
    finally:
        window.close()


def test_parameter_tables_show_pending_colors_and_only_manual_refresh_discards(
    tmp_path,
    monkeypatch,
):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add_all(
            [
                DevWindGen(
                    id=1,
                    name="W1",
                    p_rated=100.0,
                    wind_in=3.0,
                    wind_rated=12.0,
                    wind_cut=25.0,
                ),
                ScadaYc(pnt_no=1, name="环境.当前风速", value=8.0, time=1),
            ]
        )
    )
    window = OperatorMainWindow(database)

    def column_index(table, field_name):
        return next(
            index
            for index in range(table.columnCount())
            if table.horizontalHeaderItem(index).text() == field_name
        )

    try:
        wind_table = window.ui.windTable
        rated_column = column_index(wind_table, "p_rated")
        rated_item = wind_table.item(0, rated_column)
        baseline_color = rated_item.background().color().name()
        rated_item.setText("222.222")

        yc_table = window.ui.ycTable
        name_column = column_index(yc_table, "name")
        name_item = yc_table.item(0, name_column)
        name_item.setText("环境.人工修改风速")
        application.processEvents()

        assert wind_table.property("modified") is True
        assert yc_table.property("modified") is True
        assert rated_item.background().color().name() != baseline_color
        assert name_item.background().color().name() == "#ffe0a3"
        assert "未保存修改" in rated_item.toolTip()
        assert "未保存修改" in name_item.toolTip()
        assert window.ui.saveDevicesButton.isEnabled()
        assert window.ui.saveScadaButton.isEnabled()

        def update_database(session):
            session.get(DevWindGen, 1).p_rated = 77.7777
            session.get(ScadaYc, 1).name = "环境.数据库风速"

        database.write(update_database)
        window.ui.mainTabs.setCurrentWidget(window.ui.devicePage)
        window.refresh_periodic_page()
        assert wind_table.item(0, rated_column).text() == "222.222"
        window.ui.mainTabs.setCurrentWidget(window.ui.scadaPage)
        window.refresh_periodic_page()
        assert yc_table.item(0, name_column).text() == "环境.人工修改风速"

        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
        )
        window.ui.refreshDevicesButton.click()
        window.ui.refreshScadaButton.click()
        application.processEvents()

        assert wind_table.item(0, rated_column).text() == "77.778"
        assert yc_table.item(0, name_column).text() == "环境.数据库风速"
        assert wind_table.property("modified") is not True
        assert yc_table.property("modified") is not True
        assert not window.ui.saveDevicesButton.isEnabled()
        assert not window.ui.saveScadaButton.isEnabled()
    finally:
        window.close()


def test_device_parameters_are_editable_and_saved_without_overwriting_live_fields(
    tmp_path,
):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add(
            DevWindGen(
                id=2001,
                name="风力发电机1",
                p_rated=50.0,
                wind_in=3.0,
                wind_rated=12.0,
                wind_cut=25.0,
                status=1,
                p_max_curr=17.656,
                angle_pitch_curr=2.5,
                p_curr=17.656,
                p_set=17.656,
            )
        )
    )
    window = OperatorMainWindow(database)

    def column_index(name):
        return next(
            index
            for index in range(window.ui.windTable.columnCount())
            if window.ui.windTable.horizontalHeaderItem(index).text() == name
        )

    try:
        table = window.ui.windTable
        assert "angle_yaw_curr" not in [
            table.horizontalHeaderItem(index).text()
            for index in range(table.columnCount())
        ]
        assert table.editTriggers() & QAbstractItemView.EditTrigger.DoubleClicked
        assert table.editTriggers() & QAbstractItemView.EditTrigger.SelectedClicked
        assert table.editTriggers() & QAbstractItemView.EditTrigger.EditKeyPressed
        assert window.ui.deviceEditHintLabel.text().startswith("双击浅黄色单元格")
        assert not window.ui.saveDevicesButton.isEnabled()

        editable_fields = {"name", "p_rated", "wind_in", "wind_rated", "wind_cut"}
        read_only_fields = {
            "id",
            "status",
            "p_max_curr",
            "angle_pitch_curr",
            "p_curr",
            "p_set",
        }
        for field_name in editable_fields:
            item = table.item(0, column_index(field_name))
            assert item.flags() & Qt.ItemFlag.ItemIsEditable
            assert "可编辑" in item.toolTip()
            assert item.background().color().name() == "#fff8d6"
        for field_name in read_only_fields:
            item = table.item(0, column_index(field_name))
            assert not item.flags() & Qt.ItemFlag.ItemIsEditable
            assert "只读" in item.toolTip()
            assert item.background().color().name() == "#f1f3f5"

        blank_row = table.rowCount() - 1
        assert table.item(blank_row, column_index("id")).flags() & Qt.ItemFlag.ItemIsEditable
        assert not table.item(
            blank_row, column_index("p_curr")
        ).flags() & Qt.ItemFlag.ItemIsEditable

        rated_item = table.item(0, column_index("p_rated"))
        rated_item.setText("66.666")
        application.processEvents()
        assert table in window._dirty_editor_tables
        assert window.ui.saveDevicesButton.isEnabled()

        # A concurrent Core/IO live-value update must survive a parameter save.
        database.write(
            lambda session: setattr(
                session.get(DevWindGen, 2001), "p_curr", 23.456
            )
        )
        window.ui.saveDevicesButton.click()
        application.processEvents()
        with database.session() as session:
            saved = session.get(DevWindGen, 2001)
            assert saved.p_rated == 66.666
            assert saved.p_curr == 23.456
            assert saved.status == 1
        assert table not in window._dirty_editor_tables
        assert not window.ui.saveDevicesButton.isEnabled()
        assert table.item(0, column_index("p_rated")).text() == "66.666"
        assert table.item(0, column_index("p_curr")).text() == "23.456"

        errors = []
        window.show_error = lambda title, exc: errors.append((title, str(exc)))
        table.item(0, column_index("p_rated")).setText("abc")
        application.processEvents()
        window.ui.saveDevicesButton.click()
        application.processEvents()
        assert errors
        assert errors[0][0] == "保存设备定义失败"
        assert "p_rated" in errors[0][1]
        with database.session() as session:
            assert session.get(DevWindGen, 2001).p_rated == 66.666
        assert table.item(0, column_index("p_rated")).text() == "abc"
        assert table in window._dirty_editor_tables
        assert window.ui.saveDevicesButton.isEnabled()
    finally:
        window.close()


def test_each_device_type_saves_its_static_parameters(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add_all(
            [
                DevDiesalGen(
                    id=1001,
                    name="柴油发电机1",
                    p_rated=50.0,
                    p_max=50.0,
                    p_min=10.0,
                    p_coeff=0.2,
                    p_curr=12.0,
                ),
                DevWindGen(
                    id=2001,
                    name="风力发电机1",
                    p_rated=50.0,
                    wind_in=3.0,
                    wind_rated=12.0,
                    wind_cut=25.0,
                    p_curr=20.0,
                ),
                DevSolarGen(
                    id=3001,
                    name="光伏发电机1",
                    p_rated=80.0,
                    p_curr=30.0,
                ),
                DevEstore(
                    id=4001,
                    name="储能1",
                    p_charge_max=30.0,
                    p_charge_eff=0.95,
                    p_discharge_max=25.0,
                    p_discharge_eff=0.94,
                    battery_capacity=100.0,
                    soc_curr=0.5,
                    soc_max=0.9,
                    soc_min=0.1,
                ),
                DevLoad(id=5001, name="负荷1", p_curr=75.0),
            ]
        )
    )
    window = OperatorMainWindow(database)

    def set_field(table, field_name, value):
        column = next(
            index
            for index in range(table.columnCount())
            if table.horizontalHeaderItem(index).text() == field_name
        )
        item = table.item(0, column)
        assert item.flags() & Qt.ItemFlag.ItemIsEditable
        item.setText(value)

    try:
        set_field(window.ui.diesalTable, "p_coeff", "0.225")
        set_field(window.ui.windTable, "wind_cut", "26.500")
        set_field(window.ui.solarTable, "p_rated", "88.800")
        set_field(window.ui.estoreTable, "battery_capacity", "123.456")
        set_field(window.ui.loadTable, "name", "重要负荷1")
        application.processEvents()
        window.ui.saveDevicesButton.click()
        application.processEvents()

        with database.session() as session:
            assert session.get(DevDiesalGen, 1001).p_coeff == 0.225
            assert session.get(DevWindGen, 2001).wind_cut == 26.5
            assert session.get(DevSolarGen, 3001).p_rated == 88.8
            assert session.get(DevEstore, 4001).battery_capacity == 123.456
            assert session.get(DevLoad, 5001).name == "重要负荷1"
    finally:
        window.close()


def test_log_page_browses_decision_input_process_output_and_preserves_selection(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    audit = {
        "schema_version": 1,
        "event": "control_decision",
        "decision_id": "decision-3661-000001",
        "mode": "closed",
        "trigger": {"data_time_curr": 3661, "oper_period": 5},
        "inputs": {
            "totals": {"load_kw": 123.45678},
            "devices": {"dev_wind_gen": [{"id": 1, "p_max_curr": 80.12345}]},
        },
        "process": [
            {
                "step": 1,
                "name": "renewable_priority",
                "executed": True,
                "before": {"load_kw": 123.45678},
                "action": {"wind_kw": 80.12345},
                "after": {"remaining_kw": 43.33333},
                "reason": "renewable_first",
            }
        ],
        "outputs": {
            "devices": [{"table": "dev_wind_gen", "id": 1, "p_set_after": 80.12345}],
            "totals": {"curtailment_kw": 0.0, "unserved_kw": 0.0},
        },
        "validation": {
            "balance_error_kw": 0.00001,
            "tolerance_kw": 0.001,
            "within_tolerance": True,
            "warnings": [],
        },
    }

    def seed(session):
        session.add_all(
            [
                OperatorLog(
                    id=1,
                    log_time=1_787_422_688,
                    simu_time=3661,
                    log_type=4,
                    log_info=json.dumps(audit, ensure_ascii=False),
                ),
                OperatorLog(
                    id=2,
                    log_time=1_787_422_689,
                    simu_time=3662,
                    log_type=1,
                    log_info="普通运行日志",
                ),
            ]
        )

    database.write(seed)
    window = OperatorMainWindow(database)
    try:
        window.ui.mainTabs.setCurrentIndex(3)
        application.processEvents()
        assert window.ui.logSplitter.childrenCollapsible() is False
        assert [
            window.ui.logTable.horizontalHeaderItem(index).text()
            for index in range(window.ui.logTable.columnCount())
        ] == ["墙钟时刻", "运行时刻", "类型", "决策 ID", "摘要"]
        decision_row = next(
            row
            for row in range(window.ui.logTable.rowCount())
            if window.ui.logTable.item(row, 3).text() == "decision-3661-000001"
        )
        assert window.ui.logTable.item(decision_row, 0).text() == format_wall_time(
            1_787_422_688
        )
        assert window.ui.logTable.item(decision_row, 1).text() == "01:01:01"
        assert window.ui.logTable.item(decision_row, 2).text() == "控制决策"
        window.ui.logTable.setCurrentCell(decision_row, 0)
        application.processEvents()
        assert [
            window.ui.logDetailTree.topLevelItem(index).text(0)
            for index in range(window.ui.logDetailTree.topLevelItemCount())
        ] == ["触发条件", "输入", "决策过程", "输出", "平衡校验 / 警告"]
        detail_texts = []
        iterator = window.iter_tree_items(window.ui.logDetailTree)
        for item in iterator:
            detail_texts.extend([item.text(0), item.text(1)])
        assert "123.457" in detail_texts
        assert "80.123" in detail_texts
        raw = window.ui.logRawText.toPlainText()
        assert '"decision_id": "decision-3661-000001"' in raw
        assert "renewable_priority" in raw

        window.ui.logTypeCombo.setCurrentText("控制决策")
        window.ui.logStartEdit.setText("01:00:00")
        window.ui.logEndEdit.setText("02:00:00")
        window.ui.logKeywordEdit.setText("wind")
        window.apply_log_filters()
        assert window.ui.logTable.rowCount() == 1
        assert window.ui.logTable.item(0, 3).text() == "decision-3661-000001"
        window.ui.logTable.setCurrentCell(0, 0)
        window.ui.logDetailTabs.setCurrentIndex(1)
        selected_id = window.ui.logTable.item(0, 0).data(Qt.ItemDataRole.UserRole)

        database.write(
            lambda session: session.add(
                OperatorLog(
                    id=3,
                    log_time=1_787_422_690,
                    simu_time=3663,
                    log_type=4,
                    log_info=json.dumps(
                        {**audit, "decision_id": "decision-3663-000002"},
                        ensure_ascii=False,
                    ),
                )
            )
        )
        window.refresh_logs()
        assert window.ui.logTypeCombo.currentText() == "控制决策"
        assert window.ui.logKeywordEdit.text() == "wind"
        assert window.ui.logDetailTabs.currentIndex() == 1
        current = window.ui.logTable.item(window.ui.logTable.currentRow(), 0)
        assert current.data(Qt.ItemDataRole.UserRole) == selected_id

        window.ui.logResetButton.click()
        application.processEvents()
        assert window.ui.logTypeCombo.currentText() == "全部"
        assert window.ui.logStartEdit.text() == ""
        assert window.ui.logEndEdit.text() == ""
        assert window.ui.logKeywordEdit.text() == ""
    finally:
        window.close()


def test_log_page_falls_back_to_plain_text_for_legacy_non_json(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add(
            OperatorLog(
                log_time=1_787_422_688,
                simu_time=5,
                log_type=1,
                log_info="旧格式普通文本",
            )
        )
    )
    window = OperatorMainWindow(database)
    try:
        window.ui.mainTabs.setCurrentIndex(3)
        window.ui.logTable.setCurrentCell(0, 0)
        application.processEvents()
        assert "旧格式普通文本" in window.ui.logRawText.toPlainText()
        assert "旧格式普通文本" in window.ui.logDetailTree.topLevelItem(0).text(1)
    finally:
        window.close()


def test_history_has_no_data_limit_and_logs_use_database_pagination(tmp_path):
    application = QApplication.instance() or QApplication([])
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    row_count = 1205

    def seed(session):
        session.add(
            ScadaYc(
                pnt_no=1,
                name="环境.当前风速",
                value=float(row_count),
                time=row_count,
            )
        )
        session.add_all(
            [
                OperatorHistory(simu_time=index, wind_speed=float(index))
                for index in range(1, row_count + 1)
            ]
        )
        session.add_all(
            [
                ScadaYcHis(time=index, pnt_no=1, value=float(index))
                for index in range(1, row_count + 1)
            ]
        )
        session.add_all(
            [
                OperatorLog(
                    log_time=index,
                    simu_time=index,
                    log_type=1,
                    log_info=f"history-log-{index}",
                )
                for index in range(1, row_count + 1)
            ]
        )

    database.write(seed)
    log_queries: list[str] = []

    def capture_log_queries(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ):
        if "operator_log" in statement.lower():
            log_queries.append(statement)

    event.listen(database.engine, "before_cursor_execute", capture_log_queries)
    window = OperatorMainWindow(database)
    try:
        assert window.home_plot.series
        assert all(
            len(curve.points) == row_count for curve in window.home_plot.series
        )
        assert window.home_plot.series[0].points[0][0] == 1.0
        assert window.home_plot.series[0].points[-1][0] == float(row_count)
        assert window.log_page_size == 100
        assert window.log_page == 1
        assert window.log_total_count == row_count
        assert window.log_total_pages == 13
        assert window.ui.logTable.rowCount() == 100
        assert window.ui.logTotalLabel.text() == f"共 {row_count} 条"
        assert window.ui.logPageInfoLabel.text() == "第 1 / 13 页"
        assert not window.ui.logPreviousPageButton.isEnabled()
        assert window.ui.logNextPageButton.isEnabled()
        assert "history-log-1205" in window.ui.logTable.item(0, 4).text()
        assert any(
            " limit " in f" {statement.lower()} "
            and " offset " in f" {statement.lower()} "
            for statement in log_queries
        )

        window.ui.logNextPageButton.click()
        application.processEvents()
        assert window.log_page == 2
        assert window.ui.logTable.rowCount() == 100
        assert window.ui.logPageInfoLabel.text() == "第 2 / 13 页"
        assert "history-log-1105" in window.ui.logTable.item(0, 4).text()

        window.ui.logPreviousPageButton.click()
        application.processEvents()
        assert window.log_page == 1
        selected_id = window.ui.logTable.item(99, 0).data(Qt.ItemDataRole.UserRole)
        window.ui.logTable.setCurrentCell(99, 0)
        database.write(
            lambda session: session.add(
                OperatorLog(
                    log_time=row_count + 1,
                    simu_time=row_count + 1,
                    log_type=1,
                    log_info=f"history-log-{row_count + 1}",
                )
            )
        )
        window.refresh_logs()
        current = window.ui.logTable.item(window.ui.logTable.currentRow(), 0)
        assert window.log_page == 2
        assert window.ui.logTable.currentRow() == 0
        assert current.data(Qt.ItemDataRole.UserRole) == selected_id

        window.ui.logPageSizeCombo.setCurrentText("500")
        application.processEvents()
        assert window.log_page_size == 500
        assert window.log_page == 1
        assert window.log_total_count == row_count + 1
        assert window.log_total_pages == 3
        assert window.ui.logTable.rowCount() == 500
        assert window.ui.logPageInfoLabel.text() == "第 1 / 3 页"

        window.ui.mainTabs.setCurrentWidget(window.ui.historyPage)
        yc_point = window.ui.historyTree.topLevelItem(0).child(0)
        yc_point.setCheckState(0, Qt.CheckState.Checked)
        window.refresh_history_plot()
        assert len(window.history_plot.series) == 1
        assert len(window.history_plot.series[0].points) == row_count
        assert window.history_plot.series[0].points[0] == (1.0, 1.0)
        assert window.history_plot.series[0].points[-1] == (
            float(row_count),
            float(row_count),
        )

        with database.session() as session:
            assert session.query(OperatorHistory).count() == row_count
            assert session.query(ScadaYcHis).count() == row_count
            assert session.query(OperatorLog).count() == row_count + 1
    finally:
        window.close()
        event.remove(database.engine, "before_cursor_execute", capture_log_queries)
