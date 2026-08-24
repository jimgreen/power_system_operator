from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DispatchUnit:
    id: int
    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class DispatchInput:
    load_kw: float
    wind: list[DispatchUnit]
    solar: list[DispatchUnit]
    diesel: list[DispatchUnit]
    storage_charge: list[DispatchUnit]
    storage_discharge: list[DispatchUnit]


@dataclass(slots=True)
class DispatchResult:
    wind: dict[int, float] = field(default_factory=dict)
    solar: dict[int, float] = field(default_factory=dict)
    diesel: dict[int, float] = field(default_factory=dict)
    storage: dict[int, float] = field(default_factory=dict)
    curtailment_kw: float = 0.0
    unserved_kw: float = 0.0
    overgeneration_kw: float = 0.0
    balance_error_kw: float = 0.0
    process: list[dict[str, Any]] = field(default_factory=list)


def _clamped_max(unit: DispatchUnit) -> float:
    return max(0.0, float(unit.maximum))


def _allocate_in_order(units: list[DispatchUnit], total: float) -> dict[int, float]:
    remaining = max(0.0, total)
    allocation: dict[int, float] = {}
    for unit in units:
        value = min(_clamped_max(unit), remaining)
        allocation[unit.id] = value
        remaining -= value
    return allocation


def _allocate_diesel(units: list[DispatchUnit], total: float) -> dict[int, float]:
    allocation = {
        unit.id: min(_clamped_max(unit), max(0.0, float(unit.minimum))) for unit in units
    }
    remaining = max(0.0, total - sum(allocation.values()))
    for unit in units:
        headroom = max(0.0, _clamped_max(unit) - allocation[unit.id])
        addition = min(headroom, remaining)
        allocation[unit.id] += addition
        remaining -= addition
    return allocation


def calculate_dispatch(data: DispatchInput) -> DispatchResult:
    """Calculate a renewable-first active-power dispatch in kW.

    Positive storage power means discharge, negative means charge. Wind is
    dispatched before solar when curtailment is unavoidable. Running diesel
    generators remain at or above their minimum output.
    """

    load = max(0.0, float(data.load_kw))
    wind_available = sum(_clamped_max(unit) for unit in data.wind)
    solar_available = sum(_clamped_max(unit) for unit in data.solar)
    renewable_available = wind_available + solar_available
    diesel_min = sum(
        min(_clamped_max(unit), max(0.0, float(unit.minimum))) for unit in data.diesel
    )
    diesel_max = sum(_clamped_max(unit) for unit in data.diesel)
    charge_max = sum(_clamped_max(unit) for unit in data.storage_charge)
    discharge_max = sum(_clamped_max(unit) for unit in data.storage_discharge)

    result = DispatchResult()
    storage_ids = {unit.id for unit in data.storage_charge} | {
        unit.id for unit in data.storage_discharge
    }
    result.storage = {unit_id: 0.0 for unit_id in storage_ids}

    def add_step(
        name: str,
        *,
        executed: bool,
        before: dict[str, Any],
        action: dict[str, Any],
        after: dict[str, Any],
        reason: str,
    ) -> None:
        result.process.append(
            {
                "step": len(result.process) + 1,
                "name": name,
                "executed": bool(executed),
                "before": before,
                "action": action,
                "after": after,
                "reason": reason,
            }
        )

    add_step(
        "renewable_priority",
        executed=renewable_available > 0,
        before={"load_kw": load},
        action={
            "wind_available_kw": wind_available,
            "solar_available_kw": solar_available,
            "renewable_available_kw": renewable_available,
        },
        after={"load_after_available_renewable_kw": max(0.0, load - renewable_available)},
        reason="renewable_first" if renewable_available > 0 else "no_available_renewable",
    )
    add_step(
        "diesel_minimum",
        executed=diesel_min > 0,
        before={"diesel_units": len(data.diesel)},
        action={"diesel_minimum_total_kw": diesel_min},
        after={"renewable_plus_diesel_min_kw": renewable_available + diesel_min},
        reason="running_diesel_minimum"
        if diesel_min > 0
        else "no_running_diesel_or_zero_minimum",
    )

    raw_surplus = 0.0
    charge = 0.0
    shortage = 0.0
    discharge = 0.0

    if renewable_available + diesel_min >= load:
        raw_surplus = renewable_available + diesel_min - load
        charge = min(raw_surplus, charge_max)
        renewable_used = max(0.0, min(renewable_available, load + charge - diesel_min))
        diesel_target = diesel_min
        result.curtailment_kw = max(0.0, renewable_available - renewable_used)
        charge_allocation = _allocate_in_order(data.storage_charge, charge)
        result.storage.update({unit_id: -value for unit_id, value in charge_allocation.items()})
        add_step(
            "storage_charge",
            executed=charge > 0,
            before={"surplus_kw": raw_surplus, "charge_limit_kw": charge_max},
            action={"charge_kw": charge, "allocation_kw": charge_allocation},
            after={"surplus_after_charge_kw": max(0.0, raw_surplus - charge)},
            reason=(
                "absorb_surplus"
                if charge > 0
                else "no_surplus" if raw_surplus <= 0 else "no_charge_capacity"
            ),
        )
    else:
        renewable_used = renewable_available
        residual = load - renewable_used
        diesel_target = min(diesel_max, max(diesel_min, residual))
        shortage = max(0.0, residual - diesel_target)
        discharge = min(shortage, discharge_max)
        discharge_allocation = _allocate_in_order(data.storage_discharge, discharge)
        result.storage.update(discharge_allocation)
        result.unserved_kw = max(0.0, shortage - discharge)
        add_step(
            "storage_charge",
            executed=False,
            before={"surplus_kw": 0.0, "charge_limit_kw": charge_max},
            action={"charge_kw": 0.0, "allocation_kw": {}},
            after={"surplus_after_charge_kw": 0.0},
            reason="supply_deficit",
        )

    wind_used = min(wind_available, renewable_used)
    solar_used = max(0.0, renewable_used - wind_used)
    result.wind = _allocate_in_order(data.wind, wind_used)
    result.solar = _allocate_in_order(data.solar, solar_used)
    result.diesel = _allocate_diesel(data.diesel, diesel_target)

    add_step(
        "renewable_curtailment",
        executed=result.curtailment_kw > 0,
        before={"renewable_available_kw": renewable_available},
        action={
            "wind_set_kw": result.wind,
            "solar_set_kw": result.solar,
            "curtailment_kw": result.curtailment_kw,
        },
        after={"renewable_set_kw": wind_used + solar_used},
        reason="surplus_after_storage"
        if result.curtailment_kw > 0
        else "all_available_renewable_used",
    )
    diesel_increase = max(0.0, diesel_target - diesel_min)
    add_step(
        "diesel_increase",
        executed=diesel_increase > 0,
        before={"diesel_minimum_total_kw": diesel_min},
        action={"increase_kw": diesel_increase, "allocation_kw": result.diesel},
        after={"diesel_set_total_kw": sum(result.diesel.values())},
        reason="renewable_deficit"
        if diesel_increase > 0
        else "diesel_minimum_sufficient_or_no_headroom_needed",
    )
    add_step(
        "storage_discharge",
        executed=discharge > 0,
        before={"shortage_after_diesel_kw": shortage, "discharge_limit_kw": discharge_max},
        action={
            "discharge_kw": discharge,
            "allocation_kw": {
                unit_id: value for unit_id, value in result.storage.items() if value > 0
            },
        },
        after={"shortage_after_discharge_kw": result.unserved_kw},
        reason=(
            "cover_remaining_shortage"
            if discharge > 0
            else "no_shortage" if shortage <= 0 else "no_discharge_capacity"
        ),
    )
    add_step(
        "unserved_energy",
        executed=result.unserved_kw > 0,
        before={"shortage_after_discharge_kw": result.unserved_kw},
        action={"unserved_kw": result.unserved_kw},
        after={"served_load_kw": max(0.0, load - result.unserved_kw)},
        reason="insufficient_total_supply"
        if result.unserved_kw > 0
        else "load_fully_supplied",
    )

    supply = sum(result.wind.values()) + sum(result.solar.values()) + sum(result.diesel.values())
    supply += sum(result.storage.values())
    physical_error = supply - load
    if physical_error > 0:
        result.overgeneration_kw = physical_error
    result.balance_error_kw = physical_error + result.unserved_kw - result.overgeneration_kw
    return result
