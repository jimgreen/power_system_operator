from __future__ import annotations

import argparse
import json
import logging
import socket
import time
from pathlib import Path


def exchange(host: str, port: int, request: dict, timeout: float = 5.0) -> dict:
    payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(payload)
        response_file = sock.makefile("rb")
        line = response_file.readline(2 * 1024 * 1024)
    if not line:
        raise ConnectionError("operator_io 未返回数据")
    response = json.loads(line.decode("utf-8"))
    if not response.get("ok"):
        raise RuntimeError(response.get("error", "operator_io 返回失败"))
    return response


def load_measurements(path: str | None) -> dict:
    if not path:
        return {"yc": [], "yx": []}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("测量文件必须是 JSON 对象")
    return {"yc": data.get("yc", []), "yx": data.get("yx", [])}


def main() -> None:
    parser = argparse.ArgumentParser(description="operator_io 示例 RTU 周期客户端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--rtu-id", type=int, default=1)
    parser.add_argument("--period", type=float, default=1.0)
    parser.add_argument("--measurements", help="含 yc/yx 数组的 UTF-8 JSON 文件")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    last_yt_time = 0
    last_yk_time = 0
    simu_time = 0
    while True:
        started = time.monotonic()
        measurements = load_measurements(args.measurements)
        simu_time += max(1, int(round(args.period)))
        request = {
            "rtu_id": args.rtu_id,
            "simu_time": simu_time,
            "yc": measurements["yc"],
            "yx": measurements["yx"],
            "last_yt_time": last_yt_time,
            "last_yk_time": last_yk_time,
        }
        try:
            response = exchange(args.host, args.port, request)
            if response["yt"] or response["yk"]:
                print(json.dumps({"yt": response["yt"], "yk": response["yk"]}, ensure_ascii=False))
            simu_time = max(simu_time, int(response["simu_time"]))
            last_yt_time = max([last_yt_time, *[row["time"] for row in response["yt"]]])
            last_yk_time = max([last_yk_time, *[row["time"] for row in response["yk"]]])
        except (OSError, ValueError, RuntimeError) as exc:
            logging.error("RTU 交换失败: %s", exc)
        if args.once:
            break
        time.sleep(max(0.0, args.period - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
