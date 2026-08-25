from __future__ import annotations

import json

from power_operator.database import Database, initialize_database
from power_operator.io_service import apply_rtu_request, update_existing_scada_points
from power_operator.models import (
    DevWindGen,
    OperatorControl,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYk,
    ScadaYt,
    ScadaYx,
)


def test_rtu_exchange_updates_measurements_and_returns_changed_controls(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(
        lambda session: (
            session.add(
                DevWindGen(
                    id=1,
                    name="风力发电机1",
                    status=0,
                    control_mode=1,
                )
            ),
            session.add(
                ScadaYc(
                    pnt_no=101,
                    name="dev_wind_gen.1.p_curr",
                    value=0.0,
                    time=0,
                )
            ),
            session.add(
                ScadaYx(
                    pnt_no=201,
                    name="dev_wind_gen.1.status",
                    value=0,
                    time=0,
                )
            ),
            session.add(ScadaYt(pnt_no=301, name="dev_wind_gen.1.p_set", value=42.5, time=20)),
            session.add(ScadaYk(pnt_no=401, name="dev_wind_gen.1.status", value=0, time=21)),
            session.add(ScadaYk(pnt_no=402, name="dev_wind_gen.1.status", value=1, time=22)),
        )
    )

    response = apply_rtu_request(
        db,
        {
            "rtu_id": 7,
            "simu_time": 88,
            "yc": [
                {"pnt_no": 101, "name": "REMOTE.yc.name", "value": 38.2},
                {"pnt_no": 102, "name": "REMOTE.unknown.yc", "value": 99.0},
            ],
            "yx": [
                {"pnt_no": 201, "name": "REMOTE.yx.name", "value": 1},
                {"pnt_no": 202, "name": "REMOTE.unknown.yx", "value": 1},
            ],
            "last_yt_time": 19,
            "last_yk_time": 20,
        },
        now=100,
    )

    assert response["ok"] is True
    assert response["simu_time"] == 88
    assert response["yt"] == [
        {"pnt_no": 301, "name": "dev_wind_gen.1.p_set", "value": 42.5, "time": 20}
    ]
    assert response["yk"] == [
        {"pnt_no": 401, "name": "dev_wind_gen.1.status", "value": 0, "time": 21}
    ]
    with db.session() as session:
        yc = session.get(ScadaYc, 101)
        yx = session.get(ScadaYx, 201)
        assert (yc.name, yc.value, yc.time) == (
            "dev_wind_gen.1.p_curr",
            38.2,
            88,
        )
        assert (yx.name, yx.value, yx.time) == (
            "dev_wind_gen.1.status",
            1,
            88,
        )
        assert session.get(ScadaYc, 102) is None
        assert session.get(ScadaYx, 202) is None
        warnings = (
            session.query(OperatorLog)
            .filter_by(log_type=2)
            .order_by(OperatorLog.id)
            .all()
        )
        warning_payloads = [json.loads(row.log_info) for row in warnings]
        assert [
            (row["signal"], row["pnt_no"], row["source"])
            for row in warning_payloads
        ] == [
            ("YC", 102, "operator_io.compat_server"),
            ("YX", 202, "operator_io.compat_server"),
        ]
        assert session.get(OperatorControl, 1).data_time_curr == 88
        assert session.get(ScadaRtu, 7).refresh_time == 100


def test_rtu_exchange_ignores_zero_time_measurements_and_never_returns_zero_time_commands(
    tmp_path,
):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(
        lambda session: session.add_all(
            [
                DevWindGen(
                    id=1,
                    name="风力发电机1",
                    status=0,
                    control_mode=1,
                ),
                ScadaYx(pnt_no=4, name="valid.yx", value=0, time=1),
                ScadaYt(pnt_no=1, name="zero.yt", value=10.0, time=0),
                ScadaYt(
                    pnt_no=2,
                    name="dev_wind_gen.1.p_set",
                    value=20.0,
                    time=1,
                ),
                ScadaYt(
                    pnt_no=5,
                    name="风力发电机1.偏航角设定",
                    value=30.0,
                    time=1,
                ),
                ScadaYt(
                    pnt_no=6,
                    name="风力发电机1.桨距角设定",
                    value=2.0,
                    time=1,
                ),
                ScadaYk(pnt_no=3, name="zero.yk", value=1, time=0),
                ScadaYk(
                    pnt_no=4,
                    name="dev_wind_gen.1.status",
                    value=1,
                    time=1,
                ),
            ]
        )
    )

    response = apply_rtu_request(
        db,
        {
            "rtu_id": 1,
            "simu_time": 0,
            "yc": [{"pnt_no": 10, "name": "invalid.yc", "value": 8.0}],
            "yx": [{"pnt_no": 11, "name": "invalid.yx", "value": 1}],
            "last_yt_time": -1,
            "last_yk_time": -1,
        },
        now=100,
    )

    assert [row["pnt_no"] for row in response["yt"]] == [2]
    assert [row["pnt_no"] for row in response["yk"]] == [4]
    with db.session() as session:
        assert session.get(ScadaYc, 10) is None
        assert session.get(ScadaYx, 11) is None
        assert session.get(OperatorControl, 1).data_time_curr == 0


def test_rtu_exchange_does_not_recreate_removed_wind_yaw_measurements(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add(
            ScadaYc(
                pnt_no=200101,
                name="风力发电机1.当前有功",
                value=0.0,
                time=0,
            )
        )
    )

    response = apply_rtu_request(
        database,
        {
            "rtu_id": 1,
            "simu_time": 10,
            "yc": [
                {
                    "pnt_no": 200101,
                    "name": "风力发电机1.当前有功",
                    "value": 30.0,
                },
                {
                    "pnt_no": 200103,
                    "name": "风力发电机1.当前偏航角",
                    "value": 3.5,
                },
                {
                    "pnt_no": 200203,
                    "name": "dev_wind_gen.2.angle_yaw_curr",
                    "value": 4.5,
                },
                {
                    "pnt_no": 100102,
                    "name": "柴油发电机1.本步柴油消耗",
                    "value": 0.25,
                },
                {
                    "pnt_no": 200102,
                    "name": "风力发电机1.理论最大有功",
                    "value": 50.0,
                },
                {
                    "pnt_no": 200105,
                    "name": "风力发电机1.有功功率设定值",
                    "value": 40.0,
                },
            ],
            "yx": [],
        },
        now=100,
    )

    assert response["ok"] is True
    with database.session() as session:
        assert session.get(ScadaYc, 200101).value == 30.0
        assert session.get(ScadaYc, 200103) is None
        assert session.get(ScadaYc, 200203) is None
        assert session.get(ScadaYc, 100102) is None
        assert session.get(ScadaYc, 200102) is None
        assert session.get(ScadaYc, 200105) is None
        assert session.query(OperatorLog).count() == 0


def test_runtime_update_ignores_retired_scada_measurements(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed_and_update(session):
        session.add_all(
            [
                ScadaYc(
                    pnt_no=100102,
                    name="柴油发电机1.本步柴油消耗",
                    value=0.0,
                    time=0,
                ),
                ScadaYc(
                    pnt_no=200102,
                    name="风力发电机1.理论最大有功",
                    value=0.0,
                    time=0,
                ),
                ScadaYc(
                    pnt_no=200105,
                    name="风力发电机1.有功功率设定值",
                    value=0.0,
                    time=0,
                ),
                ScadaYc(
                    pnt_no=200101,
                    name="风力发电机1.当前有功",
                    value=0.0,
                    time=0,
                ),
                ScadaYt(
                    pnt_no=200102,
                    name="风力发电机1.偏航角设定",
                    value=0.0,
                    time=0,
                ),
                ScadaYt(
                    pnt_no=200104,
                    name="风力发电机1.桨距角设定",
                    value=0.0,
                    time=0,
                ),
                ScadaYt(
                    pnt_no=200105,
                    name="风力发电机1.有功设定",
                    value=0.0,
                    time=0,
                ),
            ]
        )
        session.flush()
        update_existing_scada_points(
            session,
            ScadaYc,
            [
                {"pnt_no": 100102, "name": "远端改名", "value": 0.25},
                {"pnt_no": 200102, "name": "远端改名", "value": 50.0},
                {"pnt_no": 200105, "name": "远端改名", "value": 40.0},
                {"pnt_no": 200101, "name": "远端改名", "value": 30.0},
            ],
            42,
            log_wall_time=100,
            source="test.receiver",
        )
        update_existing_scada_points(
            session,
            ScadaYt,
            [
                {"pnt_no": 200102, "name": "远端改名", "value": 15.0},
                {"pnt_no": 200104, "name": "远端改名", "value": 5.0},
                {"pnt_no": 200105, "name": "远端改名", "value": 35.0},
            ],
            42,
            log_wall_time=100,
            source="test.receiver",
        )

    database.write(seed_and_update)

    with database.session() as session:
        for pnt_no in (100102, 200102, 200105):
            retired_yc = session.get(ScadaYc, pnt_no)
            assert (retired_yc.value, retired_yc.time) == (0.0, 0)
        active_yc = session.get(ScadaYc, 200101)
        assert (active_yc.value, active_yc.time) == (30.0, 42)
        retired = session.get(ScadaYt, 200102)
        retired_pitch = session.get(ScadaYt, 200104)
        active = session.get(ScadaYt, 200105)
        assert (retired.value, retired.time) == (0.0, 0)
        assert (retired_pitch.value, retired_pitch.time) == (0.0, 0)
        assert (active.value, active.time) == (35.0, 42)


def test_runtime_scada_updates_only_value_and_time_and_warns_for_all_unknown_signals(
    tmp_path,
    caplog,
):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    caplog.set_level("WARNING", logger="power_operator.io_service")
    cases = [
        (ScadaYc, "YC", 1.25, 9.75),
        (ScadaYx, "YX", 0, 1),
        (ScadaYt, "YT", 2.5, 8.5),
        (ScadaYk, "YK", 1, 0),
    ]

    def exercise(session):
        for model, signal, initial_value, _received_value in cases:
            session.add(
                model(
                    pnt_no=1,
                    name=f"LOCAL.{signal}.definition",
                    value=initial_value,
                    time=1,
                )
            )
        session.flush()
        for model, signal, _initial_value, received_value in cases:
            update_existing_scada_points(
                session,
                model,
                [
                    {
                        "pnt_no": 1,
                        "name": f"REMOTE.{signal}.renamed",
                        "value": received_value,
                    },
                    {
                        "pnt_no": 999,
                        "name": f"REMOTE.{signal}.unknown",
                        "value": received_value,
                    },
                ],
                42,
                log_wall_time=100,
                source="test.receiver",
            )

    database.write(exercise)

    with database.session() as session:
        for model, signal, _initial_value, received_value in cases:
            point = session.get(model, 1)
            assert (point.name, point.value, point.time) == (
                f"LOCAL.{signal}.definition",
                received_value,
                42,
            )
            assert session.get(model, 999) is None
        warnings = (
            session.query(OperatorLog)
            .filter_by(log_type=2)
            .order_by(OperatorLog.id)
            .all()
        )
        payloads = [json.loads(row.log_info) for row in warnings]
        assert [(row["signal"], row["pnt_no"]) for row in payloads] == [
            ("YC", 999),
            ("YX", 999),
            ("YT", 999),
            ("YK", 999),
        ]
        assert all(row["event"] == "unknown_scada_point" for row in payloads)
        assert all(row["source"] == "test.receiver" for row in payloads)
        assert all(row["simu_time"] == 42 for row in payloads)
    assert [
        signal
        for signal in ("YC", "YX", "YT", "YK")
        if f"收到未知 {signal} 点号" in caplog.text
    ] == ["YC", "YX", "YT", "YK"]
