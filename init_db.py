from __future__ import annotations

import argparse

from power_operator.database import Database, initialize_database


def main() -> None:
    parser = argparse.ArgumentParser(description="创建/升级 EMS SQLite 数据库")
    parser.add_argument("--db", default="ems.db", help="SQLite 数据库文件，默认 ems.db")
    args = parser.parse_args()
    database = Database(args.db)
    initialize_database(database)
    print(f"数据库已就绪: {database.path}")


if __name__ == "__main__":
    main()
