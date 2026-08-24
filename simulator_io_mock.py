from __future__ import annotations

import argparse
import json
import logging
import math
import socketserver
import threading
from typing import Any

LOGGER = logging.getLogger(__name__)


class SimulatorState:
    """Small in-memory peer used to demonstrate the operator_io bridge."""

    def __init__(
        self,
        *,
        run_seq: int = 1,
        simu_status: int = 1,
        simu_time_start: int = 0,
        runtime_ready: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._setpoints: dict[str, float] = {}
        self._statuses: dict[str, int] = {}
        self.run_seq = max(0, int(run_seq))
        self.simu_status = int(simu_status)
        self.simu_time_start = max(0, int(simu_time_start))
        self.runtime_ready = bool(runtime_ready)
        self.simu_time_curr = self.simu_time_start

    def _read_response(self, request: dict[str, Any]) -> dict[str, Any]:
        current_time = max(0, int(request.get("simu_time", 0)))
        requested_data = request.get("data")
        if requested_data is not None and not isinstance(requested_data, dict):
            raise ValueError("data 必须是对象")

        def requested_points(alias: str) -> list[int] | None:
            if requested_data is None:
                return None
            values = requested_data.get(alias, [])
            if not isinstance(values, list):
                raise ValueError(f"{alias} 必须是点号数组")
            return [int(value) for value in values]

        requested_yc = requested_points("yc")
        requested_yx = requested_points("yx")
        day_angle = 2.0 * math.pi * (current_time % 86_400) / 86_400.0
        wind_speed = round(8.5 + 1.5 * math.sin(day_angle), 3)
        irradiance = round(max(0.0, 780.0 * math.sin(math.pi * (current_time % 86_400) / 86_400.0)), 3)
        ambient_temperature = round(20.0 + 6.0 * math.sin(day_angle - math.pi / 2.0), 3)
        with self._lock:
            self.simu_time_curr = current_time
            run_seq = self.run_seq
            simu_status = self.simu_status
            simu_time_start = self.simu_time_start
            runtime_ready = self.runtime_ready
            values = {
                "dev_diesal_gen.1.p_curr": self._setpoints.get(
                    "dev_diesal_gen.1.p_set", 25.0
                ),
                "dev_diesal_gen.2.p_curr": self._setpoints.get(
                    "dev_diesal_gen.2.p_set", 18.0
                ),
                "dev_wind_gen.1.p_curr": self._setpoints.get(
                    "dev_wind_gen.1.p_set", 35.0
                ),
                "dev_solar_gen.1.p_curr": self._setpoints.get(
                    "dev_solar_gen.1.p_set", 40.0
                ),
                "dev_estore.1.p_curr": self._setpoints.get("dev_estore.1.p_set", 0.0),
                "dev_estore.1.soc_curr": 0.55,
                "dev_load.1.p_curr": 145.0,
            }
            statuses = {
                name: self._statuses.get(name, 1)
                for name in (
                    "dev_diesal_gen.1.status",
                    "dev_diesal_gen.2.status",
                    "dev_wind_gen.1.status",
                    "dev_solar_gen.1.status",
                    "dev_estore.1.status",
                    "dev_load.1.status",
                )
            }
        yc_rows = [
            {"pnt_no": 1, "name": "simu.wind", "value": wind_speed},
            {"pnt_no": 2, "name": "simu.solar", "value": irradiance},
            {"pnt_no": 3, "name": "amb_temp", "value": ambient_temperature},
        ]
        point_numbers = {
            "dev_diesal_gen.1.p_curr": 1001,
            "dev_diesal_gen.2.p_curr": 1002,
            "dev_wind_gen.1.p_curr": 2001,
            "dev_solar_gen.1.p_curr": 3001,
            "dev_estore.1.p_curr": 4001,
            "dev_estore.1.soc_curr": 4002,
            "dev_load.1.p_curr": 5001,
        }
        yc_rows.extend(
            {"pnt_no": point_numbers[name], "name": name, "value": value}
            for name, value in values.items()
        )
        yx_rows = [
            {"pnt_no": 1001, "name": "dev_diesal_gen.1.status", "value": statuses["dev_diesal_gen.1.status"]},
            {"pnt_no": 1002, "name": "dev_diesal_gen.2.status", "value": statuses["dev_diesal_gen.2.status"]},
            {"pnt_no": 2001, "name": "dev_wind_gen.1.status", "value": statuses["dev_wind_gen.1.status"]},
            {"pnt_no": 3001, "name": "dev_solar_gen.1.status", "value": statuses["dev_solar_gen.1.status"]},
            {"pnt_no": 4001, "name": "dev_estore.1.status", "value": statuses["dev_estore.1.status"]},
            {"pnt_no": 5001, "name": "dev_load.1.status", "value": statuses["dev_load.1.status"]},
        ]
        def value_time_rows(
            alias: str,
            rows: list[dict[str, Any]],
            point_numbers: list[int] | None,
        ) -> list[dict[str, Any]]:
            rows_by_point = {int(row["pnt_no"]): row for row in rows}
            if point_numbers is not None:
                unknown = sorted(set(point_numbers) - rows_by_point.keys())
                if unknown:
                    LOGGER.warning(
                        "收到未知 %s 点号，已按原请求位置返回 value=null,time=0：pnt_nos=%s",
                        alias.upper(),
                        ",".join(str(point_number) for point_number in unknown),
                    )
            ordered_points = (
                sorted(rows_by_point) if point_numbers is None else point_numbers
            )
            return [
                {
                    "value": (
                        rows_by_point[point_number]["value"]
                        if point_number in rows_by_point
                        else None
                    ),
                    "time": current_time if point_number in rows_by_point else 0,
                }
                for point_number in ordered_points
            ]

        return {
            "ok": True,
            "run_seq": run_seq,
            "simu_status": simu_status,
            "simu_time_start": simu_time_start,
            "runtime_ready": runtime_ready,
            "simu_time": current_time,
            "data": {
                "yc": value_time_rows("yc", yc_rows, requested_yc),
                "yx": value_time_rows("yx", yx_rows, requested_yx),
            },
        }

    def _write_response(self, request: dict[str, Any]) -> dict[str, Any]:
        data = request.get("data")
        if not isinstance(data, dict):
            raise ValueError("data 必须是对象")
        yt_rows = data.get("yt", [])
        yk_rows = data.get("yk", [])
        if not isinstance(yt_rows, list) or not isinstance(yk_rows, list):
            raise ValueError("yt/yk 必须是数组")
        valid_yt_rows = self._valid_command_rows(yt_rows)
        valid_yk_rows = self._valid_command_rows(yk_rows)
        with self._lock:
            request_run_seq = request.get("run_seq")
            if request_run_seq is not None:
                if (
                    isinstance(request_run_seq, bool)
                    or not isinstance(request_run_seq, int)
                    or request_run_seq < 0
                ):
                    raise ValueError("run_seq 必须是非负整数")
                if request_run_seq != self.run_seq:
                    return {
                        "ok": False,
                        "error": (
                            "控制指令 run_seq 与当前任务不一致："
                            f"request={request_run_seq}, current={self.run_seq}"
                        ),
                        "run_seq": self.run_seq,
                        "simu_time": self.simu_time_curr,
                    }
            for row in valid_yt_rows:
                self._setpoints[str(row["name"])] = float(row["value"])
            for row in valid_yk_rows:
                self._statuses[str(row["name"])] = int(row["value"])
        LOGGER.info(
            "收到有效控制命令：YT=%d，YK=%d",
            len(valid_yt_rows),
            len(valid_yk_rows),
        )
        return {
            "ok": True,
            "run_seq": self.run_seq,
            "simu_time": self.simu_time_curr,
            "accepted_yt": len(valid_yt_rows),
            "accepted_yk": len(valid_yk_rows),
        }

    @staticmethod
    def _valid_command_rows(rows: list[Any]) -> list[dict[str, Any]]:
        valid: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("YT/YK 数据项必须是对象")
            try:
                point_time = int(row.get("time", 0))
            except (TypeError, ValueError) as exc:
                raise ValueError("YT/YK time 必须是整数") from exc
            if point_time > 0:
                valid.append(row)
        return valid

    def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if action == "read":
            return self._read_response(request)
        if action == "write":
            return self._write_response(request)
        if action == "ping":
            return {"ok": True, "service": "simulator_io"}
        raise ValueError("action 必须是 read、write 或 ping")


class SimulatorRequestHandler(socketserver.StreamRequestHandler):
    max_line_bytes = 2 * 1024 * 1024

    def handle(self) -> None:
        line = self.rfile.readline(self.max_line_bytes + 1)
        try:
            if len(line) > self.max_line_bytes:
                raise ValueError("请求过大")
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("请求必须是 JSON 对象")
            response = self.server.state.handle(request)  # type: ignore[attr-defined]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")


class ThreadingSimulatorServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: SimulatorState | None = None):
        self.state = state or SimulatorState()
        super().__init__(address, SimulatorRequestHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description="simulator_io JSON Lines Mock 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9200)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    with ThreadingSimulatorServer((args.host, args.port)) as server:
        LOGGER.info("simulator_io Mock 正在监听 %s:%d", args.host, args.port)
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            LOGGER.info("simulator_io Mock 已停止")


if __name__ == "__main__":
    main()
