from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
)

_DEVICE_MODELS = {
    "dev_diesal_gen": DevDiesalGen,
    "dev_wind_gen": DevWindGen,
    "dev_solar_gen": DevSolarGen,
    "dev_estore": DevEstore,
    "dev_load": DevLoad,
}

_MEASUREMENT_SUFFIXES = {
    "dev_diesal_gen": {
        "p_curr": ("当前有功", "当前功率", "当前出力"),
    },
    "dev_wind_gen": {
        "p_curr": ("当前有功", "当前功率", "当前出力"),
        "angle_pitch_curr": ("当前桨距角",),
    },
    "dev_solar_gen": {
        "p_curr": ("当前有功", "当前功率", "当前出力"),
    },
    "dev_estore": {
        "p_curr": ("当前功率", "当前有功", "当前出力"),
        "soc_curr": ("当前SOC", "SOC", "当前荷电状态"),
    },
    "dev_load": {
        "p_curr": ("当前负荷", "当前负荷值", "当前功率", "当前有功"),
    },
}


def normalize_point_name(value: str) -> str:
    return str(value).strip().casefold()


def predefined_measurement_targets(
    session: Session,
) -> dict[str, tuple[object, str]]:
    """Build unique ``<device name>.<measurement>`` YC targets.

    Duplicate device names are deliberately omitted instead of guessing which
    device a point belongs to.  The returned ORM objects belong to ``session``
    so the caller can update them in the same transaction as history writes.
    """

    candidates: dict[str, list[tuple[object, str]]] = defaultdict(list)
    for table_name, model in _DEVICE_MODELS.items():
        field_suffixes = _MEASUREMENT_SUFFIXES.get(table_name, {})
        for device in session.scalars(select(model).order_by(model.id)).all():
            device_name = str(device.name).strip()
            if not device_name:
                continue
            for field_name, suffixes in field_suffixes.items():
                for suffix in suffixes:
                    key = normalize_point_name(f"{device_name}.{suffix}")
                    candidates[key].append((device, field_name))
    return {
        point_name: targets[0]
        for point_name, targets in candidates.items()
        if len(targets) == 1
    }
