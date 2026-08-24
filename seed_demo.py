from __future__ import annotations

import argparse
from sqlalchemy.orm import Session

from power_operator.database import Database, initialize_database
from power_operator.core import CONTROL_CLOSED, OPER_STOPPED
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    ScadaYc,
    ScadaYx,
)


def seed(session: Session) -> None:
    control = session.get(OperatorControl, 1)
    control.oper_status = OPER_STOPPED
    control.control_status = CONTROL_CLOSED
    control.data_period = 1
    control.oper_period = 5
    control.data_time_curr = 0
    control.oper_time_curr = 0

    devices = [
        DevDiesalGen(
            id=1,
            name="柴油发电机 1",
            p_rated=120,
            p_max=120,
            p_min=25,
            p_coeff=0.245,
            status=1,
            p_curr=25,
            p_set=25,
        ),
        DevDiesalGen(
            id=2,
            name="柴油发电机 2",
            p_rated=80,
            p_max=80,
            p_min=18,
            p_coeff=0.255,
            status=1,
            p_curr=18,
            p_set=18,
        ),
        DevWindGen(
            id=1,
            name="风机 1",
            p_rated=100,
            wind_in=3,
            wind_rated=11,
            wind_cut=25,
            status=1,
            p_curr=35,
        ),
        DevSolarGen(id=1, name="光伏阵列 1", p_rated=80, status=1, p_curr=40),
        DevEstore(
            id=1,
            name="电池储能 1",
            status=1,
            p_charge_max=50,
            p_charge_eff=0.95,
            p_discharge_max=50,
            p_discharge_eff=0.95,
            p_curr=0,
            p_set=0,
            battery_capacity=300,
            soc_curr=0.55,
            soc_max=0.9,
            soc_min=0.1,
        ),
        DevLoad(id=1, name="综合负荷 1", status=1, p_curr=145),
    ]
    for device in devices:
        session.merge(device)

    simu_time = 0
    yc = [
        ScadaYc(pnt_no=1, name="simu.wind", value=8.5, time=simu_time),
        ScadaYc(pnt_no=2, name="simu.solar", value=750.0, time=simu_time),
        ScadaYc(pnt_no=1001, name="dev_diesal_gen.1.p_curr", value=25.0, time=simu_time),
        ScadaYc(pnt_no=1002, name="dev_diesal_gen.2.p_curr", value=18.0, time=simu_time),
        ScadaYc(pnt_no=2001, name="dev_wind_gen.1.p_curr", value=35.0, time=simu_time),
        ScadaYc(pnt_no=3001, name="dev_solar_gen.1.p_curr", value=40.0, time=simu_time),
        ScadaYc(pnt_no=4001, name="dev_estore.1.p_curr", value=0.0, time=simu_time),
        ScadaYc(pnt_no=4002, name="dev_estore.1.soc_curr", value=0.55, time=simu_time),
        ScadaYc(pnt_no=5001, name="dev_load.1.p_curr", value=145.0, time=simu_time),
    ]
    yx = [
        ScadaYx(pnt_no=1001, name="dev_diesal_gen.1.status", value=1, time=simu_time),
        ScadaYx(pnt_no=1002, name="dev_diesal_gen.2.status", value=1, time=simu_time),
        ScadaYx(pnt_no=2001, name="dev_wind_gen.1.status", value=1, time=simu_time),
        ScadaYx(pnt_no=3001, name="dev_solar_gen.1.status", value=1, time=simu_time),
        ScadaYx(pnt_no=4001, name="dev_estore.1.status", value=1, time=simu_time),
        ScadaYx(pnt_no=5001, name="dev_load.1.status", value=1, time=simu_time),
    ]
    for point in [*yc, *yx]:
        session.merge(point)


def main() -> None:
    parser = argparse.ArgumentParser(description="写入可选的演示设备和四遥数据")
    parser.add_argument("--db", default="ems.db")
    args = parser.parse_args()
    database = Database(args.db)
    initialize_database(database)
    database.write(seed)
    print(f"演示数据已写入: {database.path}")


if __name__ == "__main__":
    main()
