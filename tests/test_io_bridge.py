from __future__ import annotations

import threading

import pytest
from sqlalchemy import event, func, select

from power_operator.core import OPER_RUNNING
from power_operator.database import Database, initialize_database
from power_operator.io_service import (
    OperatorIoBridge,
    SimulatorIoClient,
    _map_measurement_response_rows,
)
from power_operator.models import (
    DevWindGen,
    OperatorControl,
    OperatorHistory,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYcHis,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
    ScadaYxHis,
)
from simulator_io_mock import SimulatorState, ThreadingSimulatorServer


class RecordingCoreManager:
    def __init__(self, database: Database, *, stop_error: Exception | None = None):
        self.database = database
        self.stop_error = stop_error
        self.events: list[tuple[str, dict[str, int]]] = []

    def _snapshot(self) -> dict[str, int]:
        with self.database.session() as session:
            return {
                "data_time": session.get(OperatorControl, 1).data_time_curr,
                "oper_time": session.get(OperatorControl, 1).oper_time_curr,
                "history": session.scalar(select(func.count()).select_from(OperatorHistory)),
                "logs": session.scalar(select(func.count()).select_from(OperatorLog)),
                "yc_time": session.get(ScadaYc, 1).time,
            }

    def stop_and_wait(self) -> None:
        self.events.append(("stop", self._snapshot()))
        if self.stop_error is not None:
            raise self.stop_error

    def start(self) -> None:
        self.events.append(("start", self._snapshot()))


@pytest.mark.parametrize(
    ("old_time", "new_time"),
    [
        (60, 28800),
        (43200, 28800),
        (28800, 28800),
    ],
)
def test_new_run_sequence_clears_history_for_any_clock_direction_and_waits_until_ready(
    tmp_path, old_time, new_time
):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_time_curr = old_time
        control.oper_time_curr = old_time
        control.source_run_seq = 1
        control.source_time_start = 0
        control.source_runtime_ready = 1
        session.add_all(
            [
                ScadaYc(pnt_no=1, name="环境.当前风速", value=15.0, time=old_time),
                ScadaYx(pnt_no=2, name="风机.运行状态", value=1, time=old_time),
                OperatorHistory(simu_time=old_time, wind_speed=15.0),
                OperatorLog(
                    log_time=1,
                    simu_time=old_time,
                    log_type=1,
                    log_info="旧任务",
                ),
                ScadaYcHis(time=old_time, pnt_no=1, value=15.0),
                ScadaYxHis(time=old_time, pnt_no=2, value=1),
            ]
        )

    database.write(seed)
    responses = iter(
        [
            {
                "ok": True,
                "run_seq": 2,
                "simu_status": 0,
                "simu_time_start": new_time,
                "runtime_ready": False,
                "simu_time": new_time,
                "data": {
                    "yc": [{"value": 0.0, "time": 0}],
                    "yx": [{"value": 0, "time": 0}],
                },
            },
            {
                "ok": True,
                "run_seq": 2,
                "simu_status": 1,
                "simu_time_start": new_time,
                "runtime_ready": True,
                "simu_time": new_time + 60,
                "data": {
                    "yc": [{"value": 16.0, "time": new_time + 60}],
                    "yx": [{"value": 1, "time": new_time + 60}],
                },
            },
        ]
    )
    manager = RecordingCoreManager(database)
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: next(responses),
        core_process_manager=manager,
    )

    assert bridge._pull_measurements(old_time, 1) is True
    assert [event[0] for event in manager.events] == ["stop"]
    with database.session() as session:
        control = session.get(OperatorControl, 1)
        assert (
            control.source_run_seq,
            control.source_time_start,
            control.source_runtime_ready,
            control.data_time_curr,
            control.oper_time_curr,
        ) == (2, new_time, 0, new_time, 0)
        assert session.scalar(select(func.count()).select_from(OperatorHistory)) == 0
        assert session.scalar(select(func.count()).select_from(OperatorLog)) == 0
        assert (session.get(ScadaYc, 1).value, session.get(ScadaYc, 1).time) == (
            0.0,
            0,
        )

    assert bridge._pull_measurements(new_time, 1) is True
    assert [event[0] for event in manager.events] == ["stop", "start"]
    with database.session() as session:
        control = session.get(OperatorControl, 1)
        assert (
            control.source_run_seq,
            control.source_runtime_ready,
            control.data_time_curr,
        ) == (2, 1, new_time + 60)
        assert (session.get(ScadaYc, 1).value, session.get(ScadaYc, 1).time) == (
            16.0,
            new_time + 60,
        )


def test_bridge_pulls_measurements_on_data_period_and_pushes_changed_commands_each_second(tmp_path):
    wall_time = 1_787_422_688
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_period = 2
        control.source_run_seq = 7
        control.source_runtime_ready = 1
        session.add_all(
            [
                DevWindGen(id=1, name="W1", status=0),
                ScadaYc(pnt_no=1, name="simu.wind", value=0.0, time=0),
                ScadaYx(
                    pnt_no=2,
                    name="dev_wind_gen.1.status",
                    value=0,
                    time=0,
                ),
                ScadaYt(pnt_no=31, name="dev_wind_gen.1.p_set", value=20.5, time=2),
                ScadaYt(
                    pnt_no=33,
                    name="风力发电机1.偏航角设定",
                    value=15.0,
                    time=2,
                ),
                ScadaYt(
                    pnt_no=34,
                    name="风力发电机1.桨距角设定",
                    value=2.0,
                    time=2,
                ),
                ScadaYk(pnt_no=41, name="dev_wind_gen.1.status", value=1, time=2),
                ScadaYk(pnt_no=43, name="dev_wind_gen.1.status", value=0, time=3),
                ScadaYt(pnt_no=32, name="invalid.yt", value=99.0, time=0),
                ScadaYk(pnt_no=42, name="invalid.yk", value=1, time=0),
            ]
        )

    database.write(seed)
    requests: list[dict] = []

    def transport(request: dict) -> dict:
        requests.append(request)
        if request["action"] == "read":
            return {
                "ok": True,
                "simu_time": request["simu_time"],
                "data": {
                    "yc": [{"value": 8.2, "time": request["simu_time"]}],
                    "yx": [{"value": 0, "time": request["simu_time"]}],
                },
            }
        return {"ok": True}

    bridge = OperatorIoBridge(
        database, transport=transport, rtu_id=7, wall_clock=lambda: wall_time
    )
    bridge.tick(monotonic_time=0)
    bridge.tick(monotonic_time=0.5)
    bridge.tick(monotonic_time=1.0)

    assert [request["action"] for request in requests] == ["read", "write"]
    write = requests[1]
    assert set(write) == {"action", "run_seq", "data"}
    assert write["run_seq"] == 7
    assert [row["pnt_no"] for row in write["data"]["yt"]] == [31]
    assert [row["pnt_no"] for row in write["data"]["yk"]] == [41]
    with database.session() as session:
        assert session.get(OperatorControl, 1).data_time_curr == 2
        yc = session.get(ScadaYc, 1)
        yx = session.get(ScadaYx, 2)
        assert (yc.name, yc.value, yc.time) == ("simu.wind", 8.2, 2)
        assert (yx.name, yx.value, yx.time) == (
            "dev_wind_gen.1.status",
            0,
            2,
        )
        assert session.query(OperatorLog).filter_by(log_type=2).count() == 0
        assert session.get(ScadaRtu, 7).refresh_time == wall_time

    bridge.tick(monotonic_time=2.0)
    writes = [request for request in requests if request["action"] == "write"]
    assert len(writes) == 1


def test_bridge_marks_rtu_disconnected_when_transport_fails(tmp_path):
    wall_time = 1_787_422_688
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        session.add(
            ScadaRtu(
                id=7,
                ip="127.0.0.1",
                port=9200,
                status=1,
                refresh_time=12,
            )
        )

    database.write(seed)

    def failing_transport(_request):
        raise ConnectionError("peer unavailable")

    bridge = OperatorIoBridge(
        database, transport=failing_transport, rtu_id=7, wall_clock=lambda: wall_time
    )
    with pytest.raises(ConnectionError, match="peer unavailable"):
        bridge.tick(monotonic_time=0)

    with database.session() as session:
        rtu = session.get(ScadaRtu, 7)
        assert rtu.status == 0
        assert rtu.refresh_time == 12

    def recovered_transport(request):
        return {
            "ok": True,
            "simu_time": request["simu_time"],
            "yc": [],
            "yx": [],
        }

    bridge.transport = recovered_transport
    bridge.tick(monotonic_time=0.5)
    with database.session() as session:
        rtu = session.get(ScadaRtu, 7)
        assert rtu.status == 1
        assert rtu.refresh_time == wall_time

    database.write(
        lambda session: setattr(session.get(OperatorControl, 1), "oper_status", 0)
    )
    bridge.tick(monotonic_time=1.0)
    with database.session() as session:
        rtu = session.get(ScadaRtu, 7)
        assert rtu.status == 0
        assert rtu.refresh_time == wall_time


def test_bridge_honors_manual_connection_switch(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.io_connect_enabled = 0
        session.add(
            ScadaRtu(
                id=7,
                ip="127.0.0.1",
                port=9200,
                status=1,
                refresh_time=12,
            )
        )

    database.write(seed)
    requests: list[dict] = []

    def transport(request):
        requests.append(request)
        return {
            "ok": True,
            "simu_time": request["simu_time"],
            "yc": [],
            "yx": [],
        }

    bridge = OperatorIoBridge(database, transport=transport, rtu_id=7)
    bridge.tick(monotonic_time=0)
    assert requests == []
    with database.session() as session:
        rtu = session.get(ScadaRtu, 7)
        assert rtu.status == 0
        assert rtu.refresh_time == 12

    database.write(
        lambda session: setattr(
            session.get(OperatorControl, 1), "io_connect_enabled", 1
        )
    )
    bridge.tick(monotonic_time=0.5)
    assert [request["action"] for request in requests] == ["read"]


def test_bridge_uses_authoritative_simulator_time_and_nested_yc_yx_packet(tmp_path):
    wall_time = 1_787_422_688
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_period = 2
        control.data_time_curr = 50
        session.add_all(
            [
                ScadaYc(pnt_no=1, name="simu.wind", value=0.0, time=0),
                ScadaYx(
                    pnt_no=2001,
                    name="dev_wind_gen.1.status",
                    value=0,
                    time=0,
                ),
            ]
        )

    database.write(seed)

    def transport(request):
        assert request == {
            "action": "read",
            "rtu_id": 7,
            "simu_time": 52,
            "data": {"yc": [1], "yx": [2001]},
        }
        return {
            "ok": True,
            "simu_time": 37,
            "data": {
                "yc": [{"value": 8.2, "time": 37}],
                "yx": [{"value": 1, "time": 37}],
            },
        }

    bridge = OperatorIoBridge(
        database,
        transport=transport,
        rtu_id=7,
        peer_ip="127.0.0.1",
        peer_port=9001,
        wall_clock=lambda: wall_time,
    )
    bridge.tick(monotonic_time=0.0)

    with database.session() as session:
        assert session.get(OperatorControl, 1).data_time_curr == 37
        assert session.get(ScadaYc, 1).time == 37
        assert session.get(ScadaYx, 2001).time == 37
        rtu = session.get(ScadaRtu, 7)
        assert (rtu.status, rtu.refresh_time) == (1, wall_time)


def test_measurement_response_mapping_preserves_exact_request_positions():
    requested = [102, 999, 101, 102, 103]
    rows = [
        {"value": 0.5, "time": 37},
        {"value": None, "time": 0},
        {"value": 8.5, "time": 37},
        {"value": 0.5, "time": 37},
        {"value": 9.9, "time": 0},
    ]

    mapped, uses_row_time = _map_measurement_response_rows(
        rows, requested, 37, "YC"
    )

    assert uses_row_time is True
    assert [row["pnt_no"] for row in mapped] == requested
    assert [(row["value"], row["time"]) for row in mapped] == [
        (0.5, 37),
        (None, 0),
        (8.5, 37),
        (0.5, 37),
        (9.9, 0),
    ]


@pytest.mark.parametrize(
    ("rows", "requested", "message"),
    [
        ([], [101], "响应数量与请求不一致"),
        ([{"value": 1.0, "time": 1}], [101, 102], "响应数量与请求不一致"),
        (
            [{"pnt_no": 101, "value": 1.0, "time": 1}],
            [101],
            "只能包含 value 和 time",
        ),
        (
            [{"name": "不应返回点名", "value": 1.0, "time": 1}],
            [101],
            "只能包含 value 和 time",
        ),
    ],
)
def test_measurement_response_mapping_rejects_non_positional_packets(
    rows, requested, message
):
    with pytest.raises(ValueError, match=message):
        _map_measurement_response_rows(rows, requested, 1, "YC")


def test_bridge_rejects_short_positional_response_and_marks_rtu_disconnected(
    tmp_path,
):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        session.add_all(
            [
                ScadaRtu(
                    id=7,
                    ip="127.0.0.1",
                    port=9001,
                    status=1,
                    refresh_time=123,
                ),
                ScadaYc(pnt_no=101, name="known.yc", value=7.5, time=1),
            ]
        )

    database.write(seed)
    bridge = OperatorIoBridge(
        database,
        rtu_id=7,
        transport=lambda _request: {
            "ok": True,
            "simu_time": 2,
            "data": {"yc": [], "yx": []},
        },
    )

    with pytest.raises(ValueError, match="响应数量与请求不一致"):
        bridge.tick(monotonic_time=0.0)

    with database.session() as session:
        point = session.get(ScadaYc, 101)
        assert (point.value, point.time) == (7.5, 1)
        rtu = session.get(ScadaRtu, 7)
        assert (rtu.status, rtu.refresh_time) == (0, 123)


def test_bridge_uses_wall_clock_for_rtu_and_simulator_time_for_data(
    tmp_path, monkeypatch
):
    wall_time = 1_787_422_688
    monkeypatch.setattr("power_operator.io_service.time.time", lambda: wall_time)
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_time_curr = 50
        session.add(ScadaYc(pnt_no=1, name="simu.wind", value=0.0, time=0))

    database.write(seed)
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: {
            "ok": True,
            "simu_time": 37,
            "data": {
                "yc": [{"value": 8.2, "time": 37}],
                "yx": [],
            },
        },
        rtu_id=7,
    )

    bridge.tick(monotonic_time=0.0)

    with database.session() as session:
        assert session.get(OperatorControl, 1).data_time_curr == 37
        assert session.get(ScadaYc, 1).time == 37
        assert session.get(ScadaRtu, 7).refresh_time == wall_time


def test_clock_rollback_stops_core_clears_runtime_applies_packet_then_restarts(tmp_path):
    wall_time = 1_787_422_688
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_period = 5
        control.oper_period = 10
        control.data_time_curr = 100
        control.oper_time_curr = 90
        session.add_all(
            [
                ScadaRtu(id=7, ip="old", port=9001, status=1, refresh_time=123),
                ScadaYc(pnt_no=1, name="simu.wind", value=12.0, time=100),
                ScadaYx(
                    pnt_no=2,
                    name="dev_wind_gen.1.status",
                    value=0,
                    time=100,
                ),
                ScadaYt(
                    pnt_no=3,
                    name="dev_wind_gen.1.p_set",
                    value=44.0,
                    time=90,
                ),
                ScadaYk(
                    pnt_no=4,
                    name="dev_wind_gen.1.status",
                    value=1,
                    time=90,
                ),
                OperatorHistory(simu_time=100, wind_speed=12.0),
                OperatorLog(log_time=123, simu_time=100, log_type=0, log_info="old"),
                ScadaYcHis(time=100, pnt_no=1, value=12.0),
                ScadaYxHis(time=100, pnt_no=2, value=0),
                ScadaYtHis(time=90, pnt_no=3, value=44.0),
                ScadaYkHis(time=90, pnt_no=4, value=1),
            ]
        )

    database.write(seed)
    manager = RecordingCoreManager(database)
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: {
            "ok": True,
            "simu_time": 20,
            "data": {
                "yc": [{"value": 8.2, "time": 20}],
                "yx": [{"value": 1, "time": 20}],
            },
        },
        rtu_id=7,
        peer_ip="127.0.0.1",
        peer_port=9001,
        wall_clock=lambda: wall_time,
        core_process_manager=manager,
    )
    bridge._last_data_monotonic = 90.0
    bridge._last_control_monotonic = 91.0
    bridge._last_yt_time = 90
    bridge._last_yk_time = 90

    writes: list[str] = []

    def capture_writes(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith(("update ", "delete ")):
            writes.append(normalized)

    event.listen(database.engine, "before_cursor_execute", capture_writes)
    try:
        bridge.tick(monotonic_time=100.0)
    finally:
        event.remove(database.engine, "before_cursor_execute", capture_writes)

    clock_rollback_sql = next(
        index
        for index, statement in enumerate(writes)
        if statement.startswith("update operator_control")
    )
    history_delete_sql = next(
        index
        for index, statement in enumerate(writes)
        if statement.startswith("delete from operator_history")
    )
    yc_updates = [
        index
        for index, statement in enumerate(writes)
        if statement.startswith("update scada_yc")
    ]
    assert clock_rollback_sql < history_delete_sql < yc_updates[0] < yc_updates[1]

    assert manager.events == [
        (
            "stop",
            {
                "data_time": 100,
                "oper_time": 90,
                "history": 1,
                "logs": 1,
                "yc_time": 100,
            },
        ),
        (
            "start",
            {
                "data_time": 20,
                "oper_time": 0,
                "history": 0,
                "logs": 0,
                "yc_time": 20,
            },
        ),
    ]
    with database.session() as session:
        control = session.get(OperatorControl, 1)
        assert (control.data_time_curr, control.oper_time_curr) == (20, 0)
        assert (session.get(ScadaYc, 1).value, session.get(ScadaYc, 1).time) == (
            8.2,
            20,
        )
        assert (session.get(ScadaYx, 2).value, session.get(ScadaYx, 2).time) == (
            1,
            20,
        )
        assert (session.get(ScadaYt, 3).value, session.get(ScadaYt, 3).time) == (
            0.0,
            0,
        )
        assert (session.get(ScadaYk, 4).value, session.get(ScadaYk, 4).time) == (
            0,
            0,
        )
        for model in (
            OperatorHistory,
            OperatorLog,
            ScadaYcHis,
            ScadaYxHis,
            ScadaYtHis,
            ScadaYkHis,
        ):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        rtu = session.get(ScadaRtu, 7)
        assert (rtu.status, rtu.refresh_time) == (1, wall_time)
    assert bridge._last_data_monotonic is None
    assert bridge._last_control_monotonic is None
    assert bridge._last_yt_time == 0
    assert bridge._last_yk_time == 0


@pytest.mark.parametrize("response_time", [100, 101])
def test_equal_or_forward_clock_does_not_restart_core_or_clear_history(
    tmp_path, response_time
):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_time_curr = 100
        session.add(OperatorHistory(simu_time=100))

    database.write(seed)
    manager = RecordingCoreManager(database)
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: {
            "ok": True,
            "simu_time": response_time,
            "data": {"yc": [], "yx": []},
        },
        core_process_manager=manager,
    )

    bridge.tick(monotonic_time=0.0)

    assert manager.events == []
    with database.session() as session:
        assert session.get(OperatorControl, 1).data_time_curr == response_time
        assert session.scalar(select(func.count()).select_from(OperatorHistory)) == 1


def test_clock_rollback_stop_failure_leaves_runtime_data_untouched(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_time_curr = 100
        control.oper_time_curr = 90
        session.add_all(
            [
                ScadaRtu(id=1, ip="127.0.0.1", port=9001, status=1, refresh_time=123),
                ScadaYc(pnt_no=1, name="simu.wind", value=12.0, time=100),
                OperatorHistory(simu_time=100),
                OperatorLog(log_time=123, simu_time=100, log_type=0, log_info="old"),
            ]
        )

    database.write(seed)
    manager = RecordingCoreManager(database, stop_error=RuntimeError("core stop failed"))
    bridge = OperatorIoBridge(
        database,
            transport=lambda _request: {
                "ok": True,
                "simu_time": 20,
                "data": {"yc": [{"value": 8.2, "time": 20}], "yx": []},
            },
        core_process_manager=manager,
    )

    with pytest.raises(RuntimeError, match="core stop failed"):
        bridge.tick(monotonic_time=0.0)

    assert [event[0] for event in manager.events] == ["stop"]
    with database.session() as session:
        control = session.get(OperatorControl, 1)
        assert (control.data_time_curr, control.oper_time_curr) == (100, 90)
        assert (session.get(ScadaYc, 1).value, session.get(ScadaYc, 1).time) == (
            12.0,
            100,
        )
        assert session.scalar(select(func.count()).select_from(OperatorHistory)) == 1
        assert session.scalar(select(func.count()).select_from(OperatorLog)) == 1


def test_clock_rollback_apply_failure_rolls_back_and_recovers_core(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_time_curr = 100
        control.oper_time_curr = 90
        session.add_all(
            [
                ScadaYc(pnt_no=1, name="simu.wind", value=12.0, time=100),
                OperatorHistory(simu_time=100),
                OperatorLog(log_time=123, simu_time=100, log_type=0, log_info="old"),
            ]
        )

    database.write(seed)
    manager = RecordingCoreManager(database)
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: {
            "ok": True,
            "simu_time": 20,
            "data": {"yc": [{"value": None, "time": 20}], "yx": []},
        },
        core_process_manager=manager,
    )

    with pytest.raises(ValueError, match="value"):
        bridge.tick(monotonic_time=0.0)

    assert [event[0] for event in manager.events] == ["stop", "start"]
    assert manager.events[-1][1] == {
        "data_time": 100,
        "oper_time": 90,
        "history": 1,
        "logs": 1,
        "yc_time": 100,
    }
    with database.session() as session:
        control = session.get(OperatorControl, 1)
        assert (control.data_time_curr, control.oper_time_curr) == (100, 90)
        assert (session.get(ScadaYc, 1).value, session.get(ScadaYc, 1).time) == (
            12.0,
            100,
        )
        assert session.scalar(select(func.count()).select_from(OperatorHistory)) == 1
        assert session.scalar(select(func.count()).select_from(OperatorLog)) == 1


def test_bridge_rejects_yc_yx_packet_without_simulator_time(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: setattr(
            session.get(OperatorControl, 1), "oper_status", OPER_RUNNING
        )
    )
    bridge = OperatorIoBridge(
        database,
        transport=lambda _request: {"ok": True, "yc": [], "yx": []},
        rtu_id=7,
    )

    with pytest.raises(ValueError, match="simu_time"):
        bridge.tick(monotonic_time=0.0)

    with database.session() as session:
        assert session.get(OperatorControl, 1).data_time_curr == 0
        assert session.get(ScadaRtu, 7).status == 0


def test_real_tcp_bridge_connection_switch_and_yk_status_filter(tmp_path):
    wall_times = iter([1_787_422_688, 1_787_422_689])
    class RecordingSimulatorState(SimulatorState):
        def __init__(self):
            super().__init__()
            self.exchanges: list[tuple[dict, dict]] = []

        def handle(self, request):
            response = super().handle(request)
            self.exchanges.append((request, response))
            return response

    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.io_connect_enabled = 0
        control.data_period = 1
        control.source_run_seq = 1
        control.source_runtime_ready = 1
        session.add_all(
            [
                ScadaRtu(
                    id=7,
                    ip="127.0.0.1",
                    port=0,
                    status=1,
                    refresh_time=12,
                ),
                ScadaYt(
                    pnt_no=9001,
                    name="dev_wind_gen.1.p_set",
                    value=32.0,
                    time=1,
                ),
                ScadaYk(
                    pnt_no=2001,
                    name="dev_wind_gen.1.status",
                    value=1,
                    time=1,
                ),
                ScadaYx(
                    pnt_no=2001,
                    name="dev_wind_gen.1.status",
                    value=0,
                    time=0,
                ),
            ]
        )

    database.write(seed)
    state = RecordingSimulatorState()
    server = ThreadingSimulatorServer(("127.0.0.1", 0), state)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    host, port = server.server_address
    bridge = OperatorIoBridge(
        database,
        transport=SimulatorIoClient(host, port, timeout=2.0),
        rtu_id=7,
        peer_ip=host,
        peer_port=port,
        wall_clock=lambda: next(wall_times),
    )
    try:
        bridge.tick(monotonic_time=0.0)
        assert state.exchanges == []
        with database.session() as session:
            rtu = session.get(ScadaRtu, 7)
            assert (rtu.status, rtu.refresh_time) == (0, 12)

        database.write(
            lambda session: setattr(
                session.get(OperatorControl, 1), "io_connect_enabled", 1
            )
        )
        bridge.tick(monotonic_time=0.5)
        assert [request["action"] for request, _ in state.exchanges] == ["read"]
        with database.session() as session:
            rtu = session.get(ScadaRtu, 7)
            assert (rtu.status, rtu.refresh_time) == (1, 1_787_422_688)

        database.write(
            lambda session: setattr(
                session.get(OperatorControl, 1), "data_period", 100
            )
        )
        bridge.tick(monotonic_time=1.6)
        write_responses = [
            response
            for request, response in state.exchanges
            if request["action"] == "write"
        ]
        assert write_responses == [
            {
                "ok": True,
                "run_seq": 1,
                "simu_time": 1,
                "accepted_yt": 1,
                "accepted_yk": 0,
            }
        ]

        def require_status_change(session):
            yx = session.get(ScadaYx, 2001)
            yx.value = 0
            yx.time = 2
            yk = session.get(ScadaYk, 2001)
            yk.time = 2
            session.get(OperatorControl, 1).data_time_curr = 2

        database.write(require_status_change)
        bridge.tick(monotonic_time=2.7)
        write_responses = [
            response
            for request, response in state.exchanges
            if request["action"] == "write"
        ]
        assert [response["accepted_yk"] for response in write_responses] == [0, 1]

        def disconnect(session):
            session.get(OperatorControl, 1).io_connect_enabled = 0

        database.write(disconnect)
        exchange_count = len(state.exchanges)
        bridge.tick(monotonic_time=2.8)
        assert len(state.exchanges) == exchange_count
        with database.session() as session:
            rtu = session.get(ScadaRtu, 7)
            assert (rtu.status, rtu.refresh_time) == (0, 1_787_422_688)

        database.write(
            lambda session: setattr(
                session.get(OperatorControl, 1), "io_connect_enabled", 1
            )
        )
        bridge.tick(monotonic_time=2.9)
        assert len(state.exchanges) == exchange_count + 1
        assert state.exchanges[-1][0]["action"] == "read"
        with database.session() as session:
            rtu = session.get(ScadaRtu, 7)
            assert (rtu.status, rtu.refresh_time) == (1, 1_787_422_689)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
