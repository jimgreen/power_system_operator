from __future__ import annotations

import json

from sqlalchemy import select

from power_operator.core import (
    CONTROL_CLOSED,
    CONTROL_OPEN,
    OPER_RUNNING,
    OperatorCore,
)
from power_operator.database import Database, initialize_database
from power_operator.models import (
    DevDiesalGen,
    DevEstore,
    DevLoad,
    DevSolarGen,
    DevWindGen,
    OperatorControl,
    OperatorHistory,
    OperatorLog,
    ScadaYc,
    ScadaYcHis,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
    ScadaYxHis,
)


def test_stopped_to_running_transition_clears_runtime_data_but_keeps_point_definitions(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                ScadaYc(pnt_no=1, name="simu.wind", value=8.0, time=9),
                ScadaYx(pnt_no=2, name="dev_load.1.status", value=1, time=9),
                ScadaYt(pnt_no=3, name="dev_diesal_gen.1.p_set", value=20.0, time=9),
                ScadaYk(pnt_no=4, name="dev_diesal_gen.1.status", value=1, time=9),
                DevEstore(
                    id=5,
                    name="S1",
                    status=1,
                    soc_curr=0.7,
                    soc_min=0.2,
                ),
                OperatorHistory(simu_time=9),
                OperatorLog(log_time=1, simu_time=9, log_type=1, log_info="old"),
                ScadaYcHis(time=9, pnt_no=1, value=8.0),
            ]
        )

    database.write(seed)
    core = OperatorCore(database)
    assert core.tick(monotonic_time=0) is None
    database.write(
        lambda session: setattr(session.get(OperatorControl, 1), "oper_status", OPER_RUNNING)
    )

    core.tick(monotonic_time=0.5)

    with database.session() as session:
        assert session.get(ScadaYc, 1).name == "simu.wind"
        assert session.get(ScadaYc, 1).value == 0
        assert session.get(ScadaYc, 1).time == 0
        assert session.get(ScadaYx, 2).value == 0
        assert session.get(ScadaYt, 3).value == 0
        assert session.get(ScadaYk, 4).value == 0
        assert session.get(DevEstore, 5).soc_curr == 0.7
        assert session.query(OperatorHistory).count() == 0
        assert session.query(OperatorLog).count() == 0
        assert session.query(ScadaYcHis).count() == 0
        control = session.get(OperatorControl, 1)
        assert control.data_time_curr == 0
        assert control.oper_time_curr == 0


def test_tick_uses_wall_clock_decision_period_when_simulation_time_jumps(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.data_period = 1
        control.oper_period = 5
        control.data_time_curr = 60

    database.write(seed)
    core = OperatorCore(database)

    first = core.tick(monotonic_time=0.0)
    assert first is not None
    assert first.simu_time == 60

    for wall_second, simu_time in enumerate(range(120, 360, 60), start=1):
        database.write(
            lambda session, value=simu_time: setattr(
                session.get(OperatorControl, 1), "data_time_curr", value
            )
        )
        assert core.tick(monotonic_time=float(wall_second)) is None

    database.write(
        lambda session: setattr(
            session.get(OperatorControl, 1), "data_time_curr", 360
        )
    )
    second = core.tick(monotonic_time=5.0)

    assert second is not None
    assert second.simu_time == 360
    with database.session() as session:
        assert session.query(OperatorHistory).count() == 6
        assert session.query(OperatorLog).filter_by(log_type=4).count() == 2
        assert session.get(OperatorControl, 1).oper_time_curr == 360


def test_valid_predefined_yx_updates_every_device_status_in_real_time(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(id=1001, name="柴油发电机1", status=0),
                DevWindGen(id=2001, name="风力发电机1", status=1),
                DevSolarGen(id=3001, name="光伏发电单元1", status=0),
                DevLoad(id=4001, name="综合负荷1", status=1),
                DevEstore(id=5001, name="储能单元1", status=0),
                ScadaYx(pnt_no=100101, name="柴油发电机1.运行状态", value=1, time=10),
                ScadaYx(pnt_no=200101, name="风力发电机1.运行状态", value=0, time=10),
                ScadaYx(pnt_no=300101, name="光伏发电单元1.运行状态", value=1, time=10),
                ScadaYx(pnt_no=400101, name="综合负荷1.运行状态", value=0, time=10),
                ScadaYx(pnt_no=500101, name="储能单元1.运行状态", value=1, time=10),
            ]
        )

    database.write(seed)
    OperatorCore(database).process_data_refresh(current_time=10, wall_time=100)

    with database.session() as session:
        assert session.get(DevDiesalGen, 1001).status == 1
        assert session.get(DevWindGen, 2001).status == 0
        assert session.get(DevSolarGen, 3001).status == 1
        assert session.get(DevLoad, 4001).status == 0
        assert session.get(DevEstore, 5001).status == 1


def test_valid_control_mode_yx_updates_all_controllable_devices(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(id=1001, name="柴油发电机1", control_mode=1),
                DevWindGen(id=2001, name="风力发电机1", control_mode=1),
                DevSolarGen(id=3001, name="光伏发电单元1", control_mode=1),
                DevEstore(id=5001, name="储能单元1", control_mode=1),
                ScadaYx(pnt_no=100102, name="柴油发电机1.控制模式", value=0, time=10),
                ScadaYx(pnt_no=200102, name="风力发电机1.控制模式", value=0, time=10),
                ScadaYx(pnt_no=300102, name="光伏发电单元1.控制模式", value=0, time=10),
                ScadaYx(pnt_no=500102, name="储能单元1.控制模式", value=0, time=10),
            ]
        )

    database.write(seed)
    OperatorCore(database).process_data_refresh(current_time=10, wall_time=100)

    with database.session() as session:
        assert session.get(DevDiesalGen, 1001).control_mode == 0
        assert session.get(DevWindGen, 2001).control_mode == 0
        assert session.get(DevSolarGen, 3001).control_mode == 0
        assert session.get(DevEstore, 5001).control_mode == 0


def test_closed_operator_controls_only_closed_devices_and_accounts_for_open_power(
    tmp_path,
):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="开环柴发",
                    p_rated=100,
                    p_max=100,
                    p_min=0,
                    status=1,
                    p_curr=30,
                    p_set=10,
                    control_mode=0,
                ),
                DevDiesalGen(
                    id=2,
                    name="闭环柴发",
                    p_rated=100,
                    p_max=100,
                    p_min=0,
                    status=1,
                    p_curr=0,
                    p_set=0,
                    control_mode=1,
                ),
                DevWindGen(
                    id=3,
                    name="停机开环风机",
                    status=0,
                    p_curr=900,
                    control_mode=0,
                ),
                DevLoad(id=1, name="负荷", status=1, p_curr=100),
                ScadaYt(pnt_no=100101, name="开环柴发.有功出力设定", value=88, time=9),
                ScadaYt(pnt_no=100201, name="闭环柴发.有功出力设定", value=0, time=0),
                ScadaYk(pnt_no=100101, name="开环柴发.启停命令", value=1, time=9),
                ScadaYk(pnt_no=100201, name="闭环柴发.启停命令", value=1, time=0),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_CLOSED
        control.oper_period = 10

    database.write(seed)
    OperatorCore(database).run_decision(10, wall_time=100)

    with database.session() as session:
        open_device = session.get(DevDiesalGen, 1)
        closed_device = session.get(DevDiesalGen, 2)
        open_yt = session.get(ScadaYt, 100101)
        open_yk = session.get(ScadaYk, 100101)
        closed_yt = session.get(ScadaYt, 100201)
        audit = json.loads(
            session.scalars(
                select(OperatorLog).where(OperatorLog.log_type == 4)
            ).one().log_info
        )

    assert (open_device.p_set, open_yt.time, open_yk.time) == (30, 0, 0)
    assert (closed_device.p_set, closed_yt.value, closed_yt.time) == (70, 70, 10)
    assert audit["inputs"]["totals"]["open_loop_fixed_power_kw"] == 30
    open_output = next(
        row
        for row in audit["outputs"]["devices"]
        if row["table"] == "dev_diesal_gen" and row["id"] == 1
    )
    assert open_output["control_mode"] == 0
    assert open_output["yt"]["reason"] == "device_open_loop"


def test_predefined_yx_can_use_same_point_yk_to_identify_device(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevWindGen(id=2001, name="风力发电机1", status=0),
                ScadaYx(
                    pnt_no=200101,
                    name="风机一号运行遥信",
                    value=2,
                    time=10,
                ),
                ScadaYk(
                    pnt_no=200101,
                    name="风力发电机1.启停命令",
                    value=0,
                    time=99,
                ),
            ]
        )

    database.write(seed)
    OperatorCore(database).process_data_refresh(current_time=10, wall_time=100)

    with database.session() as session:
        assert session.get(DevWindGen, 2001).status == 1


def test_newest_valid_yx_wins_when_multiple_points_map_to_same_device(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevLoad(id=4001, name="综合负荷1", status=1),
                ScadaYx(
                    pnt_no=400101,
                    name="综合负荷1.运行状态",
                    value=0,
                    time=20,
                ),
                ScadaYx(
                    pnt_no=499999,
                    name="dev_load.4001.status",
                    value=1,
                    time=10,
                ),
            ]
        )

    database.write(seed)
    OperatorCore(database).process_data_refresh(current_time=20, wall_time=100)

    with database.session() as session:
        assert session.get(DevLoad, 4001).status == 0


def test_predefined_yc_updates_device_measurements_and_soc_from_latest_value(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(id=1001, name="柴油发电机1", status=1, p_curr=0.0),
                DevWindGen(id=2001, name="风力发电机1", status=1),
                DevSolarGen(id=3001, name="光伏发电单元1", status=1),
                DevLoad(id=4001, name="综合负荷1", status=1, p_curr=0.0),
                DevEstore(
                    id=5001,
                    name="储能单元1",
                    status=1,
                    p_curr=0.0,
                    soc_curr=0.2,
                ),
                DevEstore(
                    id=5002,
                    name="储能单元2",
                    status=1,
                    soc_curr=0.4,
                ),
                ScadaYc(pnt_no=100101, name="柴油发电机1.当前有功", value=11.1, time=10),
                ScadaYc(pnt_no=200101, name="风力发电机1.当前有功", value=22.2, time=10),
                ScadaYc(pnt_no=200104, name="风力发电机1.当前桨距角", value=4.4, time=10),
                ScadaYc(pnt_no=300101, name="光伏发电单元1.当前有功", value=55.5, time=10),
                ScadaYc(pnt_no=400101, name="综合负荷1.当前负荷", value=66.6, time=10),
                ScadaYc(pnt_no=500101, name="储能单元1.当前功率", value=-7.7, time=10),
                ScadaYc(pnt_no=500102, name="储能单元1.当前SOC", value=0.6789, time=10),
                ScadaYc(
                    pnt_no=599999,
                    name="dev_estore.5001.soc_curr",
                    value=0.3,
                    time=5,
                ),
                ScadaYc(pnt_no=500202, name="储能单元2.当前SOC", value=0.9, time=0),
            ]
        )

    database.write(seed)
    OperatorCore(database).process_data_refresh(current_time=10, wall_time=100)

    with database.session() as session:
        assert session.get(DevDiesalGen, 1001).p_curr == 11.1
        wind = session.get(DevWindGen, 2001)
        assert wind.p_curr == 22.2
        assert wind.angle_pitch_curr == 4.4
        assert session.get(DevSolarGen, 3001).p_curr == 55.5
        assert session.get(DevLoad, 4001).p_curr == 66.6
        storage = session.get(DevEstore, 5001)
        assert storage.p_curr == -7.7
        assert storage.soc_curr == 0.6789
        assert session.get(DevEstore, 5002).soc_curr == 0.4
        history = session.get(OperatorHistory, 10)
        assert history.load_power_curr_sum == 66.6
        assert history.estore_power_soc_sum == 1.0789


def test_new_data_updates_devices_and_closed_loop_decision_writes_commands(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                ),
                DevLoad(id=1, name="L1", status=1),
                ScadaYc(pnt_no=1, name="dev_load.1.p_curr", value=60, time=10),
                ScadaYx(pnt_no=2, name="dev_load.1.status", value=1, time=10),
                ScadaYt(
                    pnt_no=91001,
                    name="D1.有功出力设定",
                    value=0.0,
                    time=0,
                ),
                ScadaYk(
                    pnt_no=91002,
                    name="D1.启停命令",
                    value=0,
                    time=0,
                ),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_CLOSED
        control.data_period = 5
        control.oper_period = 10
        control.data_time_curr = 10
        control.oper_time_curr = 0

    database.write(seed)
    core = OperatorCore(database)

    summary = core.tick(monotonic_time=0)

    assert summary is not None
    assert summary.simu_time == 10
    with database.session() as session:
        assert session.get(DevLoad, 1).p_curr == 60
        assert session.get(DevDiesalGen, 1).p_set == 60
        predefined_yt = session.get(ScadaYt, 91001)
        assert predefined_yt.name == "D1.有功出力设定"
        assert predefined_yt.value == 60
        assert predefined_yt.time == 10
        assert session.get(ScadaYt, 100001) is None
        assert session.get(ScadaYk, 91002).name == "D1.启停命令"
        assert session.get(ScadaYk, 91002).time == 0
        assert session.get(ScadaYk, 100001) is None
        assert session.get(OperatorControl, 1).oper_time_curr == 10
        assert session.get(ScadaYcHis, (10, 1)).value == 60


def test_closed_loop_refreshes_yt_time_when_setpoint_value_is_unchanged(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                    p_curr=60,
                    p_set=60,
                ),
                DevLoad(id=1, name="L1", status=1, p_curr=60),
                ScadaYt(
                    pnt_no=91001,
                    name="D1.有功出力设定",
                    value=60.0,
                    time=10,
                ),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_CLOSED
        control.oper_period = 10
        control.oper_time_curr = 10

    database.write(seed)

    OperatorCore(database).run_decision(20, wall_time=100)

    with database.session() as session:
        point = session.get(ScadaYt, 91001)
        assert point.value == 60.0
        assert point.time == 20
        assert session.get(ScadaYtHis, (20, 91001)).value == 60.0


def test_closed_loop_does_not_create_commands_when_points_are_not_predefined(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                ),
                DevLoad(id=1, name="L1", status=1, p_curr=60),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_CLOSED
        control.data_time_curr = 10
        control.oper_time_curr = 0
        control.oper_period = 10

    database.write(seed)
    OperatorCore(database).tick()

    with database.session() as session:
        assert session.query(ScadaYt).count() == 0
        assert session.query(ScadaYk).count() == 0
        audit = session.scalars(
            select(OperatorLog).where(OperatorLog.log_type == 4)
        ).one()
        output = next(
            row
            for row in json.loads(audit.log_info)["outputs"]["devices"]
            if row["table"] == "dev_diesal_gen"
        )
        assert output["yt"]["reason"] == "point_not_defined"
        assert output["yk"]["reason"] == "point_not_defined"


def test_open_loop_updates_device_setpoints_without_writing_commands(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                ),
                DevLoad(id=1, name="L1", status=1, p_curr=60),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_OPEN
        control.data_time_curr = 10
        control.oper_time_curr = 0
        control.oper_period = 10

    database.write(seed)
    summary = OperatorCore(database).tick()

    assert summary is not None
    with database.session() as session:
        assert session.get(DevDiesalGen, 1).p_set == 60
        assert session.query(ScadaYt).count() == 0
        assert session.query(ScadaYk).count() == 0


def test_closed_loop_invalidates_existing_yk_when_target_already_matches_status(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(
                    id=1,
                    name="D1",
                    p_rated=100,
                    p_max=100,
                    p_min=20,
                    p_coeff=0.25,
                    status=1,
                ),
                DevLoad(id=1, name="L1", status=1, p_curr=60),
                ScadaYk(
                    pnt_no=100001,
                    name="dev_diesal_gen.1.status",
                    value=1,
                    time=5,
                ),
            ]
        )
        control = session.get(OperatorControl, 1)
        control.oper_status = OPER_RUNNING
        control.control_status = CONTROL_CLOSED
        control.data_time_curr = 10
        control.oper_time_curr = 0
        control.oper_period = 10

    database.write(seed)
    OperatorCore(database).tick()

    with database.session() as session:
        point = session.get(ScadaYk, 100001)
        assert point.value == 1
        assert point.time == 0
        assert session.query(ScadaYkHis).count() == 0


def test_zero_time_scada_points_do_not_update_devices_or_enter_history(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(
        lambda session: session.add_all(
            [
                DevLoad(id=1, name="L1", status=1, p_curr=12.0),
                ScadaYc(
                    pnt_no=1,
                    name="dev_load.1.p_curr",
                    value=999.0,
                    time=0,
                ),
                ScadaYx(
                    pnt_no=2,
                    name="dev_load.1.status",
                    value=0,
                    time=0,
                ),
                ScadaYt(pnt_no=3, name="dev_load.1.p_set", value=1.0, time=0),
                ScadaYk(pnt_no=4, name="dev_load.1.status", value=1, time=0),
            ]
        )
    )

    OperatorCore(database).process_data_refresh(current_time=10, wall_time=100)

    with database.session() as session:
        load = session.get(DevLoad, 1)
        assert load.p_curr == 12.0
        assert load.status == 1
        assert session.get(OperatorHistory, 10).load_power_curr_sum == 12.0
        assert session.query(ScadaYcHis).count() == 0
        assert session.query(ScadaYxHis).count() == 0
        assert session.query(ScadaYtHis).count() == 0
        assert session.query(ScadaYkHis).count() == 0
