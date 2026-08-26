from __future__ import annotations

import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from sqlalchemy import Engine, event, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from .command_points import migrate_legacy_generated_command_points
from .models import Base, OperatorControl
from .retired_measurements import (
    is_retired_operator_yc_measurement_name,
    is_retired_wind_angle_setpoint_name,
)

T = TypeVar("T")


class Database:
    """A worker-local SQLAlchemy facade around the shared SQLite file.

    WAL and busy_timeout are SQLite's cross-process/cross-thread concurrency
    mechanisms. Each long-lived process or worker thread creates its own
    ``Database``/engine instance and never shares a ``Session``.
    """

    def __init__(self, path: str | Path = "ems.db", busy_timeout_ms: int = 10_000):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.busy_timeout_ms = busy_timeout_ms
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            connect_args={"timeout": busy_timeout_ms / 1000, "check_same_thread": False},
            pool_pre_ping=True,
        )
        self._session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
            autoflush=False,
        )
        self._install_sqlite_pragmas(self.engine)

    def _install_sqlite_pragmas(self, engine: Engine) -> None:
        busy_timeout_ms = self.busy_timeout_ms

        @event.listens_for(engine, "connect")
        def configure_connection(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def write(self, operation: Callable[[Session], T], retries: int = 7) -> T:
        """Run a short write transaction and retry only lock/busy failures."""

        for attempt in range(retries):
            session = self._session_factory()
            try:
                result = operation(session)
                session.commit()
                return result
            except OperationalError as exc:
                session.rollback()
                message = str(exc).lower()
                if attempt + 1 >= retries or not ("locked" in message or "busy" in message):
                    raise
                time.sleep(min(0.025 * 2**attempt, 0.8) + random.uniform(0, 0.02))
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        raise RuntimeError("unreachable")

    def dispose(self) -> None:
        self.engine.dispose()


def initialize_database(database: Database) -> None:
    migrated_control_mode_tables = _migrate_existing_schema(database)
    Base.metadata.create_all(database.engine)
    _enforce_binary_operator_status(database)
    if migrated_control_mode_tables:
        with database.engine.begin() as connection:
            for table_name in migrated_control_mode_tables:
                connection.exec_driver_sql(
                    'INSERT OR IGNORE INTO "scada_yx"'
                    '("pnt_no", "name", "value", "time") '
                    f'SELECT "id" * 100 + 2, "name" || \'.控制模式\', '
                    f'"control_mode", 0 FROM "{table_name}"'
                )
    # ``create_all`` does not add newly introduced indexes to tables that
    # already existed. Explicit checkfirst calls make init_db an idempotent
    # lightweight schema upgrade for ORM-declared indexes.
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(database.engine, checkfirst=True)

    def seed(session: Session) -> None:
        migrate_legacy_generated_command_points(session)
        if session.get(OperatorControl, 1) is None:
            session.add(
                OperatorControl(
                    id=1,
                    oper_status=0,
                    control_status=0,
                    io_connect_enabled=1,
                    data_period=1,
                    oper_period=1,
                    data_time_curr=0,
                    oper_time_curr=0,
                    source_run_seq=0,
                    source_time_start=0,
                    source_runtime_ready=0,
                )
            )
    database.write(seed)


def _enforce_binary_operator_status(database: Database) -> None:
    """Remove the retired paused value and reject non-binary status writes."""

    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            'UPDATE "operator_control" SET "oper_status" = 0 '
            'WHERE "oper_status" NOT IN (0, 1) OR "oper_status" IS NULL'
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS operator_control_oper_status_insert
            BEFORE INSERT ON operator_control
            WHEN NEW.oper_status NOT IN (0, 1)
            BEGIN
                SELECT RAISE(ABORT, 'oper_status only accepts 0 or 1');
            END
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER IF NOT EXISTS operator_control_oper_status_update
            BEFORE UPDATE OF oper_status ON operator_control
            WHEN NEW.oper_status NOT IN (0, 1)
            BEGIN
                SELECT RAISE(ABORT, 'oper_status only accepts 0 or 1');
            END
            """
        )


def _migrate_existing_schema(database: Database) -> tuple[str, ...]:
    """Apply small, idempotent SQLite migrations needed by existing ems.db files."""

    schema = inspect(database.engine)
    tables = set(schema.get_table_names())
    if "curve_def" in tables:
        with database.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE "curve_def"')
        tables.remove("curve_def")
    if "scada_yc" in tables:
        with database.engine.begin() as connection:
            retired_point_numbers = [
                int(row.pnt_no)
                for row in connection.exec_driver_sql(
                    'SELECT "pnt_no", "name" FROM "scada_yc"'
                ).all()
                if is_retired_operator_yc_measurement_name(row.name)
            ]
            for pnt_no in retired_point_numbers:
                if "scada_yc_his" in tables:
                    connection.exec_driver_sql(
                        'DELETE FROM "scada_yc_his" WHERE "pnt_no" = ?',
                        (pnt_no,),
                    )
                connection.exec_driver_sql(
                    'DELETE FROM "scada_yc" WHERE "pnt_no" = ?',
                    (pnt_no,),
                )
    if "scada_yt" in tables:
        with database.engine.begin() as connection:
            retired_point_numbers = [
                int(row.pnt_no)
                for row in connection.exec_driver_sql(
                    'SELECT "pnt_no", "name" FROM "scada_yt"'
                ).all()
                if is_retired_wind_angle_setpoint_name(row.name)
            ]
            for pnt_no in retired_point_numbers:
                if "scada_yt_his" in tables:
                    connection.exec_driver_sql(
                        'DELETE FROM "scada_yt_his" WHERE "pnt_no" = ?',
                        (pnt_no,),
                    )
                connection.exec_driver_sql(
                    'DELETE FROM "scada_yt" WHERE "pnt_no" = ?',
                    (pnt_no,),
                )
    if "dev_wind_gen" in tables:
        wind_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns("dev_wind_gen")
        }
        if "angle_yaw_curr" in wind_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER TABLE "dev_wind_gen" DROP COLUMN "angle_yaw_curr"'
                )
    if "dev_estore" in tables:
        estore_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns("dev_estore")
        }
        if "soc_init" in estore_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER TABLE "dev_estore" DROP COLUMN "soc_init"'
                )
    migrated_control_mode_tables: list[str] = []
    for table_name in (
        "dev_diesal_gen",
        "dev_wind_gen",
        "dev_solar_gen",
        "dev_estore",
    ):
        if table_name not in tables:
            continue
        device_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns(table_name)
        }
        if "control_mode" not in device_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "control_mode" '
                    "INTEGER NOT NULL DEFAULT 1"
                )
            migrated_control_mode_tables.append(table_name)
    if "operator_control" in tables:
        columns = [column["name"] for column in schema.get_columns("operator_control")]
        additions = {
            "control_status": "INTEGER NOT NULL DEFAULT 0",
            "io_connect_enabled": "INTEGER NOT NULL DEFAULT 1",
            "data_period": "INTEGER NOT NULL DEFAULT 1",
            "data_time_curr": "INTEGER NOT NULL DEFAULT 0",
            "source_run_seq": "INTEGER NOT NULL DEFAULT 0",
            "source_time_start": "INTEGER NOT NULL DEFAULT 0",
            "source_runtime_ready": "INTEGER NOT NULL DEFAULT 0",
        }
        with database.engine.begin() as connection:
            for name, definition in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(
                        f'ALTER TABLE "operator_control" ADD COLUMN "{name}" {definition}'
                    )
            connection.exec_driver_sql(
                'UPDATE "operator_control" SET "oper_status" = 0 '
                'WHERE "oper_status" NOT IN (0, 1) OR "oper_status" IS NULL'
            )
        expected_order = [
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
        columns = [
            column["name"]
            for column in inspect(database.engine).get_columns("operator_control")
        ]
        # Older databases had oper_period/oper_time_curr before the newly added
        # fields. Rebuild only the known contract (never discard unknown user
        # columns) so schema inspection matches the published table order.
        if set(columns) == set(expected_order) and columns != expected_order:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    """
                    CREATE TABLE operator_control__migrated (
                        id INTEGER NOT NULL PRIMARY KEY,
                        oper_status INTEGER NOT NULL DEFAULT 0
                            CHECK (oper_status IN (0, 1)),
                        control_status INTEGER NOT NULL DEFAULT 0,
                        io_connect_enabled INTEGER NOT NULL DEFAULT 1,
                        data_period INTEGER NOT NULL DEFAULT 1,
                        oper_period INTEGER NOT NULL DEFAULT 1,
                        data_time_curr INTEGER NOT NULL DEFAULT 0,
                        oper_time_curr INTEGER NOT NULL DEFAULT 0,
                        source_run_seq INTEGER NOT NULL DEFAULT 0,
                        source_time_start INTEGER NOT NULL DEFAULT 0,
                        source_runtime_ready INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    INSERT INTO operator_control__migrated (
                        id, oper_status, control_status, io_connect_enabled, data_period,
                        oper_period, data_time_curr, oper_time_curr, source_run_seq,
                        source_time_start, source_runtime_ready
                    )
                        SELECT id, oper_status, control_status, io_connect_enabled, data_period,
                           oper_period, data_time_curr, oper_time_curr, source_run_seq,
                           source_time_start, source_runtime_ready
                    FROM operator_control
                    """
                )
                connection.exec_driver_sql("DROP TABLE operator_control")
                connection.exec_driver_sql(
                    "ALTER TABLE operator_control__migrated RENAME TO operator_control"
                )

    if "operator_history" in tables:
        history_columns = {
            column["name"]
            for column in inspect(database.engine).get_columns("operator_history")
        }
        if "simu_wind" in history_columns and "wind_speed" not in history_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER TABLE "operator_history" '
                    'RENAME COLUMN "simu_wind" TO "wind_speed"'
                )
        if "simu_sloar" in history_columns and "solar_radiation" not in history_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER TABLE "operator_history" '
                    'RENAME COLUMN "simu_sloar" TO "solar_radiation"'
                )
        if "amb_temp" not in history_columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    'ALTER TABLE "operator_history" '
                    'ADD COLUMN "amb_temp" FLOAT NOT NULL DEFAULT 0'
                )
        expected_history_columns = [
            "simu_time",
            "wind_speed",
            "solar_radiation",
            "amb_temp",
            "diesal_power_curr_sum",
            "diesal_power_set_sum",
            "diesal_curr_sum",
            "wind_power_curr_sum",
            "wind_power_max_sum",
            "wind_power_set_sum",
            "solar_power_curr_sum",
            "solar_power_max_sum",
            "solar_power_set_sum",
            "load_power_curr_sum",
            "estore_power_curr_sum",
            "estore_power_set_sum",
            "estore_power_soc_sum",
        ]
        history_column_order = [
            column["name"]
            for column in inspect(database.engine).get_columns("operator_history")
        ]
        if (
            set(history_column_order) == set(expected_history_columns)
            and history_column_order != expected_history_columns
        ):
            quoted_columns = ", ".join(
                f'"{name}"' for name in expected_history_columns
            )
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    "DROP TABLE IF EXISTS operator_history__migrated"
                )
                connection.exec_driver_sql(
                    """
                    CREATE TABLE operator_history__migrated (
                        simu_time INTEGER NOT NULL PRIMARY KEY,
                        wind_speed FLOAT NOT NULL,
                        solar_radiation FLOAT NOT NULL,
                        amb_temp FLOAT NOT NULL,
                        diesal_power_curr_sum FLOAT NOT NULL,
                        diesal_power_set_sum FLOAT NOT NULL,
                        diesal_curr_sum FLOAT NOT NULL,
                        wind_power_curr_sum FLOAT NOT NULL,
                        wind_power_max_sum FLOAT NOT NULL,
                        wind_power_set_sum FLOAT NOT NULL,
                        solar_power_curr_sum FLOAT NOT NULL,
                        solar_power_max_sum FLOAT NOT NULL,
                        solar_power_set_sum FLOAT NOT NULL,
                        load_power_curr_sum FLOAT NOT NULL,
                        estore_power_curr_sum FLOAT NOT NULL,
                        estore_power_set_sum FLOAT NOT NULL,
                        estore_power_soc_sum FLOAT NOT NULL
                    )
                    """
                )
                connection.exec_driver_sql(
                    f"INSERT INTO operator_history__migrated ({quoted_columns}) "
                    f"SELECT {quoted_columns} FROM operator_history"
                )
                connection.exec_driver_sql("DROP TABLE operator_history")
                connection.exec_driver_sql(
                    "ALTER TABLE operator_history__migrated "
                    "RENAME TO operator_history"
                )

    if "operator_log" in tables:
        log_columns = inspect(database.engine).get_columns("operator_log")
        log_column_names = [column["name"] for column in log_columns]
        log_info = next(
            (column for column in log_columns if column["name"] == "log_info"),
            None,
        )
        expected_log_columns = ["id", "log_time", "simu_time", "log_type", "log_info"]
        # SQLite does not enforce VARCHAR lengths, but rebuilding known legacy
        # schemas keeps fresh and upgraded databases on the same published
        # TEXT contract. Unknown extension columns are never discarded.
        if (
            log_info is not None
            and str(log_info["type"]).upper() != "TEXT"
            and log_column_names == expected_log_columns
        ):
            with database.engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE IF EXISTS operator_log__migrated")
                connection.exec_driver_sql(
                    """
                    CREATE TABLE operator_log__migrated (
                        id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                        log_time INTEGER NOT NULL,
                        simu_time INTEGER NOT NULL,
                        log_type INTEGER NOT NULL DEFAULT 0,
                        log_info TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                connection.exec_driver_sql(
                    """
                    INSERT INTO operator_log__migrated (
                        id, log_time, simu_time, log_type, log_info
                    )
                    SELECT id, log_time, simu_time, log_type, COALESCE(log_info, '')
                    FROM operator_log
                    ORDER BY id
                    """
                )
                connection.exec_driver_sql("DROP TABLE operator_log")
                connection.exec_driver_sql(
                    "ALTER TABLE operator_log__migrated RENAME TO operator_log"
                )

    history_tables = ("scada_yc_his", "scada_yx_his", "scada_yt_his", "scada_yk_his")
    for table_name in history_tables:
        if table_name not in tables:
            continue
        columns = {column["name"] for column in inspect(database.engine).get_columns(table_name)}
        if "simu_time" in columns and "time" not in columns:
            with database.engine.begin() as connection:
                connection.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" RENAME COLUMN "simu_time" TO "time"'
                )

    return tuple(migrated_control_mode_tables)
