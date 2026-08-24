from __future__ import annotations

import re


_LEGACY_WIND_YAW_NAME = re.compile(
    r"^dev_wind_gen\.\d+\.angle_yaw_curr$",
    re.IGNORECASE,
)
_LEGACY_WIND_YAW_SETPOINT_NAME = re.compile(
    r"^dev_wind_gen\.\d+\.angle_yaw_(?:set|setpoint)$",
    re.IGNORECASE,
)
_LEGACY_WIND_PITCH_SETPOINT_NAME = re.compile(
    r"^dev_wind_gen\.\d+\.angle_pitch_(?:set|setpoint)$",
    re.IGNORECASE,
)
_LEGACY_RETIRED_YC_NAME = re.compile(
    r"^(?:"
    r"dev_diesal_gen\.\d+\.(?:diesal_curr|diesel_curr|step_diesel_consumption)"
    r"|dev_(?:wind_gen|solar_gen)\.\d+\.p_max_curr"
    r"|dev_wind_gen\.\d+\.p_set"
    r")$",
    re.IGNORECASE,
)


def is_retired_wind_yaw_measurement_name(value: object) -> bool:
    """Identify obsolete wind-yaw YC definitions during compatibility cleanup."""

    name = str(value).strip()
    return name.endswith(".当前偏航角") or _LEGACY_WIND_YAW_NAME.fullmatch(name) is not None


def is_retired_operator_yc_measurement_name(value: object) -> bool:
    """Identify obsolete YC definitions that the operator no longer consumes."""

    name = str(value).strip()
    retired_suffixes = (
        ".本步柴油消耗",
        ".理论最大有功",
        ".理论最大功率",
        ".理论最大出力",
        ".有功功率设定值",
    )
    return (
        is_retired_wind_yaw_measurement_name(name)
        or name.endswith(retired_suffixes)
        or _LEGACY_RETIRED_YC_NAME.fullmatch(name) is not None
    )


def is_retired_wind_yaw_setpoint_name(value: object) -> bool:
    """Identify obsolete wind-yaw YT definitions during compatibility cleanup."""

    name = str(value).strip()
    return (
        name.endswith(".偏航角设定")
        or _LEGACY_WIND_YAW_SETPOINT_NAME.fullmatch(name) is not None
    )


def is_retired_wind_pitch_setpoint_name(value: object) -> bool:
    """Identify obsolete wind-pitch YT definitions during compatibility cleanup."""

    name = str(value).strip()
    return (
        name.endswith(".桨距角设定")
        or _LEGACY_WIND_PITCH_SETPOINT_NAME.fullmatch(name) is not None
    )


def is_retired_wind_angle_setpoint_name(value: object) -> bool:
    """Identify all obsolete wind-angle YT definitions."""

    return is_retired_wind_yaw_setpoint_name(
        value
    ) or is_retired_wind_pitch_setpoint_name(value)
