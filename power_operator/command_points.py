from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
)

_DEVICE_MODELS = {
    "dev_diesal_gen": DevDiesalGen,
    "dev_wind_gen": DevWindGen,
    "dev_solar_gen": DevSolarGen,
    "dev_estore": DevEstore,
    "dev_load": DevLoad,
}
_YT_SUFFIXES = {
    "dev_diesal_gen": ("有功出力设定", "功率设定"),
    "dev_wind_gen": ("功率设定", "有功出力设定"),
    "dev_solar_gen": ("功率设定", "有功出力设定"),
    "dev_estore": ("功率设定", "有功出力设定"),
}
_LEGACY_YT_RE = re.compile(
    r"^(dev_diesal_gen|dev_wind_gen|dev_solar_gen|dev_estore)\.(\d+)\.p_set$",
    re.IGNORECASE,
)
_LEGACY_YK_RE = re.compile(
    r"^(dev_diesal_gen|dev_wind_gen|dev_solar_gen|dev_estore)\.(\d+)\.status$",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    return str(value).strip().casefold()


def predefined_command_names(table_name: str, device, model) -> set[str]:
    device_name = str(device.name).strip()
    if not device_name:
        return set()
    if model is ScadaYt:
        suffixes = _YT_SUFFIXES.get(table_name, ())
    elif model is ScadaYk:
        suffixes = ("启停命令",)
    else:
        suffixes = ()
    return {_normalized(f"{device_name}.{suffix}") for suffix in suffixes}


def find_predefined_command_point(
    session: Session,
    model,
    table_name: str,
    device,
):
    """Find an operator-defined YT/YK point without inventing a new identity."""

    expected_names = predefined_command_names(table_name, device, model)
    if not expected_names:
        return None
    return next(
        (
            point
            for point in session.scalars(select(model).order_by(model.pnt_no)).all()
            if _normalized(point.name) in expected_names
        ),
        None,
    )


def find_device_for_predefined_status_point(
    session: Session,
    point: ScadaYx,
):
    """Resolve an operator-defined YX point to its actual device.

    The direct definition ``<device name>.运行状态`` is preferred.  Some
    imported point lists use a different YX description, so the independently
    defined YK at the same point number is accepted as a fallback when its
    name is exactly ``<device name>.启停命令``.  YK values are deliberately not
    inspected: a command target must never be treated as actual status.
    """

    devices = [
        (table_name, device)
        for table_name, model in _DEVICE_MODELS.items()
        for device in session.scalars(select(model).order_by(model.id)).all()
    ]
    status_name = _normalized(point.name)
    direct_matches = [
        device
        for _table_name, device in devices
        if str(device.name).strip()
        and status_name == _normalized(f"{device.name}.运行状态")
    ]
    if len(direct_matches) == 1:
        return direct_matches[0]

    command = session.get(ScadaYk, int(point.pnt_no))
    if command is None:
        return None
    command_name = _normalized(command.name)
    command_matches = [
        device
        for table_name, device in devices
        if command_name in predefined_command_names(
            table_name, device, ScadaYk
        )
    ]
    return command_matches[0] if len(command_matches) == 1 else None


def find_device_for_predefined_control_mode_point(
    session: Session,
    point: ScadaYx,
):
    """Resolve a simulator ``<device>.控制模式`` YX to a controllable device."""

    point_name = _normalized(point.name)
    matches = [
        device
        for table_name, model in _DEVICE_MODELS.items()
        if table_name != "dev_load"
        for device in session.scalars(select(model).order_by(model.id)).all()
        if str(device.name).strip()
        and point_name == _normalized(f"{device.name}.控制模式")
    ]
    if len(matches) == 1:
        return matches[0]

    point_number = int(point.pnt_no)
    if point_number <= 0 or point_number % 100 != 2:
        return None
    device_id = point_number // 100
    id_matches = [
        session.get(model, device_id)
        for table_name, model in _DEVICE_MODELS.items()
        if table_name != "dev_load"
    ]
    id_matches = [device for device in id_matches if device is not None]
    return id_matches[0] if len(id_matches) == 1 else None


def find_device_for_command_point(session: Session, point, model):
    """Resolve an existing YT/YK definition back to its controllable device."""

    raw_name = str(point.name).strip()
    legacy_pattern = _LEGACY_YT_RE if model is ScadaYt else _LEGACY_YK_RE
    legacy_match = legacy_pattern.fullmatch(raw_name)
    if legacy_match is not None:
        table_name = legacy_match.group(1).lower()
        return session.get(_DEVICE_MODELS[table_name], int(legacy_match.group(2)))

    normalized_name = _normalized(raw_name)
    matches = [
        device
        for table_name, device_model in _DEVICE_MODELS.items()
        if table_name != "dev_load"
        for device in session.scalars(select(device_model).order_by(device_model.id)).all()
        if normalized_name in predefined_command_names(table_name, device, model)
    ]
    return matches[0] if len(matches) == 1 else None


def migrate_legacy_generated_command_points(session: Session) -> dict[str, int]:
    """Move old auto-generated commands to existing definitions, then remove them.

    A legacy point is removed only when the matching device and a separately
    predefined Chinese command point both exist. Unknown or user-only points
    are left untouched.
    """

    counts = {"yt_points": 0, "yk_points": 0, "yt_history": 0, "yk_history": 0}
    specifications = (
        (ScadaYt, ScadaYtHis, _LEGACY_YT_RE, "yt_points", "yt_history"),
        (ScadaYk, ScadaYkHis, _LEGACY_YK_RE, "yk_points", "yk_history"),
    )
    for model, history_model, pattern, point_key, history_key in specifications:
        legacy_points = [
            point
            for point in session.scalars(select(model).order_by(model.pnt_no)).all()
            if pattern.fullmatch(str(point.name).strip()) is not None
        ]
        for legacy in legacy_points:
            match = pattern.fullmatch(str(legacy.name).strip())
            if match is None:
                continue
            table_name = match.group(1).lower()
            device = session.get(_DEVICE_MODELS[table_name], int(match.group(2)))
            if device is None:
                continue
            predefined = find_predefined_command_point(
                session, model, table_name, device
            )
            if predefined is None or int(predefined.pnt_no) == int(legacy.pnt_no):
                continue

            if int(legacy.time) > int(predefined.time):
                predefined.value = legacy.value
                predefined.time = legacy.time

            history_rows = session.scalars(
                select(history_model).where(history_model.pnt_no == legacy.pnt_no)
            ).all()
            for history in history_rows:
                target_key = (int(history.time), int(predefined.pnt_no))
                if session.get(history_model, target_key) is None:
                    session.add(
                        history_model(
                            time=int(history.time),
                            pnt_no=int(predefined.pnt_no),
                            value=history.value,
                        )
                    )
                session.delete(history)
                counts[history_key] += 1

            session.delete(legacy)
            counts[point_key] += 1
    return counts
