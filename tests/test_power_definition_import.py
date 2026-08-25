from __future__ import annotations

import hashlib
import sqlite3

import pytest
from sqlalchemy import inspect, select

import power_operator.definition_import as definition_import_module
from power_operator.database import Database, initialize_database
from power_operator.definition_import import DEFINITION_MODELS, import_power_definitions
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYk,
    ScadaYt,
    ScadaYx,
)


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_source(path) -> None:
    source = Database(path)
    initialize_database(source)

    def seed(session):
        session.add_all(
            [
                ScadaRtu(id=7, ip="127.0.0.1", port=9200, status=1, refresh_time=123),
                ScadaYc(pnt_no=1, name="simu.wind", value=8.25, time=12),
                ScadaYx(pnt_no=2, name="dev_load.1.status", value=1, time=12),
                ScadaYt(pnt_no=3, name="dev_wind_gen.1.p_set", value=22.5, time=12),
                ScadaYk(pnt_no=4, name="dev_wind_gen.1.status", value=1, time=0),
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                    p_curr=20,
                    p_set=20,
                ),
                DevWindGen(
                    id=1,
                    name="W1",
                    p_rated=80,
                    wind_in=3,
                    wind_rated=11,
                    wind_cut=25,
                    status=1,
                    p_max_curr=50,
                    angle_pitch_curr=2,
                    p_curr=45,
                    p_set=40,
                ),
                DevSolarGen(
                    id=1,
                    name="S1",
                    p_rated=60,
                    status=1,
                    p_max_curr=30,
                    p_curr=25,
                    p_set=20,
                ),
                DevEstore(
                    id=1,
                    name="E1",
                    status=1,
                    p_charge_max=20,
                    p_charge_eff=0.95,
                    p_discharge_max=20,
                    p_discharge_eff=0.95,
                    p_curr=0,
                    p_set=0,
                    battery_capacity=100,
                    soc_curr=0.5,
                    soc_max=0.9,
                    soc_min=0.1,
                ),
                DevLoad(id=1, name="L1", status=1, p_curr=100),
                OperatorLog(log_time=1, simu_time=1, log_type=1, log_info="不得复制"),
            ]
        )

    source.write(seed)
    source.dispose()
    connection = sqlite3.connect(path)
    connection.execute("ALTER TABLE scada_rtu ADD COLUMN conn_num INTEGER DEFAULT 0")
    connection.execute("UPDATE scada_rtu SET conn_num=9")
    connection.execute(
        "ALTER TABLE dev_wind_gen ADD COLUMN angle_yaw_curr FLOAT DEFAULT 0"
    )
    connection.execute("UPDATE dev_wind_gen SET angle_yaw_curr=1")
    connection.execute(
        "INSERT INTO scada_yc(pnt_no, name, value, time) VALUES (?, ?, ?, ?)",
        (200103, "风力发电机1.当前偏航角", 1.0, 12),
    )
    connection.executemany(
        "INSERT INTO scada_yc(pnt_no, name, value, time) VALUES (?, ?, ?, ?)",
        [
            (100102, "柴油发电机1.本步柴油消耗", 0.25, 12),
            (200102, "风力发电机1.理论最大有功", 50.0, 12),
            (200105, "风力发电机1.有功功率设定值", 40.0, 12),
        ],
    )
    connection.execute(
        "INSERT INTO scada_yt(pnt_no, name, value, time) VALUES (?, ?, ?, ?)",
        (200102, "风力发电机1.偏航角设定", 6.0, 12),
    )
    connection.execute(
        "INSERT INTO scada_yt(pnt_no, name, value, time) VALUES (?, ?, ?, ?)",
        (200103, "风力发电机1.桨距角设定", 2.0, 12),
    )
    connection.commit()
    connection.close()


def test_import_power_definitions_copies_only_target_columns_and_keeps_source_read_only(
    tmp_path,
):
    source_path = tmp_path / "power.db"
    target_path = tmp_path / "ems.db"
    _build_source(source_path)
    source_hash = _sha256(source_path)

    report = import_power_definitions(source_path, target_path, replace=True)

    assert _sha256(source_path) == source_hash
    assert report.source_path == source_path.resolve()
    assert report.target_path == target_path.resolve()
    assert report.source_sha256_before == source_hash
    assert report.source_sha256_after == source_hash
    assert report.source_unchanged is True
    assert report.tables["scada_rtu"].source_count == 1
    assert report.tables["scada_rtu"].target_count == 1
    assert report.tables["scada_yt"].source_count == 1
    assert report.tables["scada_yt"].target_count == 1
    assert report.tables["scada_rtu"].ignored_source_columns == ("conn_num",)
    assert report.tables["dev_wind_gen"].ignored_source_columns == (
        "angle_yaw_curr",
    )
    target = Database(target_path)
    assert "conn_num" not in {
        column["name"] for column in inspect(target.engine).get_columns("scada_rtu")
    }
    with target.session() as session:
        assert session.get(ScadaRtu, 7).ip == "127.0.0.1"
        assert session.get(ScadaYc, 1).value == 8.25
        assert session.get(ScadaYc, 200103) is None
        assert session.get(ScadaYc, 100102) is None
        assert session.get(ScadaYc, 200102) is None
        assert session.get(ScadaYc, 200105) is None
        assert session.get(ScadaYt, 3).value == 22.5
        assert session.get(ScadaYt, 200102) is None
        assert session.get(ScadaYt, 200103) is None
        assert session.get(DevDiesalGen, 1).p_coeff == 0.25
        assert session.get(DevEstore, 1).battery_capacity == 100
        assert session.query(OperatorLog).count() == 0
        for model in DEFINITION_MODELS:
            assert session.scalars(select(model)).all()

    source = Database(source_path)
    with source.session() as session:
        assert session.get(ScadaYc, 100102).name == "柴油发电机1.本步柴油消耗"
        assert session.get(ScadaYc, 200102).name == "风力发电机1.理论最大有功"
        assert session.get(ScadaYc, 200105).name == "风力发电机1.有功功率设定值"
        assert session.get(ScadaYt, 200102).name == "风力发电机1.偏航角设定"
        assert session.get(ScadaYt, 200103).name == "风力发电机1.桨距角设定"
    source.dispose()


def test_import_refuses_overwrite_without_replace_and_same_source_target(tmp_path):
    source_path = tmp_path / "power.db"
    target_path = tmp_path / "ems.db"
    _build_source(source_path)
    target = Database(target_path)
    initialize_database(target)
    target.write(lambda session: session.add(DevLoad(id=99, name="existing", status=0, p_curr=0)))

    with pytest.raises(ValueError, match="--replace"):
        import_power_definitions(source_path, target_path, replace=False)
    with target.session() as session:
        assert session.get(DevLoad, 99).name == "existing"

    with pytest.raises(ValueError, match="不能是同一个文件"):
        import_power_definitions(source_path, source_path, replace=True)


def test_import_missing_required_column_fails_before_target_changes(tmp_path):
    source_path = tmp_path / "broken_power.db"
    target_path = tmp_path / "ems.db"
    _build_source(source_path)
    connection = sqlite3.connect(source_path)
    connection.executescript(
        """
        ALTER TABLE dev_load RENAME TO dev_load_old;
        CREATE TABLE dev_load(id INTEGER PRIMARY KEY, name TEXT, status INTEGER);
        INSERT INTO dev_load(id, name, status)
        SELECT id, name, status FROM dev_load_old;
        DROP TABLE dev_load_old;
        """
    )
    connection.commit()
    connection.close()
    target = Database(target_path)
    initialize_database(target)
    target.write(lambda session: session.add(DevLoad(id=99, name="existing", status=0, p_curr=3)))

    with pytest.raises(ValueError, match=r"dev_load.*p_curr"):
        import_power_definitions(source_path, target_path, replace=True)

    with target.session() as session:
        existing = session.get(DevLoad, 99)
        assert existing is not None
        assert existing.p_curr == 3


def test_import_legacy_devices_without_control_mode_defaults_to_closed_loop(
    tmp_path,
):
    source_path = tmp_path / "legacy_power.db"
    target_path = tmp_path / "ems.db"
    _build_source(source_path)
    connection = sqlite3.connect(source_path)
    try:
        for table_name in (
            "dev_diesal_gen",
            "dev_wind_gen",
            "dev_solar_gen",
            "dev_estore",
        ):
            connection.execute(
                f'ALTER TABLE "{table_name}" DROP COLUMN control_mode'
            )
        connection.commit()
    finally:
        connection.close()

    import_power_definitions(source_path, target_path, replace=True)

    target = Database(target_path)
    try:
        with target.session() as session:
            for model in (
                DevDiesalGen,
                DevWindGen,
                DevSolarGen,
                DevEstore,
            ):
                assert session.scalars(select(model)).one().control_mode == 1
    finally:
        target.dispose()


def test_import_succeeds_when_windows_sharing_prevents_direct_source_hash(
    tmp_path,
    monkeypatch,
):
    source_path = tmp_path / "power.db"
    target_path = tmp_path / "ems.db"
    _build_source(source_path)
    monkeypatch.setattr(definition_import_module, "_try_file_sha256", lambda _path: None)

    report = import_power_definitions(source_path, target_path, replace=True)

    assert report.source_sha256_before is None
    assert report.source_sha256_after is None
    assert report.source_unchanged is None
    target = Database(target_path)
    with target.session() as session:
        assert session.get(ScadaRtu, 7).port == 9200
