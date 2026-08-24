from __future__ import annotations

import pytest

from power_operator.strategy import DispatchInput, DispatchUnit, calculate_dispatch


def unit(unit_id: int, minimum: float, maximum: float) -> DispatchUnit:
    return DispatchUnit(id=unit_id, minimum=minimum, maximum=maximum)


def test_surplus_renewable_keeps_diesel_at_minimum_then_charges_and_curtails():
    result = calculate_dispatch(
        DispatchInput(
            load_kw=100,
            wind=[unit(1, 0, 100)],
            solar=[unit(1, 0, 60)],
            diesel=[unit(1, 20, 100)],
            storage_charge=[unit(1, 0, 30)],
            storage_discharge=[unit(1, 0, 40)],
        )
    )

    assert result.diesel[1] == pytest.approx(20)
    assert result.storage[1] == pytest.approx(-30)
    assert sum(result.wind.values()) + sum(result.solar.values()) == pytest.approx(110)
    assert result.curtailment_kw == pytest.approx(50)
    assert result.unserved_kw == pytest.approx(0)
    assert result.balance_error_kw == pytest.approx(0)


def test_renewable_first_then_diesel_supplies_residual_load():
    result = calculate_dispatch(
        DispatchInput(
            load_kw=180,
            wind=[unit(1, 0, 60)],
            solar=[unit(1, 0, 20)],
            diesel=[unit(1, 30, 120), unit(2, 20, 80)],
            storage_charge=[],
            storage_discharge=[unit(1, 0, 50)],
        )
    )

    assert sum(result.wind.values()) + sum(result.solar.values()) == pytest.approx(80)
    assert sum(result.diesel.values()) == pytest.approx(100)
    assert result.storage[1] == pytest.approx(0)
    assert result.unserved_kw == pytest.approx(0)


def test_storage_discharges_after_diesel_reaches_maximum():
    result = calculate_dispatch(
        DispatchInput(
            load_kw=250,
            wind=[unit(1, 0, 30)],
            solar=[],
            diesel=[unit(1, 20, 100)],
            storage_charge=[unit(1, 0, 40)],
            storage_discharge=[unit(1, 0, 70)],
        )
    )

    assert result.diesel[1] == pytest.approx(100)
    assert result.storage[1] == pytest.approx(70)
    assert result.unserved_kw == pytest.approx(50)
    assert result.balance_error_kw == pytest.approx(0)
