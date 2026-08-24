from __future__ import annotations

import pytest

from power_operator.core import CONTROL_CLOSED, OPER_PAUSED, OPER_RUNNING, OperatorCore
from power_operator.database import Database, initialize_database
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    ScadaYc,
    ScadaYcHis,
    ScadaYt,
    OperatorHistory,
)
from power_operator.wind_power import calculate_wind_max_power


def seed_system(session):
    session.add_all(
        [
            DevDiesalGen(
                id=1,
                name="柴油机1",
                p_rated=100,
                p_max=100,
                p_min=20,
                p_coeff=0.25,
                status=1,
            ),
            DevWindGen(
                id=1,
                name="风机1",
                p_rated=100,
                wind_in=3,
                wind_rated=10,
                wind_cut=20,
                status=1,
            ),
            DevSolarGen(id=1, name="光伏1", p_rated=60, status=1),
            DevEstore(
                id=1,
                name="储能1",
                status=1,
                p_charge_max=30,
                p_charge_eff=0.95,
                p_discharge_max=40,
                p_discharge_eff=0.95,
                battery_capacity=200,
                soc_curr=0.5,
                soc_max=0.9,
                soc_min=0.1,
            ),
            DevLoad(id=1, name="负荷1", status=1),
            ScadaYc(pnt_no=1, name="环境.当前风速", value=10, time=1),
            ScadaYc(pnt_no=2, name="环境.当前太阳辐照", value=1000, time=1),
            ScadaYc(pnt_no=3, name="dev_load.1.p_curr", value=100, time=1),
            ScadaYc(pnt_no=4, name="dev_diesal_gen.1.p_curr", value=20, time=1),
            ScadaYc(pnt_no=5, name="环境.当前温度", value=23.4567, time=1),
            ScadaYt(pnt_no=200001, name="风机1.功率设定", value=0, time=0),
        ]
    )
    control = session.get(OperatorControl, 1)
    control.oper_status = OPER_RUNNING
    control.control_status = CONTROL_CLOSED
    control.oper_period = 1


def test_core_cycle_refreshes_devices_dispatch_and_history(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(seed_system)

    summary = OperatorCore(db).run_cycle(wall_time=1000)

    assert summary.simu_time == 1
    with db.session() as session:
        assert session.get(DevWindGen, 1).p_max_curr == 100
        assert session.get(DevSolarGen, 1).p_max_curr == 60
        assert session.get(DevDiesalGen, 1).p_set == 20
        assert session.get(DevEstore, 1).p_set == -30
        assert session.get(OperatorControl, 1).oper_time_curr == 1
        history = session.get(OperatorHistory, 1)
        assert history.wind_speed == 10
        assert history.solar_radiation == 1000
        assert history.amb_temp == 23.4567
        assert history.wind_power_set_sum + history.solar_power_set_sum == 110
        assert session.get(ScadaYt, 200001).time == 1
        assert session.query(ScadaYcHis).filter_by(time=1).count() == 5


def test_core_tick_honors_paused_state(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(lambda session: setattr(session.get(OperatorControl, 1), "oper_status", OPER_PAUSED))

    core = OperatorCore(db)
    assert core.tick(monotonic_time=10) is None
    with db.session() as session:
        assert session.get(OperatorControl, 1).oper_time_curr == 0


def test_wind_pmax_is_calculated_from_wind_speed_and_not_retired_yc(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevWindGen(
                    id=1,
                    name="风机1",
                    p_rated=100,
                    wind_in=3,
                    wind_rated=10,
                    wind_cut=20,
                    status=1,
                    p_max_curr=999,
                ),
                ScadaYc(pnt_no=1, name="环境.当前风速", value=8, time=10),
                ScadaYc(
                    pnt_no=2,
                    name="风机1.理论最大有功",
                    value=1,
                    time=10,
                ),
            ]
        )

    database.write(seed)

    OperatorCore(database).process_data_refresh(10, wall_time=100)

    expected = calculate_wind_max_power(8, 100, 3, 10, 20)
    with database.session() as session:
        assert session.get(DevWindGen, 1).p_max_curr == pytest.approx(expected)
        assert session.get(ScadaYcHis, (10, 1)).value == 8
        assert session.get(ScadaYcHis, (10, 2)) is None
