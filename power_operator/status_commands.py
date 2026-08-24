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
    ScadaYx,
)

_DEVICE_STATUS_RE = re.compile(
    r"^(dev_diesal_gen|dev_wind_gen|dev_solar_gen|dev_estore|dev_load)\.(\d+)\.status$",
    re.IGNORECASE,
)
_DEVICE_MODELS = {
    "dev_diesal_gen": DevDiesalGen,
    "dev_wind_gen": DevWindGen,
    "dev_solar_gen": DevSolarGen,
    "dev_estore": DevEstore,
    "dev_load": DevLoad,
}


def _binary_status(value: int | float) -> int:
    return 1 if int(value) != 0 else 0


def current_status_for_yk(session: Session, command: ScadaYk) -> int | None:
    """Return the freshest known actual status for one YK command.

    A valid YX is preferred because it is the direct remote indication. The
    device table is the fallback for canonical ``dev_table.id.status`` names.
    Unknown status returns ``None`` so callers can fail closed and not send an
    unverified switching command.
    """

    same_point = session.get(ScadaYx, int(command.pnt_no))
    if same_point is not None and int(same_point.time) > 0:
        return _binary_status(same_point.value)

    command_name = str(command.name).strip()
    same_name = session.scalars(
        select(ScadaYx)
        .where(ScadaYx.name == command_name, ScadaYx.time > 0)
        .order_by(ScadaYx.time.desc(), ScadaYx.pnt_no)
    ).first()
    if same_name is not None:
        return _binary_status(same_name.value)

    match = _DEVICE_STATUS_RE.fullmatch(command_name)
    if match is None:
        return None
    model = _DEVICE_MODELS[match.group(1).lower()]
    device = session.get(model, int(match.group(2)))
    if device is None:
        return None
    return _binary_status(device.status)


def yk_requires_status_change(session: Session, command: ScadaYk) -> bool:
    current_status = current_status_for_yk(session, command)
    if current_status is None:
        return False
    return current_status != _binary_status(command.value)


def invalidate_non_actionable_yk(session: Session) -> int:
    """Reset commands that cannot be proven to request a state change."""

    invalidated = 0
    commands = session.scalars(select(ScadaYk).where(ScadaYk.time > 0)).all()
    for command in commands:
        if not yk_requires_status_change(session, command):
            command.time = 0
            invalidated += 1
    return invalidated


def set_yk_if_status_changed(
    session: Session,
    *,
    pnt_no: int,
    current_status: int,
    target_status: int,
    current_time: int,
) -> bool:
    """Create a valid YK only when the decision changes actual status.

    When the two states match, an existing point remains as a definition but
    is reset to ``time=0`` so neither history nor an IO service can execute it.
    """

    current_value = _binary_status(current_status)
    target_value = _binary_status(target_status)
    point = session.get(ScadaYk, int(pnt_no))
    if point is None:
        return False
    if current_value == target_value:
        point.value = target_value
        point.time = 0
        return False
    if int(current_time) <= 0:
        raise ValueError("YK 控制时刻必须大于 0")
    point.value = target_value
    point.time = int(current_time)
    return True
