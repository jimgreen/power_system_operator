from pathlib import Path

from generate_ems_principles_html import load_database, render_html
from power_operator.database import Database, initialize_database
from power_operator.models import ScadaYc, ScadaYk, ScadaYt, ScadaYx


def test_ems_reference_html_uses_actual_database_points_and_escapes_content(
    tmp_path,
):
    db_path = tmp_path / "ems.db"
    database = Database(db_path)
    initialize_database(database)

    def seed(session):
        session.add_all(
            [
                ScadaYc(
                    pnt_no=1,
                    name="环境.当前风速<script>",
                    value=8.12345,
                    time=3661,
                ),
                ScadaYx(
                    pnt_no=200101,
                    name="风力发电机1.运行状态",
                    value=1,
                    time=3661,
                ),
                ScadaYt(
                    pnt_no=200101,
                    name="风力发电机1.功率设定",
                    value=35.6789,
                    time=3661,
                ),
                ScadaYk(
                    pnt_no=200101,
                    name="风力发电机1.启停命令",
                    value=0,
                    time=0,
                ),
            ]
        )

    database.write(seed)
    database.dispose()

    points, metadata = load_database(db_path)
    root = Path(__file__).resolve().parents[1]
    document = render_html(root, db_path, points, metadata)

    assert metadata["integrity"] == "ok"
    assert {table: len(rows) for table, rows in points.items()} == {
        "scada_yc": 1,
        "scada_yx": 1,
        "scada_yt": 1,
        "scada_yk": 1,
    }
    assert "电网 EMS 功能原理" in document
    assert "新能源优先有功调度算法" in document
    assert "正式数据库四遥点位总表" in document
    assert "环境.当前风速&lt;script&gt;" in document
    assert "环境.当前风速<script>" not in document
    assert "模拟器 → EMS" in document
    assert "EMS → 模拟器" in document
    assert document.count("<th>数据方向</th>") == 4
    assert "8.123" in document
    assert "35.679" in document
    assert "01:01:01" in document
    assert "无效/待命" in document
    assert "@@" not in document
    assert document.count("<table>") == 4
    assert "https://" not in document
    assert "http://" not in document
