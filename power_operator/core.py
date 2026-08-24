from __future__ import annotations

import logging
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .database import Database
from .command_points import (
    find_device_for_predefined_status_point,
    find_predefined_command_point,
)
from .measurement_points import (
    normalize_point_name,
    predefined_measurement_targets,
)
from .models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    OperatorHistory,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYcHis,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
    ScadaYxHis,
)
from .retired_measurements import is_retired_operator_yc_measurement_name
from .strategy import DispatchInput, DispatchUnit, calculate_dispatch
from .status_commands import (
    current_status_for_yk,
    invalidate_non_actionable_yk,
    set_yk_if_status_changed,
    yk_requires_status_change,
)
from .wind_power import calculate_wind_max_power

LOGGER = logging.getLogger(__name__)

OPER_STOPPED = 0
OPER_RUNNING = 1
OPER_PAUSED = 2

CONTROL_OPEN = 0
CONTROL_CLOSED = 1

LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3
LOG_DECISION = 4
WIND_SPEED_YC_NAMES = {"simu.wind", "weather.wind", "wind_speed", "环境.当前风速"}
SOLAR_RADIATION_YC_NAMES = {
    "simu.solar",
    "weather.solar",
    "solar_radiation",
    "simu.sloar",
    "环境.当前太阳辐照",
}
AMBIENT_TEMPERATURE_YC_NAMES = {
    "simu.temp",
    "weather.temp",
    "amb_temp",
    "环境.当前温度",
}

DEVICE_NAME_RE = re.compile(
    r"^(dev_(?:diesal_gen|wind_gen|solar_gen|estore|load))\.(\d+)\.([a-z_]+)$"
)

DEVICE_BY_TABLE = {
    "dev_diesal_gen": DevDiesalGen,
    "dev_wind_gen": DevWindGen,
    "dev_solar_gen": DevSolarGen,
    "dev_estore": DevEstore,
    "dev_load": DevLoad,
}

YC_FIELDS = {
    "p_curr",
    "angle_pitch_curr",
    "soc_curr",
}
YX_FIELDS = {"status"}


@dataclass(frozen=True, slots=True)
class CycleSummary:
    simu_time: int
    load_kw: float
    renewable_set_kw: float
    diesel_set_kw: float
    storage_set_kw: float
    curtailment_kw: float
    unserved_kw: float


@dataclass(frozen=True, slots=True)
class ControlSnapshot:
    oper_status: int
    control_status: int
    data_period: int
    oper_period: int
    data_time_curr: int
    oper_time_curr: int


def wind_power_available(device: DevWindGen, wind_speed: float) -> float:
    """Compatibility adapter for callers that already have an ORM device."""

    return calculate_wind_max_power(
        current_wind_speed=wind_speed,
        p_rated=device.p_rated,
        wind_in=device.wind_in,
        wind_rated=device.wind_rated,
        wind_cut=device.wind_cut,
    )


def solar_power_available(device: DevSolarGen, irradiance: float) -> float:
    return max(
        0.0,
        min(float(device.p_rated), float(device.p_rated) * max(0.0, irradiance) / 1000.0),
    )


def _environment_value(points: list[ScadaYc], names: set[str], default: float = 0.0) -> float:
    for point in points:
        if point.name.strip().lower() in names:
            return float(point.value)
    return default


def _update_device_measurements(
    session: Session,
    yc_points: list[ScadaYc],
    yx_points: list[ScadaYx],
) -> None:
    predefined_targets = predefined_measurement_targets(session)
    measurement_updates = {}
    for point in yc_points:
        if int(point.time) <= 0 or is_retired_operator_yc_measurement_name(
            point.name
        ):
            continue
        match = DEVICE_NAME_RE.fullmatch(point.name.strip().lower())
        device = None
        field_name = None
        if match is not None and match.group(3) in YC_FIELDS:
            model = DEVICE_BY_TABLE[match.group(1)]
            device = session.get(model, int(match.group(2)))
            field_name = match.group(3)
        elif match is None:
            target = predefined_targets.get(normalize_point_name(point.name))
            if target is not None:
                device, field_name = target
        if device is None or field_name is None or not hasattr(device, field_name):
            continue
        measurement_key = (type(device), int(device.id), field_name)
        candidate_rank = (int(point.time), int(point.pnt_no))
        current = measurement_updates.get(measurement_key)
        if current is None or candidate_rank > current[0]:
            measurement_updates[measurement_key] = (
                candidate_rank,
                device,
                field_name,
                point.value,
            )
    for _rank, device, field_name, value in measurement_updates.values():
        setattr(device, field_name, float(value))
    status_updates = {}
    for point in yx_points:
        if int(point.time) <= 0:
            continue
        match = DEVICE_NAME_RE.fullmatch(point.name.strip().lower())
        device = None
        if match is not None and match.group(3) in YX_FIELDS:
            model = DEVICE_BY_TABLE[match.group(1)]
            device = session.get(model, int(match.group(2)))
        elif match is None:
            device = find_device_for_predefined_status_point(session, point)
        if device is None:
            continue
        device_key = (type(device), int(device.id))
        candidate_rank = (int(point.time), int(point.pnt_no))
        current = status_updates.get(device_key)
        if current is None or candidate_rank > current[0]:
            status_updates[device_key] = (candidate_rank, device, point.value)
    for _rank, device, value in status_updates.values():
        device.status = 1 if int(value) != 0 else 0


def _storage_power_limits(device: DevEstore, period_seconds: int) -> tuple[float, float]:
    if not device.status or device.battery_capacity <= 0:
        return 0.0, 0.0
    hours = max(1, period_seconds) / 3600.0
    charge_eff = max(1e-6, min(1.0, float(device.p_charge_eff)))
    discharge_eff = max(1e-6, min(1.0, float(device.p_discharge_eff)))
    room_kwh = max(0.0, (device.soc_max - device.soc_curr) * device.battery_capacity)
    energy_kwh = max(0.0, (device.soc_curr - device.soc_min) * device.battery_capacity)
    charge_limit = min(max(0.0, device.p_charge_max), room_kwh / (hours * charge_eff))
    discharge_limit = min(
        max(0.0, device.p_discharge_max), energy_kwh * discharge_eff / hours
    )
    return charge_limit, discharge_limit


def _set_command_point(
    point,
    value: float | int,
    current_time: int,
) -> tuple[bool, bool]:
    if point is None:
        return False, False
    value_changed = float(point.value) != float(value)
    point.value = value
    point.time = current_time
    return True, value_changed


def _snapshot_scada(session: Session, current_time: int) -> None:
    for point in session.scalars(select(ScadaYc).where(ScadaYc.time > 0)).all():
        if is_retired_operator_yc_measurement_name(point.name):
            continue
        session.merge(ScadaYcHis(time=current_time, pnt_no=point.pnt_no, value=point.value))
    for point in session.scalars(select(ScadaYx).where(ScadaYx.time > 0)).all():
        session.merge(ScadaYxHis(time=current_time, pnt_no=point.pnt_no, value=point.value))
    for point in session.scalars(select(ScadaYt).where(ScadaYt.time > 0)).all():
        session.merge(ScadaYtHis(time=current_time, pnt_no=point.pnt_no, value=point.value))
    for point in session.scalars(select(ScadaYk).where(ScadaYk.time > 0)).all():
        if yk_requires_status_change(session, point):
            session.merge(
                ScadaYkHis(time=current_time, pnt_no=point.pnt_no, value=point.value)
            )


def _record_history(
    session: Session,
    current_time: int,
    elapsed_seconds: int,
    accumulate_fuel: bool,
) -> OperatorHistory:
    diesels = session.scalars(select(DevDiesalGen)).all()
    winds = session.scalars(select(DevWindGen)).all()
    solars = session.scalars(select(DevSolarGen)).all()
    stores = session.scalars(select(DevEstore)).all()
    loads = session.scalars(select(DevLoad)).all()
    existing = session.get(OperatorHistory, current_time)
    previous_time = session.scalar(
        select(func.max(OperatorHistory.simu_time)).where(
            OperatorHistory.simu_time < current_time
        )
    )
    previous = (
        session.get(OperatorHistory, int(previous_time))
        if previous_time is not None
        else None
    )
    fuel = existing.diesal_curr_sum if existing is not None else (
        previous.diesal_curr_sum if previous is not None else 0.0
    )
    if accumulate_fuel and existing is None:
        fuel += sum(
            max(0.0, row.p_curr)
            * max(0.0, row.p_coeff)
            * max(1, elapsed_seconds)
            / 3600.0
            for row in diesels
        )
    yc_points = session.scalars(select(ScadaYc).where(ScadaYc.time > 0)).all()
    wind_speed = _environment_value(yc_points, WIND_SPEED_YC_NAMES)
    irradiance = _environment_value(yc_points, SOLAR_RADIATION_YC_NAMES)
    ambient_temperature = _environment_value(yc_points, AMBIENT_TEMPERATURE_YC_NAMES)
    history = OperatorHistory(
        simu_time=current_time,
        wind_speed=wind_speed,
        solar_radiation=irradiance,
        amb_temp=ambient_temperature,
        diesal_power_curr_sum=sum(row.p_curr for row in diesels),
        diesal_power_set_sum=sum(row.p_set for row in diesels),
        diesal_curr_sum=fuel,
        wind_power_curr_sum=sum(row.p_curr for row in winds),
        wind_power_max_sum=sum(row.p_max_curr for row in winds),
        wind_power_set_sum=sum(row.p_set for row in winds),
        solar_power_curr_sum=sum(row.p_curr for row in solars),
        solar_power_max_sum=sum(row.p_max_curr for row in solars),
        solar_power_set_sum=sum(row.p_set for row in solars),
        load_power_curr_sum=sum(row.p_curr for row in loads if row.status),
        estore_power_curr_sum=sum(row.p_curr for row in stores),
        estore_power_set_sum=sum(row.p_set for row in stores),
        estore_power_soc_sum=sum(row.soc_curr for row in stores),
    )
    return session.merge(history)


class OperatorCore:
    def __init__(
        self,
        database: Database,
        decision_id_factory: Callable[[int, int], str] | None = None,
    ):
        self.database = database
        self._last_oper_status: int | None = None
        self._last_processed_data_time = 0
        self._last_decision_monotonic: float | None = None
        self._decision_sequence = 0
        self._decision_id_factory = decision_id_factory or (
            lambda current_time, sequence: (
                f"decision-{current_time}-{os.getpid()}-{sequence:06d}"
            )
        )

    def read_control(self) -> ControlSnapshot:
        with self.database.session() as session:
            control = session.get(OperatorControl, 1)
            if control is None:
                return ControlSnapshot(OPER_STOPPED, CONTROL_OPEN, 1, 1, 0, 0)
            return ControlSnapshot(
                oper_status=int(control.oper_status),
                control_status=int(control.control_status),
                data_period=max(1, int(control.data_period)),
                oper_period=max(1, int(control.oper_period)),
                data_time_curr=max(0, int(control.data_time_curr)),
                oper_time_curr=max(0, int(control.oper_time_curr)),
            )

    def reset_runtime_data(self) -> None:
        def reset(session: Session) -> None:
            for model in (
                ScadaYcHis,
                ScadaYxHis,
                ScadaYtHis,
                ScadaYkHis,
                OperatorHistory,
                OperatorLog,
            ):
                session.execute(delete(model))
            for model in (ScadaYc, ScadaYt):
                for point in session.scalars(select(model)).all():
                    point.value = 0.0
                    point.time = 0
            for model in (ScadaYx, ScadaYk):
                for point in session.scalars(select(model)).all():
                    point.value = 0
                    point.time = 0
            for rtu in session.scalars(select(ScadaRtu)).all():
                rtu.status = 0
                rtu.refresh_time = 0
            for row in session.scalars(select(DevDiesalGen)).all():
                row.status = 0
                row.p_curr = 0.0
                row.p_set = 0.0
            for row in session.scalars(select(DevWindGen)).all():
                row.status = 0
                row.p_max_curr = 0.0
                row.angle_pitch_curr = 0.0
                row.p_curr = 0.0
                row.p_set = 0.0
            for row in session.scalars(select(DevSolarGen)).all():
                row.status = 0
                row.p_max_curr = 0.0
                row.p_curr = 0.0
                row.p_set = 0.0
            for row in session.scalars(select(DevEstore)).all():
                row.status = 0
                row.p_curr = 0.0
                row.p_set = 0.0
            for row in session.scalars(select(DevLoad)).all():
                row.status = 0
                row.p_curr = 0.0
            control = session.get(OperatorControl, 1)
            if control is not None:
                control.data_time_curr = 0
                control.oper_time_curr = 0

        self.database.write(reset)
        self._last_processed_data_time = 0
        self._last_decision_monotonic = None

    def process_data_refresh(
        self,
        current_time: int,
        wall_time: int | None = None,
    ) -> None:
        log_time = int(time.time()) if wall_time is None else int(wall_time)

        def refresh(session: Session) -> None:
            control = session.get(OperatorControl, 1)
            data_period = max(1, int(control.data_period)) if control is not None else 1
            yc_points = session.scalars(select(ScadaYc).order_by(ScadaYc.pnt_no)).all()
            yx_points = session.scalars(select(ScadaYx).order_by(ScadaYx.pnt_no)).all()
            valid_yc_points = [
                point
                for point in yc_points
                if int(point.time) > 0
                and not is_retired_operator_yc_measurement_name(point.name)
            ]
            valid_yx_points = [point for point in yx_points if int(point.time) > 0]
            _update_device_measurements(session, valid_yc_points, valid_yx_points)
            wind_speed = _environment_value(
                valid_yc_points, WIND_SPEED_YC_NAMES
            )
            irradiance = _environment_value(
                valid_yc_points, SOLAR_RADIATION_YC_NAMES
            )
            for device in session.scalars(select(DevWindGen)).all():
                device.p_max_curr = (
                    wind_power_available(device, wind_speed) if device.status else 0.0
                )
            for device in session.scalars(select(DevSolarGen)).all():
                device.p_max_curr = (
                    solar_power_available(device, irradiance) if device.status else 0.0
                )
            _record_history(session, current_time, data_period, accumulate_fuel=True)
            _snapshot_scada(session, current_time)
            session.add(
                OperatorLog(
                    log_time=log_time,
                    simu_time=current_time,
                    log_type=LOG_INFO,
                    log_info=f"数据刷新完成，运行时刻={current_time}",
                )
            )

        self.database.write(refresh)

    def run_decision(
        self,
        current_time: int,
        wall_time: int | None = None,
    ) -> CycleSummary:
        log_time = int(time.time()) if wall_time is None else int(wall_time)
        self._decision_sequence += 1
        decision_id = self._decision_id_factory(current_time, self._decision_sequence)

        def decide(session: Session) -> CycleSummary:
            control = session.get(OperatorControl, 1)
            if control is None:
                raise RuntimeError("operator_control 不存在")
            period = max(1, int(control.oper_period))
            previous_oper_time = int(control.oper_time_curr)
            diesels = session.scalars(select(DevDiesalGen).order_by(DevDiesalGen.id)).all()
            winds = session.scalars(select(DevWindGen).order_by(DevWindGen.id)).all()
            solars = session.scalars(select(DevSolarGen).order_by(DevSolarGen.id)).all()
            stores = session.scalars(select(DevEstore).order_by(DevEstore.id)).all()
            loads = session.scalars(select(DevLoad).order_by(DevLoad.id)).all()
            yc_points = session.scalars(select(ScadaYc).order_by(ScadaYc.pnt_no)).all()
            yx_points = session.scalars(select(ScadaYx).order_by(ScadaYx.pnt_no)).all()
            valid_yc = [
                point
                for point in yc_points
                if int(point.time) > 0
                and not is_retired_operator_yc_measurement_name(point.name)
            ]
            valid_yx = [point for point in yx_points if int(point.time) > 0]
            wind_speed = _environment_value(
                valid_yc, WIND_SPEED_YC_NAMES
            )
            irradiance = _environment_value(valid_yc, SOLAR_RADIATION_YC_NAMES)
            ambient_temperature = _environment_value(
                valid_yc, AMBIENT_TEMPERATURE_YC_NAMES
            )
            charge_units: list[DispatchUnit] = []
            discharge_units: list[DispatchUnit] = []
            storage_limits: dict[int, tuple[float, float]] = {}
            for device in stores:
                charge_limit, discharge_limit = _storage_power_limits(device, period)
                storage_limits[int(device.id)] = (charge_limit, discharge_limit)
                if device.status:
                    charge_units.append(DispatchUnit(device.id, 0.0, charge_limit))
                    discharge_units.append(DispatchUnit(device.id, 0.0, discharge_limit))
            load_kw = sum(max(0.0, row.p_curr) for row in loads if row.status)
            dispatch = calculate_dispatch(
                DispatchInput(
                    load_kw=load_kw,
                    wind=[
                        DispatchUnit(row.id, 0.0, row.p_max_curr)
                        for row in winds
                        if row.status
                    ],
                    solar=[
                        DispatchUnit(row.id, 0.0, row.p_max_curr)
                        for row in solars
                        if row.status
                    ],
                    diesel=[
                        DispatchUnit(row.id, row.p_min, row.p_max)
                        for row in diesels
                        if row.status
                    ],
                    storage_charge=charge_units,
                    storage_discharge=discharge_units,
                )
            )
            closed_loop = int(control.control_status) == CONTROL_CLOSED

            def input_state(row, fields: tuple[str, ...]) -> dict:
                included = bool(int(row.status))
                result = {
                    "id": int(row.id),
                    "name": str(row.name),
                    "status": int(row.status),
                    "included": included,
                    "excluded_reason": "" if included else "status_stopped",
                }
                for field in fields:
                    result[field] = float(getattr(row, field))
                return result

            device_inputs = {
                "dev_load": [
                    {
                        **input_state(row, ("p_curr",)),
                    }
                    for row in loads
                ],
                "dev_diesal_gen": [
                    input_state(
                        row,
                        ("p_curr", "p_set", "p_rated", "p_min", "p_max", "p_coeff"),
                    )
                    for row in diesels
                ],
                "dev_wind_gen": [
                    input_state(
                        row,
                        (
                            "p_curr",
                            "p_set",
                            "p_rated",
                            "wind_in",
                            "wind_rated",
                            "wind_cut",
                            "p_max_curr",
                        ),
                    )
                    for row in winds
                ],
                "dev_solar_gen": [
                    input_state(row, ("p_curr", "p_set", "p_rated", "p_max_curr"))
                    for row in solars
                ],
                "dev_estore": [],
            }
            for row in stores:
                state = input_state(
                    row,
                    (
                        "p_curr",
                        "p_set",
                        "p_charge_max",
                        "p_charge_eff",
                        "p_discharge_max",
                        "p_discharge_eff",
                        "battery_capacity",
                        "soc_curr",
                        "soc_min",
                        "soc_max",
                    ),
                )
                charge_limit, discharge_limit = storage_limits[int(row.id)]
                state["charge_limit_kw"] = float(charge_limit)
                state["discharge_limit_kw"] = float(discharge_limit)
                device_inputs["dev_estore"].append(state)

            valid_yc_payload = [
                {
                    "pnt_no": int(point.pnt_no),
                    "name": str(point.name),
                    "value": float(point.value),
                    "time": int(point.time),
                }
                for point in valid_yc
            ]
            valid_yx_payload = [
                {
                    "pnt_no": int(point.pnt_no),
                    "name": str(point.name),
                    "value": int(point.value),
                    "time": int(point.time),
                }
                for point in valid_yx
            ]
            excluded_points = [
                {
                    "signal": signal,
                    "pnt_no": int(point.pnt_no),
                    "name": str(point.name),
                    "value": float(point.value) if signal == "YC" else int(point.value),
                    "time": int(point.time),
                    "reason": "time_not_positive",
                }
                for signal, points in (("YC", yc_points), ("YX", yx_points))
                for point in points
                if int(point.time) <= 0
            ]

            outputs: list[dict] = []

            def apply_setpoint(row, table_name: str, values: dict[int, float]) -> None:
                old_setpoint = float(row.p_set)
                new_setpoint = float(values.get(int(row.id), 0.0))
                row.p_set = new_setpoint
                yt_point = find_predefined_command_point(
                    session, ScadaYt, table_name, row
                )
                yk_point = find_predefined_command_point(
                    session, ScadaYk, table_name, row
                )
                resolved_status = (
                    current_status_for_yk(session, yk_point)
                    if yk_point is not None
                    else None
                )
                current_status = (
                    int(resolved_status)
                    if resolved_status is not None
                    else int(row.status)
                )
                target_status = int(row.status)
                if closed_loop:
                    yt_generated, yt_value_changed = _set_command_point(
                        yt_point, new_setpoint, current_time
                    )
                    yt_time = (
                        int(current_time)
                        if yt_generated
                        else int(yt_point.time) if yt_point is not None else 0
                    )
                    yk_generated = (
                        set_yk_if_status_changed(
                            session,
                            pnt_no=int(yk_point.pnt_no),
                            current_status=current_status,
                            target_status=target_status,
                            current_time=current_time,
                        )
                        if yk_point is not None
                        else False
                    )
                    yt_reason = (
                        "point_not_defined"
                        if yt_point is None
                        else "setpoint_changed"
                        if yt_value_changed
                        else "setpoint_time_refreshed"
                    )
                    yk_reason = (
                        "point_not_defined"
                        if yk_point is None
                        else "status_changed" if yk_generated else "status_unchanged"
                    )
                else:
                    yt_generated = False
                    yt_value_changed = False
                    yk_generated = False
                    yt_time = 0
                    yt_reason = "open_loop"
                    yk_reason = "open_loop"
                outputs.append(
                    {
                        "table": table_name,
                        "id": int(row.id),
                        "name": str(row.name),
                        "current_status": current_status,
                        "target_status": target_status,
                        "p_curr": float(row.p_curr),
                        "p_set_before": old_setpoint,
                        "p_set_after": new_setpoint,
                        "p_set_delta": new_setpoint - old_setpoint,
                        "yt": {
                            "pnt_no": int(yt_point.pnt_no) if yt_point is not None else None,
                            "name": str(yt_point.name) if yt_point is not None else None,
                            "value": new_setpoint,
                            "time": yt_time,
                            "generated": yt_generated,
                            "reason": yt_reason,
                        },
                        "yk": {
                            "pnt_no": int(yk_point.pnt_no) if yk_point is not None else None,
                            "name": str(yk_point.name) if yk_point is not None else None,
                            "value": target_status,
                            "time": int(current_time) if yk_generated else 0,
                            "generated": yk_generated,
                            "reason": yk_reason,
                            "current_status": current_status,
                            "target_status": target_status,
                        },
                    }
                )

            for row in diesels:
                apply_setpoint(row, "dev_diesal_gen", dispatch.diesel)
            for row in winds:
                apply_setpoint(row, "dev_wind_gen", dispatch.wind)
            for row in solars:
                apply_setpoint(row, "dev_solar_gen", dispatch.solar)
            for row in stores:
                apply_setpoint(row, "dev_estore", dispatch.storage)
            if closed_loop:
                invalidate_non_actionable_yk(session)

            process = list(dispatch.process)
            generated_yk = [output["yk"] for output in outputs if output["yk"]["generated"]]
            process.append(
                {
                    "step": len(process) + 1,
                    "name": "status_decision",
                    "executed": bool(generated_yk),
                    "before": {
                        "current_statuses": {
                            f"{output['table']}.{output['id']}": output["current_status"]
                            for output in outputs
                        }
                    },
                    "action": {"generated_yk": generated_yk},
                    "after": {
                        "target_statuses": {
                            f"{output['table']}.{output['id']}": output["target_status"]
                            for output in outputs
                        }
                    },
                    "reason": (
                        "status_change_required"
                        if generated_yk
                        else "all_target_statuses_unchanged"
                    ),
                }
            )
            history = _record_history(session, current_time, period, accumulate_fuel=False)
            _snapshot_scada(session, current_time)
            control.oper_time_curr = current_time

            tolerance_kw = 1e-6
            warnings: list[dict] = []
            if dispatch.curtailment_kw > tolerance_kw:
                warnings.append(
                    {
                        "code": "renewable_curtailment",
                        "message": "储能吸收后仍存在新能源削减",
                        "value_kw": float(dispatch.curtailment_kw),
                    }
                )
            if dispatch.unserved_kw > tolerance_kw:
                warnings.append(
                    {
                        "code": "unserved_load",
                        "message": "可用电源和储能无法满足全部负荷",
                        "value_kw": float(dispatch.unserved_kw),
                    }
                )
            if excluded_points:
                warnings.append(
                    {
                        "code": "invalid_scada_points",
                        "message": "存在因 time<=0 未纳入输入的四遥点",
                        "count": len(excluded_points),
                    }
                )
            within_tolerance = abs(float(dispatch.balance_error_kw)) <= tolerance_kw
            if not within_tolerance:
                warnings.append(
                    {
                        "code": "power_balance_error",
                        "message": "功率平衡误差超过允许范围",
                        "value_kw": float(dispatch.balance_error_kw),
                    }
                )
            power_balance = {
                "load_kw": float(history.load_power_curr_sum),
                "wind_set_kw": float(history.wind_power_set_sum),
                "solar_set_kw": float(history.solar_power_set_sum),
                "diesel_set_kw": float(history.diesal_power_set_sum),
                "storage_set_kw": float(history.estore_power_set_sum),
                "unserved_kw": float(dispatch.unserved_kw),
                "overgeneration_kw": float(dispatch.overgeneration_kw),
            }
            process.append(
                {
                    "step": len(process) + 1,
                    "name": "power_balance",
                    "executed": True,
                    "before": power_balance,
                    "action": {
                        "formula": (
                            "wind_set + solar_set + diesel_set + storage_set "
                            "+ unserved - overgeneration - load"
                        )
                    },
                    "after": {"balance_error_kw": float(dispatch.balance_error_kw)},
                    "reason": "within_tolerance" if within_tolerance else "tolerance_exceeded",
                }
            )

            audit = {
                "schema_version": 1,
                "event": "control_decision",
                "decision_id": str(decision_id),
                "mode": "closed" if closed_loop else "open",
                "trigger": {
                    "oper_status": int(control.oper_status),
                    "control_status": int(control.control_status),
                    "mode": "closed" if closed_loop else "open",
                    "data_period": max(1, int(control.data_period)),
                    "oper_period": period,
                    "data_time_curr": int(current_time),
                    "previous_oper_time_curr": previous_oper_time,
                    "decision_wall_time": log_time,
                },
                "inputs": {
                    "environment": {
                        "wind_speed": float(wind_speed),
                        "solar_irradiance": float(irradiance),
                        "amb_temp": float(ambient_temperature),
                    },
                    "totals": {
                        "load_kw": float(load_kw),
                        "wind_available_kw": sum(
                            max(0.0, float(row.p_max_curr)) for row in winds if row.status
                        ),
                        "solar_available_kw": sum(
                            max(0.0, float(row.p_max_curr)) for row in solars if row.status
                        ),
                    },
                    "devices": device_inputs,
                    "valid_yc": valid_yc_payload,
                    "valid_yx": valid_yx_payload,
                    "excluded_points": excluded_points,
                },
                "process": process,
                "outputs": {
                    "devices": outputs,
                    "totals": {
                        "load_kw": float(history.load_power_curr_sum),
                        "wind_current_kw": float(history.wind_power_curr_sum),
                        "wind_set_kw": float(history.wind_power_set_sum),
                        "solar_current_kw": float(history.solar_power_curr_sum),
                        "solar_set_kw": float(history.solar_power_set_sum),
                        "diesel_current_kw": float(history.diesal_power_curr_sum),
                        "diesel_set_kw": float(history.diesal_power_set_sum),
                        "storage_current_kw": float(history.estore_power_curr_sum),
                        "storage_set_kw": float(history.estore_power_set_sum),
                        "storage_charge_kw": sum(
                            max(0.0, -float(value)) for value in dispatch.storage.values()
                        ),
                        "storage_discharge_kw": sum(
                            max(0.0, float(value)) for value in dispatch.storage.values()
                        ),
                        "curtailment_kw": float(dispatch.curtailment_kw),
                        "unserved_kw": float(dispatch.unserved_kw),
                    },
                },
                "validation": {
                    "equation": power_balance,
                    "balance_error_kw": float(dispatch.balance_error_kw),
                    "tolerance_kw": tolerance_kw,
                    "within_tolerance": within_tolerance,
                    "warnings": warnings,
                },
            }
            log_info = json.dumps(audit, ensure_ascii=False, allow_nan=False)
            session.add(
                OperatorLog(
                    log_time=log_time,
                    simu_time=current_time,
                    log_type=LOG_DECISION,
                    log_info=log_info,
                )
            )
            return CycleSummary(
                simu_time=current_time,
                load_kw=history.load_power_curr_sum,
                renewable_set_kw=history.wind_power_set_sum + history.solar_power_set_sum,
                diesel_set_kw=history.diesal_power_set_sum,
                storage_set_kw=history.estore_power_set_sum,
                curtailment_kw=dispatch.curtailment_kw,
                unserved_kw=dispatch.unserved_kw,
            )

        return self.database.write(decide)

    def tick(self, monotonic_time: float | None = None) -> CycleSummary | None:
        current_monotonic = (
            time.monotonic() if monotonic_time is None else float(monotonic_time)
        )
        control = self.read_control()
        previous = self._last_oper_status
        self._last_oper_status = control.oper_status
        if previous == OPER_STOPPED and control.oper_status == OPER_RUNNING:
            self.reset_runtime_data()
            return None
        if control.oper_status != OPER_RUNNING:
            self._last_decision_monotonic = None
            return None
        if control.data_time_curr > self._last_processed_data_time:
            self.process_data_refresh(control.data_time_curr)
            self._last_processed_data_time = control.data_time_curr
        if control.data_time_curr <= control.oper_time_curr:
            return None
        if (
            self._last_decision_monotonic is not None
            and current_monotonic - self._last_decision_monotonic
            < control.oper_period
        ):
            return None
        summary = self.run_decision(control.data_time_curr)
        self._last_decision_monotonic = current_monotonic
        return summary

    def run_cycle(self, wall_time: int | None = None) -> CycleSummary:
        """Process one local data/decision cycle for tests and maintenance."""

        control = self.read_control()
        current_time = control.data_time_curr
        if current_time <= control.oper_time_curr:
            current_time = control.oper_time_curr + control.oper_period

            def advance(session: Session) -> None:
                row = session.get(OperatorControl, 1)
                row.data_time_curr = current_time

            self.database.write(advance)
        self.process_data_refresh(current_time, wall_time=wall_time)
        self._last_processed_data_time = current_time
        return self.run_decision(current_time, wall_time=wall_time)

    def run_forever(self, poll_seconds: float = 0.5, stop_event=None) -> None:
        LOGGER.info("operator_core 已启动，控制表轮询周期 %.3f 秒", poll_seconds)
        while stop_event is None or not stop_event.is_set():
            started = time.monotonic()
            try:
                self.tick(started)
            except Exception:
                LOGGER.exception("operator_core 周期运行失败")
            elapsed = time.monotonic() - started
            delay = max(0.0, poll_seconds - elapsed)
            if stop_event is None:
                time.sleep(delay)
            elif stop_event.wait(delay):
                break
        LOGGER.info("operator_core 运行循环已停止")
