from __future__ import annotations

from power_operator.database import Database, initialize_database
from power_operator.models import DevWindGen, ScadaYk, ScadaYx
from power_operator.status_commands import (
    current_status_for_yk,
    set_yk_if_status_changed,
)


def test_status_command_is_only_valid_when_target_differs_from_current(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def exercise(session):
        session.add(
            ScadaYk(
                pnt_no=200001,
                name="dev_wind_gen.1.status",
                value=0,
                time=5,
            )
        )
        session.flush()
        unchanged = set_yk_if_status_changed(
            session,
            pnt_no=200001,
            current_status=1,
            target_status=1,
            current_time=10,
        )
        point = session.get(ScadaYk, 200001)
        assert unchanged is False
        assert point.value == 1
        assert point.time == 0

        changed = set_yk_if_status_changed(
            session,
            pnt_no=200001,
            current_status=1,
            target_status=0,
            current_time=11,
        )
        assert changed is True
        assert point.value == 0
        assert point.time == 11

        refreshed_same_value = set_yk_if_status_changed(
            session,
            pnt_no=200001,
            current_status=1,
            target_status=0,
            current_time=12,
        )
        assert refreshed_same_value is True
        assert point.value == 0
        assert point.time == 12

    database.write(exercise)


def test_latest_valid_yx_is_preferred_when_deciding_whether_to_send_yk(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevWindGen(id=1, name="W1", status=0),
                ScadaYx(
                    pnt_no=200001,
                    name="dev_wind_gen.1.status",
                    value=1,
                    time=9,
                ),
                ScadaYk(
                    pnt_no=200001,
                    name="dev_wind_gen.1.status",
                    value=1,
                    time=10,
                ),
            ]
        )

    database.write(seed)
    with database.session() as session:
        command = session.get(ScadaYk, 200001)
        assert current_status_for_yk(session, command) == 1


def test_status_command_never_creates_an_undefined_yk_point(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def exercise(session):
        generated = set_yk_if_status_changed(
            session,
            pnt_no=987654,
            current_status=1,
            target_status=0,
            current_time=10,
        )
        assert generated is False
        assert session.get(ScadaYk, 987654) is None

    database.write(exercise)
