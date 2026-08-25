from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect, text

from power_operator.database import Database, initialize_database
from power_operator.models import OperatorHistory, OperatorLog


EXPECTED_TABLES = {
    "scada_rtu",
    "scada_yc",
    "scada_yx",
    "scada_yk",
    "scada_yt",
    "dev_diesal_gen",
    "dev_wind_gen",
    "dev_solar_gen",
    "dev_estore",
    "dev_load",
    "operator_log",
    "operator_history",
    "operator_control",
    "scada_yc_his",
    "scada_yt_his",
    "scada_yx_his",
    "scada_yk_his",
}


def test_initialize_database_builds_required_schema(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)

    assert EXPECTED_TABLES <= set(inspect(db.engine).get_table_names())
    assert "curve_def" not in set(inspect(db.engine).get_table_names())
    columns = {column["name"] for column in inspect(db.engine).get_columns("dev_estore")}
    assert "battery_capacity" in columns
    for table_name in (
        "dev_diesal_gen",
        "dev_wind_gen",
        "dev_solar_gen",
        "dev_estore",
    ):
        assert "control_mode" in {
            column["name"]
            for column in inspect(db.engine).get_columns(table_name)
        }
    wind_columns = {
        column["name"] for column in inspect(db.engine).get_columns("dev_wind_gen")
    }
    assert "angle_yaw_curr" not in wind_columns
    assert "soc_init" not in columns
    control_columns = {
        column["name"] for column in inspect(db.engine).get_columns("operator_control")
    }
    assert {
        "oper_status",
        "control_status",
        "io_connect_enabled",
        "data_period",
        "oper_period",
        "data_time_curr",
        "oper_time_curr",
        "source_run_seq",
        "source_time_start",
        "source_runtime_ready",
    } <= control_columns
    history_columns = {
        column["name"] for column in inspect(db.engine).get_columns("scada_yc_his")
    }
    assert "time" in history_columns
    assert "simu_time" not in history_columns
    operator_history_columns = {
        column["name"]
        for column in inspect(db.engine).get_columns("operator_history")
    }
    assert "wind_speed" in operator_history_columns
    assert "simu_wind" not in operator_history_columns
    assert "solar_radiation" in operator_history_columns
    assert "simu_sloar" not in operator_history_columns
    assert "amb_temp" in operator_history_columns
    assert [
        column["name"]
        for column in inspect(db.engine).get_columns("operator_history")
    ][:4] == ["simu_time", "wind_speed", "solar_radiation", "amb_temp"]

    with db.engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar_one().lower() == "wal"
        assert connection.execute(text("PRAGMA busy_timeout")).scalar_one() >= 5000
    indexes = {index["name"] for index in inspect(db.engine).get_indexes("scada_yc_his")}
    assert "ix_scada_yc_his_pnt_time" in indexes


def test_short_retried_transactions_allow_concurrent_writers(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)

    def write_log(index: int) -> None:
        db.write(
            lambda session: session.add(
                OperatorLog(
                    log_time=index,
                    simu_time=index,
                    log_type=1,
                    log_info=f"log-{index}",
                )
            )
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write_log, range(80)))

    with db.session() as session:
        assert session.query(OperatorLog).count() == 80


def test_fresh_wind_schema_excludes_removed_yaw_field(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    columns = [
        column["name"]
        for column in inspect(database.engine).get_columns("dev_wind_gen")
    ]
    assert "angle_yaw_curr" not in columns
    assert columns == [
        "id",
        "name",
        "p_rated",
        "wind_in",
        "wind_rated",
        "wind_cut",
        "status",
        "p_max_curr",
        "angle_pitch_curr",
        "p_curr",
        "p_set",
        "control_mode",
    ]


def test_initialize_database_adds_closed_loop_mode_to_legacy_device_rows(tmp_path):
    path = tmp_path / "ems.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dev_diesal_gen (
            id INTEGER NOT NULL PRIMARY KEY,
            name TEXT NOT NULL,
            p_rated FLOAT NOT NULL,
            p_max FLOAT NOT NULL,
            p_min FLOAT NOT NULL,
            p_coeff FLOAT NOT NULL,
            status INTEGER NOT NULL,
            p_curr FLOAT NOT NULL,
            p_set FLOAT NOT NULL
        );
        INSERT INTO dev_diesal_gen VALUES
            (1001, '旧柴发', 100, 100, 0, 0.25, 1, 20, 30);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    with database.engine.connect() as sql:
        assert sql.execute(
            text("SELECT control_mode FROM dev_diesal_gen WHERE id=1001")
        ).scalar_one() == 1
        assert tuple(
            sql.execute(
                text(
                    "SELECT name, value, time FROM scada_yx "
                    "WHERE pnt_no=100102"
                )
            ).one()
        ) == ("旧柴发.控制模式", 1, 0)


def test_initialize_database_restores_missing_declared_index(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    with db.engine.begin() as connection:
        connection.execute(text("DROP INDEX ix_scada_yc_his_pnt_time"))

    initialize_database(db)

    indexes = {index["name"] for index in inspect(db.engine).get_indexes("scada_yc_his")}
    assert "ix_scada_yc_his_pnt_time" in indexes


def test_initialize_database_migrates_legacy_control_and_history_schema(tmp_path):
    path = tmp_path / "ems.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE operator_control (
            id INTEGER PRIMARY KEY,
            oper_status INTEGER NOT NULL DEFAULT 0,
            oper_period INTEGER NOT NULL DEFAULT 1,
            oper_time_curr INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO operator_control(id, oper_status, oper_period, oper_time_curr)
        VALUES (1, 0, 3, 12);
        CREATE TABLE scada_yc_his (
            simu_time INTEGER NOT NULL,
            pnt_no INTEGER NOT NULL,
            value FLOAT NOT NULL,
            PRIMARY KEY (simu_time, pnt_no)
        );
        INSERT INTO scada_yc_his(simu_time, pnt_no, value) VALUES (12, 7, 1.5);
        CREATE TABLE curve_def (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        INSERT INTO curve_def(id, name) VALUES (1, 'legacy');
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    assert [
        column["name"] for column in inspect(database.engine).get_columns("operator_control")
    ] == [
        "id",
        "oper_status",
        "control_status",
        "io_connect_enabled",
        "data_period",
        "oper_period",
        "data_time_curr",
        "oper_time_curr",
        "source_run_seq",
        "source_time_start",
        "source_runtime_ready",
    ]
    with database.engine.connect() as sql:
        row = sql.execute(
            text(
                "SELECT control_status, io_connect_enabled, data_period, oper_period, "
                "data_time_curr, oper_time_curr FROM operator_control WHERE id=1"
            )
        ).one()
        history = sql.execute(text("SELECT time, pnt_no, value FROM scada_yc_his")).one()
    assert row == (0, 1, 1, 3, 0, 12)
    assert history == (12, 7, 1.5)
    assert "curve_def" not in inspect(database.engine).get_table_names()


def test_initialize_database_migrates_operator_log_info_to_unbounded_text(tmp_path):
    path = tmp_path / "ems.db"
    import sqlite3

    long_info = "决策输入输出" * 400
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE operator_log (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            log_time INTEGER NOT NULL,
            simu_time INTEGER NOT NULL,
            log_type INTEGER NOT NULL DEFAULT 0,
            log_info VARCHAR(1024) NOT NULL DEFAULT ''
        );
        CREATE INDEX ix_operator_log_log_time ON operator_log(log_time);
        CREATE INDEX ix_operator_log_simu_time ON operator_log(simu_time);
        """
    )
    connection.execute(
        "INSERT INTO operator_log(id, log_time, simu_time, log_type, log_info) "
        "VALUES (7, 100, 90, 4, ?)",
        (long_info,),
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    columns = {
        column["name"]: str(column["type"]).upper()
        for column in inspect(database.engine).get_columns("operator_log")
    }
    assert columns["log_info"] == "TEXT"
    indexes = {index["name"] for index in inspect(database.engine).get_indexes("operator_log")}
    assert {"ix_operator_log_log_time", "ix_operator_log_simu_time"} <= indexes
    with database.session() as session:
        row = session.get(OperatorLog, 7)
        assert row.log_time == 100
        assert row.simu_time == 90
        assert row.log_type == 4
        assert row.log_info == long_info


def test_initialize_database_renames_legacy_environment_fields_without_losing_history(
    tmp_path,
):
    path = tmp_path / "ems.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE operator_history (
            simu_time INTEGER NOT NULL PRIMARY KEY,
            simu_wind FLOAT NOT NULL DEFAULT 0,
            simu_sloar FLOAT NOT NULL DEFAULT 0,
            diesal_power_curr_sum FLOAT NOT NULL DEFAULT 0,
            diesal_power_set_sum FLOAT NOT NULL DEFAULT 0,
            diesal_curr_sum FLOAT NOT NULL DEFAULT 0,
            wind_power_curr_sum FLOAT NOT NULL DEFAULT 0,
            wind_power_max_sum FLOAT NOT NULL DEFAULT 0,
            wind_power_set_sum FLOAT NOT NULL DEFAULT 0,
            solar_power_curr_sum FLOAT NOT NULL DEFAULT 0,
            solar_power_max_sum FLOAT NOT NULL DEFAULT 0,
            solar_power_set_sum FLOAT NOT NULL DEFAULT 0,
            load_power_curr_sum FLOAT NOT NULL DEFAULT 0,
            estore_power_curr_sum FLOAT NOT NULL DEFAULT 0,
            estore_power_set_sum FLOAT NOT NULL DEFAULT 0,
            estore_power_soc_sum FLOAT NOT NULL DEFAULT 0
        );
        INSERT INTO operator_history (
            simu_time, simu_wind, simu_sloar, load_power_curr_sum
        ) VALUES (3661, 8.765, 650.25, 123.5);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    columns = {
        column["name"]
        for column in inspect(database.engine).get_columns("operator_history")
    }
    assert "wind_speed" in columns
    assert "simu_wind" not in columns
    assert "solar_radiation" in columns
    assert "simu_sloar" not in columns
    assert "amb_temp" in columns
    assert [
        column["name"]
        for column in inspect(database.engine).get_columns("operator_history")
    ][:4] == ["simu_time", "wind_speed", "solar_radiation", "amb_temp"]
    with database.session() as session:
        history = session.get(OperatorHistory, 3661)
        assert history.wind_speed == 8.765
        assert history.solar_radiation == 650.25
        assert history.amb_temp == 0.0
        assert history.load_power_curr_sum == 123.5


def test_initialize_database_drops_legacy_soc_init_without_losing_storage(tmp_path):
    path = tmp_path / "ems.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dev_estore (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            status INTEGER NOT NULL,
            p_charge_max FLOAT NOT NULL,
            p_charge_eff FLOAT NOT NULL,
            p_discharge_max FLOAT NOT NULL,
            p_discharge_eff FLOAT NOT NULL,
            p_curr FLOAT NOT NULL,
            p_set FLOAT NOT NULL,
            battery_capacity FLOAT NOT NULL,
            soc_curr FLOAT NOT NULL,
            soc_max FLOAT NOT NULL,
            soc_min FLOAT NOT NULL,
            soc_init FLOAT NOT NULL
        );
        INSERT INTO dev_estore VALUES (
            5001, '储能单元1', 1, 50, 0.95, 60, 0.96,
            5, 6, 200, 0.55, 0.9, 0.1, 0.5
        );
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    columns = [
        column["name"] for column in inspect(database.engine).get_columns("dev_estore")
    ]
    assert "soc_init" not in columns
    with database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, name, status, battery_capacity, soc_curr, soc_max, soc_min "
                "FROM dev_estore WHERE id=5001"
            )
        ).one()
    assert row == (5001, "储能单元1", 1, 200.0, 0.55, 0.9, 0.1)


def test_initialize_database_drops_legacy_wind_yaw_without_losing_device(tmp_path):
    path = tmp_path / "ems.db"
    import sqlite3

    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE dev_wind_gen (
            id INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            p_rated FLOAT NOT NULL,
            wind_in FLOAT NOT NULL,
            wind_rated FLOAT NOT NULL,
            wind_cut FLOAT NOT NULL,
            status INTEGER NOT NULL,
            p_max_curr FLOAT NOT NULL,
            angle_yaw_curr FLOAT NOT NULL,
            angle_pitch_curr FLOAT NOT NULL,
            p_curr FLOAT NOT NULL,
            p_set FLOAT NOT NULL
        );
        INSERT INTO dev_wind_gen VALUES (
            2001, '风力发电机1', 50, 3, 12, 25,
            1, 42.5, 7.75, 2.25, 40.5, 41.5
        );
        CREATE TABLE scada_yc (
            pnt_no INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            value FLOAT NOT NULL,
            time INTEGER NOT NULL
        );
        INSERT INTO scada_yc VALUES
            (200101, '风力发电机1.当前有功', 40.5, 100),
            (200103, '风力发电机1.当前偏航角', 7.75, 100),
            (200203, 'dev_wind_gen.2.angle_yaw_curr', 8.25, 100),
            (100102, '柴油发电机1.本步柴油消耗', 0.25, 100),
            (200102, '风力发电机1.理论最大有功', 42.5, 100),
            (200105, '风力发电机1.有功功率设定值', 41.5, 100),
            (300102, 'dev_solar_gen.3.p_max_curr', 30.5, 100);
        CREATE TABLE scada_yc_his (
            time INTEGER NOT NULL,
            pnt_no INTEGER NOT NULL,
            value FLOAT NOT NULL,
            PRIMARY KEY (time, pnt_no)
        );
        INSERT INTO scada_yc_his VALUES
            (100, 200101, 40.5),
            (100, 200103, 7.75),
            (100, 200203, 8.25),
            (100, 100102, 0.25),
            (100, 200102, 42.5),
            (100, 200105, 41.5),
            (100, 300102, 30.5);
        CREATE TABLE scada_yt (
            pnt_no INTEGER NOT NULL PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            value FLOAT NOT NULL,
            time INTEGER NOT NULL
        );
        INSERT INTO scada_yt VALUES
            (200102, '风力发电机1.偏航角设定', 6.5, 100),
            (200104, '风力发电机1.有功设定', 41.5, 100),
            (200202, 'dev_wind_gen.2.angle_yaw_setpoint', 7.5, 100),
            (200204, '风力发电机2.桨距角设定', 2.5, 100),
            (200302, 'dev_wind_gen.3.angle_pitch_setpoint', 3.5, 100);
        CREATE TABLE scada_yt_his (
            time INTEGER NOT NULL,
            pnt_no INTEGER NOT NULL,
            value FLOAT NOT NULL,
            PRIMARY KEY (time, pnt_no)
        );
        INSERT INTO scada_yt_his VALUES
            (100, 200102, 6.5),
            (100, 200104, 41.5),
            (100, 200202, 7.5),
            (100, 200204, 2.5),
            (100, 200302, 3.5);
        """
    )
    connection.commit()
    connection.close()

    database = Database(path)
    initialize_database(database)

    columns = [
        column["name"]
        for column in inspect(database.engine).get_columns("dev_wind_gen")
    ]
    assert columns == [
        "id",
        "name",
        "p_rated",
        "wind_in",
        "wind_rated",
        "wind_cut",
        "status",
        "p_max_curr",
        "angle_pitch_curr",
        "p_curr",
        "p_set",
        "control_mode",
    ]
    with database.engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, name, p_rated, wind_in, wind_rated, wind_cut, "
                "status, p_max_curr, angle_pitch_curr, p_curr, p_set "
                "FROM dev_wind_gen WHERE id=2001"
            )
        ).one()
    assert row == (
        2001,
        "风力发电机1",
        50.0,
        3.0,
        12.0,
        25.0,
        1,
        42.5,
        2.25,
        40.5,
        41.5,
    )
    with database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT pnt_no, name FROM scada_yc ORDER BY pnt_no")
        ).all() == [(200101, "风力发电机1.当前有功")]
        assert connection.execute(
            text("SELECT time, pnt_no, value FROM scada_yc_his ORDER BY pnt_no")
        ).all() == [(100, 200101, 40.5)]
        assert connection.execute(
            text("SELECT pnt_no, name, value, time FROM scada_yt ORDER BY pnt_no")
        ).all() == [(200104, "风力发电机1.有功设定", 41.5, 100)]
        assert connection.execute(
            text("SELECT time, pnt_no, value FROM scada_yt_his ORDER BY pnt_no")
        ).all() == [(100, 200104, 41.5)]

    initialize_database(database)
    with database.engine.connect() as connection:
        assert connection.execute(
            text("SELECT pnt_no FROM scada_yt ORDER BY pnt_no")
        ).scalars().all() == [200104]
        assert connection.execute(
            text("SELECT pnt_no FROM scada_yt_his ORDER BY pnt_no")
        ).scalars().all() == [200104]
