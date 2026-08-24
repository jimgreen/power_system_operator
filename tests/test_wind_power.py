from __future__ import annotations

import pytest

from power_operator.wind_power import calculate_wind_max_power


@pytest.mark.parametrize(
    ("wind_speed", "expected"),
    [
        (-1.0, 0.0),
        (2.9, 0.0),
        (3.0, 0.0),
        (7.0, 100.0 * (7.0**3 - 3.0**3) / (10.0**3 - 3.0**3)),
        (10.0, 100.0),
        (15.0, 100.0),
        (19.999, 100.0),
        (20.0, 0.0),
        (25.0, 0.0),
    ],
)
def test_calculate_wind_max_power_uses_piecewise_turbine_curve(wind_speed, expected):
    result = calculate_wind_max_power(
        current_wind_speed=wind_speed,
        p_rated=100.0,
        wind_in=3.0,
        wind_rated=10.0,
        wind_cut=20.0,
    )

    assert result == pytest.approx(expected)


@pytest.mark.parametrize(
    ("p_rated", "wind_in", "wind_rated", "wind_cut"),
    [
        (0.0, 3.0, 10.0, 20.0),
        (-100.0, 3.0, 10.0, 20.0),
        (100.0, -1.0, 10.0, 20.0),
        (100.0, 3.0, 3.0, 20.0),
        (100.0, 3.0, 2.0, 20.0),
        (100.0, 3.0, 10.0, 10.0),
        (100.0, 3.0, 10.0, 9.0),
    ],
)
def test_calculate_wind_max_power_returns_zero_for_invalid_parameters(
    p_rated,
    wind_in,
    wind_rated,
    wind_cut,
):
    assert calculate_wind_max_power(
        current_wind_speed=8.0,
        p_rated=p_rated,
        wind_in=wind_in,
        wind_rated=wind_rated,
        wind_cut=wind_cut,
    ) == 0.0
