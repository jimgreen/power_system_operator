from __future__ import annotations

import threading

from power_operator.database import Database, initialize_database
from power_operator.io_service import ThreadingRtuServer
from power_operator.models import ScadaYc, ScadaYt
from rtu_client import exchange


def test_tcp_json_line_round_trip(tmp_path):
    db = Database(tmp_path / "ems.db")
    initialize_database(db)
    db.write(
        lambda session: session.add_all(
            [
                ScadaYc(pnt_no=1, name="simu.wind", value=0.0, time=0),
                ScadaYt(pnt_no=10, name="invalid.yt", value=99.0, time=0),
                ScadaYt(pnt_no=11, name="valid.yt", value=12.0, time=1),
            ]
        )
    )
    server = ThreadingRtuServer(("127.0.0.1", 0), db)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        response = exchange(
            "127.0.0.1",
            server.server_address[1],
            {
                "rtu_id": 1,
                "simu_time": 1,
                "yc": [{"pnt_no": 1, "name": "simu.wind", "value": 6.5}],
                "yx": [],
                "last_yt_time": -1,
                "last_yk_time": 0,
            },
        )
        assert response["ok"] is True
        assert response["server_time"] > 0
        assert [row["pnt_no"] for row in response["yt"]] == [11]
        with db.session() as session:
            assert session.get(ScadaYc, 1).time == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
