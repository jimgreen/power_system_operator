from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Database, initialize_database
from .models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    ScadaRtu,
    ScadaYc,
    ScadaYk,
    ScadaYt,
    ScadaYx,
)
from .retired_measurements import (
    is_retired_operator_yc_measurement_name,
    is_retired_wind_angle_setpoint_name,
)


DEFINITION_MODELS = (
    ScadaRtu,
    ScadaYc,
    ScadaYx,
    ScadaYt,
    ScadaYk,
    DevDiesalGen,
    DevWindGen,
    DevSolarGen,
    DevEstore,
    DevLoad,
)

OPTIONAL_SOURCE_DEFAULTS: dict[str, dict[str, object]] = {
    table_name: {"control_mode": 1}
    for table_name in (
        "dev_diesal_gen",
        "dev_wind_gen",
        "dev_solar_gen",
        "dev_estore",
    )
}


@dataclass(frozen=True, slots=True)
class TableImportReport:
    source_count: int
    target_count: int
    ignored_source_columns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DefinitionImportReport:
    source_path: Path
    target_path: Path
    source_sha256_before: str | None
    source_sha256_after: str | None
    source_unchanged: bool | None
    tables: dict[str, TableImportReport]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _try_file_sha256(path: Path) -> str | None:
    try:
        return _file_sha256(path)
    except (OSError, PermissionError):
        # A running Windows SQLite writer may open the database without file
        # sharing for direct hashing. The SQLite URI below is still read-only
        # and is the authoritative safety boundary.
        return None


def _read_source_rows(
    source_path: Path,
) -> tuple[dict[type, list[dict[str, Any]]], dict[str, tuple[str, ...]]]:
    uri = f"file:{source_path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        # Pin all ten table reads to one SQLite snapshot even if the external
        # simulator continues updating its database concurrently.
        connection.execute("BEGIN")
        source_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        copied: dict[type, list[dict[str, Any]]] = {}
        ignored: dict[str, tuple[str, ...]] = {}
        for model in DEFINITION_MODELS:
            table_name = model.__tablename__
            if table_name not in source_tables:
                raise ValueError(f"源数据库缺少必需表 {table_name}")
            source_columns = [
                str(row[1])
                for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            ]
            target_columns = [column.name for column in model.__table__.columns]
            source_defaults = OPTIONAL_SOURCE_DEFAULTS.get(table_name, {})
            missing = [
                column
                for column in target_columns
                if column not in source_columns and column not in source_defaults
            ]
            if missing:
                raise ValueError(
                    f"源表 {table_name} 缺少目标必需列: {', '.join(missing)}"
                )
            ignored[table_name] = tuple(
                column for column in source_columns if column not in target_columns
            )
            projection = ", ".join(
                f'"{column}"'
                if column in source_columns
                else f'{int(source_defaults[column])} AS "{column}"'
                for column in target_columns
            )
            order_by = ", ".join(
                f'"{column.name}"' for column in model.__table__.primary_key.columns
            )
            statement = f'SELECT {projection} FROM "{table_name}"'
            if order_by:
                statement += f" ORDER BY {order_by}"
            rows = [dict(row) for row in connection.execute(statement)]
            if model is ScadaYc:
                rows = [
                    row
                    for row in rows
                    if not is_retired_operator_yc_measurement_name(row.get("name", ""))
                ]
            elif model is ScadaYt:
                rows = [
                    row
                    for row in rows
                    if not is_retired_wind_angle_setpoint_name(row.get("name", ""))
                ]
            copied[model] = rows
        connection.rollback()
        return copied, ignored
    finally:
        connection.close()


def _target_rows(session: Session, model: type) -> list[dict[str, Any]]:
    columns = list(model.__table__.columns)
    statement = select(model).order_by(*model.__table__.primary_key.columns)
    return [
        {column.name: getattr(row, column.name) for column in columns}
        for row in session.scalars(statement).all()
    ]


def import_power_definitions(
    source: str | Path,
    target: str | Path = "ems.db",
    *,
    replace: bool = False,
) -> DefinitionImportReport:
    """Copy device and SCADA definition rows from a read-only legacy database.

    The source schema is never cloned. Only columns declared by the target ORM
    are copied, so legacy compatibility columns such as ``scada_rtu.conn_num``
    cannot leak into ``ems.db``.
    """

    source_path = Path(source).expanduser().resolve()
    target_path = Path(target).expanduser().resolve()
    if source_path == target_path:
        raise ValueError("源 power.db 与目标 ems.db 不能是同一个文件")
    if not source_path.is_file():
        raise FileNotFoundError(f"源数据库不存在: {source_path}")

    source_sha256_before = _try_file_sha256(source_path)
    source_rows, ignored_columns = _read_source_rows(source_path)

    database = Database(target_path)
    try:
        initialize_database(database)

        def copy_rows(session: Session) -> None:
            populated = [
                model.__tablename__
                for model in DEFINITION_MODELS
                if session.scalar(select(func.count()).select_from(model))
            ]
            if populated and not replace:
                raise ValueError(
                    "目标定义表非空，拒绝覆盖；确认后请使用 --replace。非空表: "
                    + ", ".join(populated)
                )
            if replace:
                for model in DEFINITION_MODELS:
                    session.execute(delete(model))
            for model in DEFINITION_MODELS:
                session.add_all(model(**values) for values in source_rows[model])

        database.write(copy_rows)

        reports: dict[str, TableImportReport] = {}
        with database.session() as session:
            for model in DEFINITION_MODELS:
                table_name = model.__tablename__
                actual = _target_rows(session, model)
                expected = source_rows[model]
                if actual != expected:
                    raise RuntimeError(f"目标表 {table_name} 写入后逐列校验失败")
                reports[table_name] = TableImportReport(
                    source_count=len(expected),
                    target_count=len(actual),
                    ignored_source_columns=ignored_columns[table_name],
                )
    finally:
        database.dispose()

    source_sha256_after = _try_file_sha256(source_path)
    source_unchanged = (
        source_sha256_before == source_sha256_after
        if source_sha256_before is not None and source_sha256_after is not None
        else None
    )
    return DefinitionImportReport(
        source_path=source_path,
        target_path=target_path,
        source_sha256_before=source_sha256_before,
        source_sha256_after=source_sha256_after,
        source_unchanged=source_unchanged,
        tables=reports,
    )
