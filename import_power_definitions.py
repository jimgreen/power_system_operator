from __future__ import annotations

import argparse

from power_operator.definition_import import import_power_definitions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从只读旧 power.db 复制设备和 SCADA 定义到 ems.db"
    )
    parser.add_argument("--source", required=True, help="已有 power.db 路径")
    parser.add_argument("--target", default="ems.db", help="目标 ems.db 路径")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="原子替换目标库中 10 张设备/SCADA 定义表的内容",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    report = import_power_definitions(args.source, args.target, replace=args.replace)
    print(f"源库（只读）: {report.source_path}")
    print(f"目标库: {report.target_path}")
    print(f"源库导入前 SHA-256: {report.source_sha256_before or '占用中，无法直接读取'}")
    print(f"源库导入后 SHA-256: {report.source_sha256_after or '占用中，无法直接读取'}")
    if report.source_unchanged is False:
        print("提示：只读快照期间源库被外部进程更新；导入内容仍来自同一个一致读事务。")
    elif report.source_unchanged is None:
        print("提示：Windows 文件共享占用导致无法比较哈希；SQLite 连接已强制 mode=ro 和 query_only。")
    else:
        print("源库文件哈希未变化。")
    for table_name, table in report.tables.items():
        ignored = ", ".join(table.ignored_source_columns) or "无"
        print(
            f"{table_name}: 源 {table.source_count} 条，目标 {table.target_count} 条，"
            f"忽略源端多余列: {ignored}"
        )
    print("设备和 SCADA 定义复制及逐列校验完成。")


if __name__ == "__main__":
    main()
