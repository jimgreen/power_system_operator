from __future__ import annotations

import json

import pytest
from sqlalchemy import event, select

from power_operator.core import (
    CONTROL_CLOSED,
    CONTROL_OPEN,
    LOG_DECISION,
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
    OperatorLog,
    ScadaYc,
    ScadaYk,
    ScadaYt,
    ScadaYx,
)


def _seed_decision_case(session, *, closed_loop: bool) -> None:
    session.add_all(
        [
            DevDiesalGen(
                id=1,
                name="柴发一号",
                p_rated=120.0,
                p_max=120.0,
                p_min=25.0,
                p_coeff=0.245,
                status=1,
                p_curr=25.0,
                p_set=12.0,
            ),
            DevDiesalGen(
                id=2,
                name="停运柴发",
                p_rated=80.0,
                p_max=80.0,
                p_min=18.0,
                p_coeff=0.255,
                status=0,
                p_curr=0.0,
                p_set=0.0,
            ),
            DevWindGen(
                id=1,
                name="风机一号",
                p_rated=100.0,
                wind_in=3.0,
                wind_rated=11.0,
                wind_cut=25.0,
                status=1,
                p_max_curr=65.0,
                p_curr=50.0,
                p_set=40.0,
            ),
            DevSolarGen(
                id=1,
                name="光伏一号",
                p_rated=80.0,
                status=1,
                p_max_curr=40.0,
                p_curr=30.0,
                p_set=20.0,
            ),
            DevEstore(
                id=1,
                name="储能一号",
                status=1,
                p_charge_max=50.0,
                p_charge_eff=0.95,
                p_discharge_max=50.0,
                p_discharge_eff=0.95,
                p_curr=0.0,
                p_set=0.0,
                battery_capacity=300.0,
                soc_curr=0.55,
                soc_max=0.9,
                soc_min=0.1,
            ),
            DevLoad(id=1, name="综合负荷", status=1, p_curr=100.0),
            ScadaYc(pnt_no=1, name="simu.wind", value=8.5, time=3600),
            ScadaYc(pnt_no=2, name="simu.solar", value=500.0, time=3600),
            ScadaYc(pnt_no=4, name="amb_temp", value=21.5, time=3600),
            ScadaYc(
                pnt_no=3,
                name="dev_load.1.p_curr",
                value=100.0,
                time=3600,
            ),
            ScadaYc(pnt_no=99, name="invalid.point", value=999.0, time=0),
            ScadaYx(
                pnt_no=100001,
                name="dev_diesal_gen.1.status",
                value=1,
                time=3600,
            ),
            ScadaYx(
                pnt_no=100002,
                name="dev_diesal_gen.2.status",
                value=0,
                time=0,
            ),
            ScadaYt(
                pnt_no=100001,
                name="柴发一号.有功出力设定",
                value=0.0,
                time=0,
            ),
            ScadaYt(
                pnt_no=100002,
                name="停运柴发.有功出力设定",
                value=0.0,
                time=0,
            ),
            ScadaYt(
                pnt_no=200001,
                name="风机一号.功率设定",
                value=0.0,
                time=0,
            ),
            ScadaYt(
                pnt_no=300001,
                name="光伏一号.功率设定",
                value=0.0,
                time=0,
            ),
            ScadaYt(
                pnt_no=400001,
                name="储能一号.功率设定",
                value=0.0,
                time=0,
            ),
            ScadaYk(
                pnt_no=100001,
                name="柴发一号.启停命令",
                value=0,
                time=0,
            ),
            ScadaYk(
                pnt_no=100002,
                name="停运柴发.启停命令",
                value=0,
                time=0,
            ),
            ScadaYk(
                pnt_no=200001,
                name="风机一号.启停命令",
                value=0,
                time=0,
            ),
            ScadaYk(
                pnt_no=300001,
                name="光伏一号.启停命令",
                value=0,
                time=0,
            ),
            ScadaYk(
                pnt_no=400001,
                name="储能一号.启停命令",
                value=0,
                time=0,
            ),
        ]
    )
    control = session.get(OperatorControl, 1)
    control.oper_status = OPER_RUNNING
    control.control_status = CONTROL_CLOSED if closed_loop else CONTROL_OPEN
    control.data_period = 5
    control.oper_period = 10
    control.data_time_curr = 3600
    control.oper_time_curr = 3590


def test_decision_writes_complete_utf8_audit_with_inputs_process_and_outputs(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(lambda session: _seed_decision_case(session, closed_loop=False))

    summary = OperatorCore(
        database,
        decision_id_factory=lambda current_time, sequence: f"decision-{current_time}-{sequence:06d}",
    ).run_decision(3600, wall_time=1_787_422_688)

    with database.session() as session:
        logs = session.scalars(
            select(OperatorLog).where(OperatorLog.log_type == LOG_DECISION)
        ).all()
        assert len(logs) == 1
        log = logs[0]
        audit = json.loads(log.log_info)
        assert log.log_time == 1_787_422_688
        assert log.simu_time == 3600
        assert len(log.log_info) > 1024
        assert audit["schema_version"] == 1
        assert audit["event"] == "control_decision"
        assert audit["decision_id"] == "decision-3600-000001"
        assert audit["mode"] == "open"
        assert audit["trigger"] == {
            "oper_status": OPER_RUNNING,
            "control_status": CONTROL_OPEN,
            "mode": "open",
            "data_period": 5,
            "oper_period": 10,
            "data_time_curr": 3600,
            "previous_oper_time_curr": 3590,
            "decision_wall_time": 1_787_422_688,
        }
        assert audit["inputs"]["environment"] == {
            "wind_speed": 8.5,
            "solar_irradiance": 500.0,
            "amb_temp": 21.5,
        }
        assert audit["inputs"]["totals"]["load_kw"] == 100.0
        assert {point["pnt_no"] for point in audit["inputs"]["valid_yc"]} == {
            1,
            2,
            3,
            4,
        }
        assert audit["inputs"]["excluded_points"][0]["pnt_no"] == 99
        diesels = audit["inputs"]["devices"]["dev_diesal_gen"]
        assert diesels[0]["included"] is True
        assert diesels[1]["included"] is False
        assert diesels[1]["excluded_reason"] == "status_stopped"
        assert {
            "renewable_priority",
            "diesel_minimum",
            "storage_charge",
            "renewable_curtailment",
            "diesel_increase",
            "storage_discharge",
            "unserved_energy",
            "status_decision",
            "power_balance",
        } <= {step["name"] for step in audit["process"]}
        for step in audit["process"]:
            assert {"step", "name", "executed", "before", "action", "after", "reason"} <= set(step)
        assert audit["outputs"]["totals"]["load_kw"] == summary.load_kw
        assert audit["outputs"]["totals"]["curtailment_kw"] == summary.curtailment_kw
        assert audit["outputs"]["totals"]["unserved_kw"] == summary.unserved_kw
        assert audit["validation"]["within_tolerance"] is True
        assert audit["validation"]["balance_error_kw"] == pytest.approx(0.0)
        diesel_output = next(
            item
            for item in audit["outputs"]["devices"]
            if item["table"] == "dev_diesal_gen" and item["id"] == 1
        )
        assert diesel_output["p_set_before"] == 12.0
        assert diesel_output["p_set_after"] == session.get(DevDiesalGen, 1).p_set
        assert diesel_output["yt"]["generated"] is False
        assert diesel_output["yt"]["reason"] == "open_loop"
        assert diesel_output["yk"]["generated"] is False
        assert diesel_output["yk"]["reason"] == "open_loop"
        assert session.query(ScadaYt).count() == 5
        assert all(point.time == 0 for point in session.scalars(select(ScadaYt)))
        assert session.query(ScadaYk).count() == 5
        assert all(point.time == 0 for point in session.scalars(select(ScadaYk)))
        assert session.get(OperatorControl, 1).oper_time_curr == 3600


def test_closed_loop_audit_matches_written_commands_and_records_unchanged_yk(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(lambda session: _seed_decision_case(session, closed_loop=True))

    OperatorCore(database).run_decision(3600, wall_time=1_787_422_688)

    with database.session() as session:
        log = session.scalars(
            select(OperatorLog).where(OperatorLog.log_type == LOG_DECISION)
        ).one()
        audit = json.loads(log.log_info)
        assert audit["mode"] == "closed"
        for output in audit["outputs"]["devices"]:
            if output["table"] == "dev_load":
                continue
            yt = session.get(ScadaYt, output["yt"]["pnt_no"])
            assert yt is not None
            assert output["yt"]["value"] == yt.value
            assert output["yt"]["time"] == yt.time
            assert output["yk"]["generated"] is False
            assert output["yk"]["reason"] == "status_unchanged"
            assert output["yk"]["current_status"] == output["yk"]["target_status"]
        assert session.query(ScadaYk).count() == 5
        assert all(point.time == 0 for point in session.scalars(select(ScadaYk)))


def test_decision_output_rolls_back_when_audit_insert_fails(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)
    database.write(lambda session: _seed_decision_case(session, closed_loop=True))

    def reject_audit(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("INSERT INTO OPERATOR_LOG"):
            raise RuntimeError("injected decision audit failure")

    event.listen(database.engine, "before_cursor_execute", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="injected decision audit failure"):
            OperatorCore(database).run_decision(3600, wall_time=1_787_422_688)
    finally:
        event.remove(database.engine, "before_cursor_execute", reject_audit)

    with database.session() as session:
        assert session.get(DevDiesalGen, 1).p_set == 12.0
        assert session.get(DevWindGen, 1).p_set == 40.0
        assert session.get(DevSolarGen, 1).p_set == 20.0
        assert session.get(OperatorControl, 1).oper_time_curr == 3590
        assert session.query(ScadaYt).count() == 5
        assert all(point.time == 0 for point in session.scalars(select(ScadaYt)))
        assert session.query(ScadaYk).count() == 5
        assert all(point.time == 0 for point in session.scalars(select(ScadaYk)))
        assert session.query(OperatorLog).count() == 0
