from __future__ import annotations

import logging
import threading

import pytest

from power_operator.io_service import SimulatorIoClient
from simulator_io_mock import SimulatorState, ThreadingSimulatorServer


def test_mock_server_supports_positional_read_and_applies_written_setpoints(caplog):
    state = SimulatorState()
    server = ThreadingSimulatorServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        client = SimulatorIoClient(host, port)
        with caplog.at_level(logging.WARNING):
            first = client.exchange(
                {
                    "action": "read",
                    "rtu_id": 1,
                    "simu_time": 3,
                    "data": {
                        "yc": [5001, 1, 999, 5001, 2, 3],
                        "yx": [5001, 999, 2001, 5001],
                    },
                }
            )
        assert first["simu_time"] == 3
        assert {
            "run_seq": first["run_seq"],
            "simu_status": first["simu_status"],
            "simu_time_start": first["simu_time_start"],
            "runtime_ready": first["runtime_ready"],
        } == {
            "run_seq": 1,
            "simu_status": 1,
            "simu_time_start": 0,
            "runtime_ready": True,
        }
        assert all(set(row) == {"value", "time"} for row in first["data"]["yc"])
        assert all(set(row) == {"value", "time"} for row in first["data"]["yx"])
        assert [row["time"] for row in first["data"]["yc"]] == [3, 3, 0, 3, 3, 3]
        assert first["data"]["yc"][0]["value"] == 145.0
        assert first["data"]["yc"][2] == {"value": None, "time": 0}
        assert first["data"]["yc"][3] == first["data"]["yc"][0]
        assert first["data"]["yx"] == [
            {"value": 1, "time": 3},
            {"value": None, "time": 0},
            {"value": 1, "time": 3},
            {"value": 1, "time": 3},
        ]
        assert "未知 YC 点号" in caplog.text
        assert "未知 YX 点号" in caplog.text

        zero_time = client.exchange(
            {
                "action": "read",
                "rtu_id": 1,
                "simu_time": 0,
                "data": {"yc": [5001, 999], "yx": [2001, 999]},
            }
        )
        assert zero_time["data"] == {
            "yc": [
                {"value": 145.0, "time": 0},
                {"value": None, "time": 0},
            ],
            "yx": [
                {"value": 1, "time": 0},
                {"value": None, "time": 0},
            ],
        }

        reply = client.exchange(
            {
                "action": "write",
                "run_seq": first["run_seq"],
                "data": {
                    "yt": [
                        {
                            "pnt_no": 100001,
                            "name": "dev_diesal_gen.1.p_set",
                            "value": 99.0,
                            "time": 0,
                        },
                        {
                            "pnt_no": 200001,
                            "name": "dev_wind_gen.1.p_set",
                            "value": 42.5,
                            "time": 3,
                        },
                    ],
                    "yk": [
                        {
                            "pnt_no": 200001,
                            "name": "dev_wind_gen.1.status",
                            "value": 0,
                            "time": 0,
                        }
                    ],
                },
            }
        )
        assert reply == {
            "ok": True,
            "run_seq": 1,
            "simu_time": 0,
            "accepted_yt": 1,
            "accepted_yk": 0,
        }

        with pytest.raises(RuntimeError, match="request=0, current=1"):
            client.exchange(
                {
                    "action": "write",
                    "run_seq": 0,
                    "data": {"yt": [], "yk": []},
                }
            )

        second = client.exchange(
            {
                "action": "read",
                "rtu_id": 1,
                "simu_time": 4,
                "data": {"yc": [2001, 1001], "yx": []},
            }
        )
        assert second["data"]["yc"] == [
            {"value": 42.5, "time": 4},
            {"value": 25.0, "time": 4},
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
