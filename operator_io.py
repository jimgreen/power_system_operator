from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

from power_operator.core_process import CoreProcessManager
from power_operator.database import Database, initialize_database
from power_operator.io_service import OperatorIoBridge, SimulatorIoClient, ThreadingRtuServer


def main() -> None:
    parser = argparse.ArgumentParser(description="operator_io 数据和控制桥接进程")
    parser.add_argument("--db", default="ems.db", help="SQLite 数据库文件，默认 ems.db")
    parser.add_argument("--mode", choices=("bridge", "server"), default="bridge")
    parser.add_argument("--simulator-host", default="127.0.0.1")
    parser.add_argument("--simulator-port", type=int, default=9001)
    parser.add_argument("--rtu-id", type=int, default=1)
    parser.add_argument("--poll", type=float, default=0.5)
    parser.add_argument("--core-poll", type=float, default=0.5)
    parser.add_argument("--core-pid-file", default=None)
    parser.add_argument("--core-stop-timeout", type=float, default=10.0)
    parser.add_argument("--core-start-timeout", type=float, default=10.0)
    parser.add_argument("--listen-host", default="127.0.0.1", help="兼容服务监听地址")
    parser.add_argument("--listen-port", type=int, default=9100, help="兼容服务监听端口")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    database = Database(args.db)
    initialize_database(database)
    if args.mode == "bridge":
        client = SimulatorIoClient(args.simulator_host, args.simulator_port)
        project_root = Path(__file__).resolve().parent
        core_manager = CoreProcessManager(
            database_path=database.path,
            core_script=project_root / "operator_core.py",
            python_executable=sys.executable,
            poll_seconds=args.core_poll,
            pid_file=args.core_pid_file,
            runtime_dir=project_root / ".runtime",
            stop_timeout=args.core_stop_timeout,
            start_timeout=args.core_start_timeout,
        )
        bridge = OperatorIoBridge(
            database,
            transport=client,
            rtu_id=args.rtu_id,
            peer_ip=args.simulator_host,
            peer_port=args.simulator_port,
            core_process_manager=core_manager,
        )
        try:
            bridge.run_forever(max(0.05, args.poll))
        except KeyboardInterrupt:
            logging.info("operator_io 已停止")
        finally:
            try:
                bridge.mark_disconnected()
            finally:
                database.dispose()
        return
    with ThreadingRtuServer((args.listen_host, args.listen_port), database) as server:
        logging.info(
            "operator_io 兼容服务正在监听 %s:%d，数据库 %s",
            args.listen_host,
            args.listen_port,
            database.path,
        )
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            logging.info("operator_io 已停止")
        finally:
            database.dispose()


if __name__ == "__main__":
    main()
