from __future__ import annotations

import math

__all__ = ["calculate_wind_max_power"]


def calculate_wind_max_power(
    current_wind_speed: float,
    p_rated: float,
    wind_in: float,
    wind_rated: float,
    wind_cut: float,
) -> float:
    """Calculate a turbine's current theoretical maximum active power in kW.

    The piecewise curve is zero below cut-in speed, cubic between cut-in and
    rated speed, rated power from rated speed up to (but excluding) cut-out
    speed, and zero at or above cut-out speed. Invalid or non-finite turbine
    parameters return zero so a bad device definition cannot inject NaN/Inf
    into dispatch calculations.
    """

    try:
        speed, rated_power, cut_in, rated_speed, cut_out = (
            float(current_wind_speed),
            float(p_rated),
            float(wind_in),
            float(wind_rated),
            float(wind_cut),
        )
    except (TypeError, ValueError):
        return 0.0
    if not all(
        math.isfinite(value)
        for value in (speed, rated_power, cut_in, rated_speed, cut_out)
    ):
        return 0.0
    if (
        rated_power <= 0.0
        or cut_in < 0.0
        or rated_speed <= cut_in
        or cut_out <= rated_speed
    ):
        return 0.0
    if speed < cut_in or speed >= cut_out:
        return 0.0
    if speed >= rated_speed:
        return rated_power

    denominator = rated_speed**3 - cut_in**3
    available_power = rated_power * (speed**3 - cut_in**3) / denominator
    return max(0.0, min(rated_power, available_power))
