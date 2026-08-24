from __future__ import annotations

import argparse
import logging
from pathlib import Path

from power_operator.core import OperatorCore
from power_operator.core_process import core_pid_file, default_core_pid_path
from power_operator.database import Database, initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description="电力系统操作员计算内核")
    parser.add_argument("--db", default="ems.db", help="SQLite 数据库文件，默认 ems.db")
    parser.add_argument("--poll", type=float, default=0.5, help="控制表轮询周期（秒）")
    parser.add_argument("--once", action="store_true", help="立即执行一个周期并退出（测试/运维用）")
    parser.add_argument(
        "--pid-file",
        default=None,
        help="Core PID 文件；默认按数据库路径生成到 .runtime",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    database = Database(args.db)
    initialize_database(database)
    core = OperatorCore(database)
    pid_file = Path(args.pid_file) if args.pid_file else default_core_pid_path(database.path)
    try:
        with core_pid_file(
            pid_file,
            core_script=Path(__file__).resolve(),
            database_path=database.path,
        ):
            if args.once:
                print(core.run_cycle())
            else:
                try:
                    core.run_forever(max(0.05, args.poll))
                except KeyboardInterrupt:
                    logging.info("operator_core 已停止")
    finally:
        database.dispose()


if __name__ == "__main__":
    main()
