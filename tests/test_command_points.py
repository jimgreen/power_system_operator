from __future__ import annotations

from power_operator.database import Database, initialize_database
from power_operator.models import (
    DevDiesalGen,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
)


def test_database_init_moves_legacy_generated_commands_to_predefined_points(tmp_path):
    database = Database(tmp_path / "ems.db")
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                DevDiesalGen(id=1001, name="柴油发电机1"),
                ScadaYt(
                    pnt_no=100101,
                    name="柴油发电机1.有功出力设定",
                    value=0.0,
                    time=0,
                ),
                ScadaYt(
                    pnt_no=101001,
                    name="dev_diesal_gen.1001.p_set",
                    value=82.25,
                    time=12,
                ),
                ScadaYtHis(time=12, pnt_no=101001, value=82.25),
                ScadaYk(
                    pnt_no=100101,
                    name="柴油发电机1.启停命令",
                    value=1,
                    time=0,
                ),
                ScadaYk(
                    pnt_no=101001,
                    name="dev_diesal_gen.1001.status",
                    value=0,
                    time=12,
                ),
                ScadaYkHis(time=12, pnt_no=101001, value=0),
            ]
        )

    database.write(seed)
    initialize_database(database)

    with database.session() as session:
        predefined_yt = session.get(ScadaYt, 100101)
        assert predefined_yt.name == "柴油发电机1.有功出力设定"
        assert predefined_yt.value == 82.25
        assert predefined_yt.time == 12
        assert session.get(ScadaYt, 101001) is None
        assert session.get(ScadaYtHis, (12, 100101)).value == 82.25
        assert session.get(ScadaYtHis, (12, 101001)) is None

        predefined_yk = session.get(ScadaYk, 100101)
        assert predefined_yk.name == "柴油发电机1.启停命令"
        assert predefined_yk.value == 0
        assert predefined_yk.time == 12
        assert session.get(ScadaYk, 101001) is None
        assert session.get(ScadaYkHis, (12, 100101)).value == 0
        assert session.get(ScadaYkHis, (12, 101001)) is None
