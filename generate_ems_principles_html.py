from __future__ import annotations

import argparse
import html
import sqlite3
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = "power_system_operator_ems_principles_and_scada_points.html"

POINT_TABLES = (
    ("scada_yc", "YC 遥测", "模拟器 → EMS", "连续量"),
    ("scada_yx", "YX 遥信", "模拟器 → EMS", "状态量"),
    ("scada_yt", "YT 遥调", "EMS → 模拟器", "连续设定"),
    ("scada_yk", "YK 遥控", "EMS → 模拟器", "离散命令"),
)


def format_simu_time(value: Any) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "--"
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_value(table: str, value: Any) -> str:
    if value is None:
        return "--"
    if table in {"scada_yx", "scada_yk"}:
        try:
            return str(int(value))
        except (TypeError, ValueError):
            return html.escape(str(value))
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return html.escape(str(value))


def point_area(point_no: int, name: str) -> str:
    if point_no < 100:
        return "环境"
    prefix = point_no // 100000
    if prefix == 1:
        return "柴油发电"
    if prefix == 2:
        return "风力发电"
    if prefix == 3:
        return "光伏发电"
    if prefix == 4:
        return "负荷"
    if prefix == 5:
        return "储能"
    if "环境" in name:
        return "环境"
    return "其他"


def point_semantics(table: str, name: str) -> tuple[str, str]:
    suffixes = (
        ("当前太阳辐照", "太阳辐照", "W/m²"),
        ("当前环境温度", "环境温度", "°C"),
        ("当前风速", "环境风速", "m/s"),
        ("当前桨距角", "桨距角量测", "°"),
        ("当前SOC", "储能荷电状态", "p.u."),
        ("当前负荷", "负荷有功", "kW"),
        ("当前功率", "储能有功；正值放电、负值充电", "kW"),
        ("当前有功", "设备当前有功", "kW"),
        ("有功出力设定", "柴油机有功设定", "kW"),
        ("功率设定", "设备有功设定", "kW"),
        ("运行状态", "设备运行状态；1 运行、0 停止", "0/1"),
        ("控制模式", "设备控制模式；1 闭环、0 开环", "0/1"),
        ("启停命令", "设备目标状态；1 启动、0 停止", "0/1"),
    )
    for suffix, meaning, unit in suffixes:
        if name.endswith(suffix):
            return meaning, unit
    fallback = {
        "scada_yc": ("连续量量测", "工程量"),
        "scada_yx": ("离散状态量", "整数"),
        "scada_yt": ("连续控制设定", "工程量"),
        "scada_yk": ("离散控制命令", "整数"),
    }
    return fallback[table]


def project_version(root: Path) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def load_database(db_path: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        if integrity.lower() != "ok":
            raise RuntimeError(f"数据库完整性检查失败：{integrity}")
        points: dict[str, list[dict[str, Any]]] = {}
        for table, _label, _direction, _kind in POINT_TABLES:
            points[table] = [
                dict(row)
                for row in connection.execute(
                    f"SELECT pnt_no, name, value, time FROM {table} ORDER BY pnt_no"
                )
            ]
        control_row = connection.execute(
            "SELECT oper_status, control_status, data_period, oper_period, "
            "data_time_curr, oper_time_curr FROM operator_control WHERE id=1"
        ).fetchone()
        control = dict(control_row) if control_row is not None else {}
        rtu_row = connection.execute(
            "SELECT id, ip, port, status, refresh_time FROM scada_rtu ORDER BY id LIMIT 1"
        ).fetchone()
        rtu = dict(rtu_row) if rtu_row is not None else {}
        return points, {
            "integrity": integrity,
            "control": control,
            "rtu": rtu,
        }
    finally:
        connection.close()


def render_point_tables(points: dict[str, list[dict[str, Any]]]) -> str:
    sections: list[str] = []
    for table, label, direction, kind in POINT_TABLES:
        rows = points[table]
        body: list[str] = []
        for row in rows:
            point_no = int(row["pnt_no"])
            name = str(row["name"])
            meaning, unit = point_semantics(table, name)
            point_time = int(row["time"])
            validity = "有效" if point_time > 0 else "无效/待命"
            validity_class = "valid" if point_time > 0 else "invalid"
            body.append(
                "<tr "
                f'data-search="{html.escape(f"{point_no} {name} {direction} {meaning} {unit} {point_area(point_no, name)}".lower())}">'
                f"<td class=\"mono\">{point_no}</td>"
                f"<td>{html.escape(name)}</td>"
                f"<td>{html.escape(direction)}</td>"
                f"<td>{html.escape(point_area(point_no, name))}</td>"
                f"<td>{html.escape(meaning)}</td>"
                f"<td>{html.escape(unit)}</td>"
                f"<td class=\"number\">{format_value(table, row['value'])}</td>"
                f"<td class=\"mono\">{format_simu_time(point_time)}</td>"
                f"<td><span class=\"state {validity_class}\">{validity}</span></td>"
                "</tr>"
            )
        sections.append(
            f"""
            <section class="point-section" id="{table}" data-kind="{table}">
              <div class="section-heading compact">
                <div>
                  <span class="eyebrow">{html.escape(table)}</span>
                  <h3>{html.escape(label)}</h3>
                  <p>{html.escape(direction)} · {html.escape(kind)} · 共 {len(rows)} 点</p>
                </div>
                <span class="count-chip">{len(rows)} points</span>
              </div>
              <div class="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>点号</th>
                      <th>点名</th>
                      <th>数据方向</th>
                      <th>对象域</th>
                      <th>业务含义</th>
                      <th>单位/编码</th>
                      <th>当前值</th>
                      <th>运行时标</th>
                      <th>有效性</th>
                    </tr>
                  </thead>
                  <tbody>
                    {''.join(body)}
                  </tbody>
                </table>
              </div>
            </section>
            """
        )
    return "\n".join(sections)


def render_html(
    root: Path,
    db_path: Path,
    points: dict[str, list[dict[str, Any]]],
    metadata: dict[str, Any],
) -> str:
    counts = {table: len(points[table]) for table, *_rest in POINT_TABLES}
    total_points = sum(counts.values())
    control = metadata["control"]
    rtu = metadata["rtu"]
    generated_at = datetime.now().astimezone()
    version = project_version(root)
    database_time = datetime.fromtimestamp(db_path.stat().st_mtime).astimezone()
    point_tables = render_point_tables(points)

    template = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='12' fill='%23156677'/%3E%3Cpath d='M36 6 15 35h14l-2 23 22-32H35z' fill='%23fff'/%3E%3C/svg%3E">
  <title>电网 EMS 功能原理、算法流程与四遥点位</title>
  <style>
    :root {
      --ink: #17263d;
      --muted: #617087;
      --line: #d8e1ed;
      --soft: #f3f7fc;
      --panel: #ffffff;
      --navy: #17345f;
      --blue: #2166d1;
      --cyan: #00a4b8;
      --green: #188c62;
      --amber: #bf7415;
      --red: #b83c4b;
      --shadow: 0 12px 34px rgba(28, 58, 102, .11);
      --radius: 16px;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      color: var(--ink);
      background: #eaf0f8;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif;
      line-height: 1.68;
    }
    a { color: inherit; }
    .page {
      width: min(1520px, 100%);
      margin: 0 auto;
      background: var(--soft);
      min-height: 100vh;
    }
    .hero {
      position: relative;
      overflow: hidden;
      padding: 54px clamp(24px, 5vw, 78px) 48px;
      color: #fff;
      background:
        radial-gradient(circle at 78% 15%, rgba(57, 212, 228, .26), transparent 27%),
        radial-gradient(circle at 93% 88%, rgba(74, 130, 255, .22), transparent 32%),
        linear-gradient(135deg, #10284e 0%, #174d86 55%, #08758c 100%);
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: 0;
      opacity: .12;
      background-image:
        linear-gradient(rgba(255,255,255,.18) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.18) 1px, transparent 1px);
      background-size: 44px 44px;
      pointer-events: none;
    }
    .hero-inner { position: relative; z-index: 1; max-width: 1120px; }
    .hero .kicker {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      padding: 6px 12px;
      border: 1px solid rgba(255,255,255,.35);
      border-radius: 999px;
      background: rgba(255,255,255,.1);
      font-size: 13px;
      letter-spacing: .08em;
    }
    h1 { margin: 18px 0 12px; font-size: clamp(34px, 5vw, 64px); line-height: 1.12; }
    .hero-lead { margin: 0; max-width: 900px; color: #dcecff; font-size: 18px; }
    .hero-meta { display: flex; flex-wrap: wrap; gap: 10px 24px; margin-top: 26px; color: #d8e9ff; font-size: 13px; }
    .layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 28px; padding: 28px; }
    .toc {
      position: sticky;
      top: 18px;
      align-self: start;
      max-height: calc(100vh - 36px);
      overflow: auto;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: rgba(255,255,255,.94);
      box-shadow: var(--shadow);
    }
    .toc strong { display: block; margin-bottom: 10px; color: var(--navy); }
    .toc a {
      display: block;
      padding: 7px 10px;
      border-left: 2px solid transparent;
      color: var(--muted);
      text-decoration: none;
      font-size: 14px;
    }
    .toc a:hover { color: var(--blue); border-left-color: var(--blue); background: #f2f7ff; }
    main { min-width: 0; }
    .section {
      margin-bottom: 24px;
      padding: clamp(22px, 3vw, 38px);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      box-shadow: var(--shadow);
      scroll-margin-top: 20px;
    }
    .section-heading { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; margin-bottom: 22px; }
    .section-heading.compact { margin-bottom: 16px; }
    .eyebrow { color: var(--blue); font-size: 12px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
    h2 { margin: 4px 0 8px; color: var(--navy); font-size: clamp(25px, 3vw, 34px); line-height: 1.25; }
    h3 { margin: 4px 0 4px; color: var(--navy); font-size: 21px; }
    h4 { margin: 0 0 8px; color: var(--navy); }
    p { margin: 0 0 12px; }
    .section-heading p, .muted { color: var(--muted); }
    .grid { display: grid; gap: 16px; }
    .grid.cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .grid.cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .grid.cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .card {
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 13px;
      background: linear-gradient(180deg, #fff, #f8fbff);
    }
    .card .metric { display: block; color: var(--navy); font-size: 28px; font-weight: 800; }
    .card .label { color: var(--muted); font-size: 13px; }
    .badge, .count-chip {
      display: inline-flex;
      align-items: center;
      white-space: nowrap;
      padding: 5px 10px;
      border-radius: 999px;
      background: #e9f2ff;
      color: var(--blue);
      font-size: 12px;
      font-weight: 700;
    }
    .architecture {
      display: grid;
      grid-template-columns: 1fr 42px 1fr 42px 1.15fr 42px 1fr;
      align-items: stretch;
      gap: 10px;
      margin-top: 20px;
    }
    .node {
      display: flex;
      min-height: 126px;
      flex-direction: column;
      justify-content: center;
      padding: 18px;
      border: 1px solid #bfd0e7;
      border-radius: 14px;
      background: #f8fbff;
      text-align: center;
    }
    .node.primary { border-color: #8fb6ec; background: #edf5ff; }
    .node.database { border-color: #82ced4; background: #ecfbfb; }
    .node strong { color: var(--navy); font-size: 17px; }
    .node span { margin-top: 6px; color: var(--muted); font-size: 12px; }
    .arrow { display: grid; place-items: center; color: var(--blue); font-size: 28px; font-weight: 700; }
    .flow {
      counter-reset: steps;
      display: grid;
      gap: 12px;
      margin: 18px 0 0;
      padding: 0;
      list-style: none;
    }
    .flow li {
      counter-increment: steps;
      position: relative;
      padding: 16px 18px 16px 62px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f9fbfe;
    }
    .flow li::before {
      content: counter(steps, decimal-leading-zero);
      position: absolute;
      left: 16px;
      top: 16px;
      display: grid;
      place-items: center;
      width: 32px;
      height: 32px;
      border-radius: 9px;
      color: #fff;
      background: var(--blue);
      font-weight: 800;
      font-size: 12px;
    }
    .flow strong { color: var(--navy); }
    .decision {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }
    .decision .branch {
      padding: 18px;
      border-top: 4px solid var(--blue);
      border-radius: 11px;
      background: #f7faff;
    }
    .decision .surplus { border-top-color: var(--green); }
    .decision .deficit { border-top-color: var(--amber); }
    .decision .command { border-top-color: var(--cyan); }
    .formula {
      overflow-x: auto;
      margin: 12px 0;
      padding: 14px 16px;
      border-left: 4px solid var(--blue);
      background: #f2f6fb;
      color: #19345a;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 14px;
      white-space: nowrap;
    }
    .callout {
      margin: 16px 0;
      padding: 14px 16px;
      border: 1px solid #b8d7ff;
      border-radius: 11px;
      background: #eef6ff;
    }
    .callout.warning { border-color: #efca86; background: #fff8e9; }
    .callout strong { color: var(--navy); }
    .legend { display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; }
    .legend span { padding: 5px 9px; border: 1px solid var(--line); border-radius: 8px; background: #fff; font-size: 12px; }
    .filter-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin: 16px 0 22px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f7faff;
    }
    .filter-bar input {
      flex: 1 1 280px;
      min-width: 0;
      padding: 10px 12px;
      border: 1px solid #bfcde0;
      border-radius: 9px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    .filter-bar button {
      padding: 9px 12px;
      border: 1px solid #bfcde0;
      border-radius: 9px;
      background: #fff;
      color: var(--navy);
      cursor: pointer;
      font: inherit;
      font-size: 13px;
    }
    .filter-bar button.active { border-color: var(--blue); color: #fff; background: var(--blue); }
    .point-section { margin-top: 24px; scroll-margin-top: 20px; }
    .table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }
    table { width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; }
    th, td { padding: 10px 11px; border-bottom: 1px solid #e1e7f0; text-align: left; vertical-align: top; }
    th { position: sticky; top: 0; z-index: 1; color: #fff; background: var(--navy); white-space: nowrap; }
    tbody tr:nth-child(even) { background: #f7f9fc; }
    tbody tr:hover { background: #edf5ff; }
    td.number { text-align: right; font-variant-numeric: tabular-nums; }
    .mono { font-family: Consolas, "Cascadia Mono", monospace; font-variant-numeric: tabular-nums; }
    .state { display: inline-block; padding: 3px 7px; border-radius: 999px; font-size: 11px; font-weight: 700; white-space: nowrap; }
    .state.valid { color: #0f6f4b; background: #dff5e9; }
    .state.invalid { color: #9c6013; background: #fff0d1; }
    tr.hidden-row, .point-section.hidden-section { display: none; }
    .checklist { margin: 0; padding-left: 20px; }
    .checklist li { margin: 7px 0; }
    footer {
      padding: 30px clamp(24px, 5vw, 78px) 46px;
      color: #5f6f84;
      font-size: 12px;
    }
    @media (max-width: 1100px) {
      .layout { grid-template-columns: 1fr; }
      .toc { position: static; max-height: none; columns: 2; }
      .toc strong { column-span: all; }
      .architecture { grid-template-columns: 1fr; }
      .arrow { transform: rotate(90deg); min-height: 28px; }
      .grid.cols-4, .grid.cols-3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 680px) {
      .hero { padding: 38px 20px; }
      .layout { padding: 14px; }
      .section { padding: 20px 16px; }
      .toc { columns: 1; }
      .grid.cols-4, .grid.cols-3, .grid.cols-2, .decision { grid-template-columns: 1fr; }
      .section-heading { flex-direction: column; }
    }
    @media print {
      @page { size: A4; margin: 13mm; }
      body, .page { background: #fff; }
      .hero { padding: 22px 24px; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
      .layout { display: block; padding: 0; }
      .toc, .filter-bar { display: none; }
      .section { margin: 0 0 12px; padding: 14px; border-radius: 0; box-shadow: none; break-inside: auto; }
      .card, .node, .branch, .flow li, .callout, .formula, table { break-inside: avoid; }
      .point-section { break-before: page; }
      th { position: static; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
      a { text-decoration: none; }
      footer { padding: 14px 0; }
    }
  </style>
</head>
<body>
<div class="page">
  <header class="hero">
    <div class="hero-inner">
      <span class="kicker">POWER SYSTEM OPERATOR · EMS REFERENCE</span>
      <h1>电网 EMS 功能原理、<br>算法流程与四遥点位</h1>
      <p class="hero-lead">依据当前运行代码与正式 ems.db 生成的工程参考文档。它把量测接入、设备状态映射、新能源优先调度、闭环命令、时间语义、异常恢复和全部四遥点位放在同一条可追溯链路中。</p>
      <div class="hero-meta">
        <span>软件版本：@@VERSION@@</span>
        <span>生成时刻：@@GENERATED_AT@@</span>
        <span>数据源：@@DB_NAME@@（只读快照）</span>
      </div>
    </div>
  </header>

  <div class="layout">
    <nav class="toc" aria-label="文档目录">
      <strong>文档目录</strong>
      <a href="#overview">1. 功能定位</a>
      <a href="#architecture">2. 系统架构</a>
      <a href="#timing">3. 周期与时间语义</a>
      <a href="#data-flow">4. 数据处理流程</a>
      <a href="#algorithm">5. 新能源优先算法</a>
      <a href="#commands">6. YT/YK 生成与闭环</a>
      <a href="#audit">7. 历史与审计</a>
      <a href="#resilience">8. 并发与异常恢复</a>
      <a href="#points">9. 四遥点位总表</a>
      <a href="#operations">10. 运行核对清单</a>
    </nav>

    <main>
      <section class="section" id="overview">
        <div class="section-heading">
          <div>
            <span class="eyebrow">01 · CAPABILITIES</span>
            <h2>功能定位</h2>
            <p>EMS 以 SQLite/SQLAlchemy 为状态总线，由唯一 MMI 宿主托管 Core 与 IO 两个工作线程，完成从电网量测到控制策略下发的闭环。</p>
          </div>
          <span class="badge">数据库完整性：@@INTEGRITY@@</span>
        </div>
        <div class="grid cols-4">
          <article class="card"><span class="metric">@@TOTAL_POINTS@@</span><span class="label">当前四遥定义总点数</span></article>
          <article class="card"><span class="metric">@@DATA_PERIOD@@ s</span><span class="label">数据采集周期 data_period</span></article>
          <article class="card"><span class="metric">@@OPER_PERIOD@@ s</span><span class="label">控制决策周期 oper_period</span></article>
          <article class="card"><span class="metric">@@DATA_TIME@@</span><span class="label">当前数据运行时刻</span></article>
        </div>
        <div class="grid cols-3" style="margin-top:16px">
          <article class="card"><h4>量测汇聚</h4><p>按模拟器返回顺序映射 YC/YX，仅响应 <code>time &gt; 0</code> 的点；未知点占位并记录告警，不修改本地点名。</p></article>
          <article class="card"><h4>实时策略</h4><p>更新设备状态、控制模式和实时功率，计算风光最大可用功率，执行新能源优先、柴发约束、储能和弃电/失供策略。</p></article>
          <article class="card"><h4>控制与审计</h4><p>闭环时刷新预定义 YT，仅在目标状态与实时状态不一致时生成 YK；输入、过程、输出和平衡校验写入完整决策日志。</p></article>
        </div>
      </section>

      <section class="section" id="architecture">
        <div class="section-heading">
          <div>
            <span class="eyebrow">02 · ARCHITECTURE</span>
            <h2>系统架构与数据闭环</h2>
            <p>网络调用不持有数据库事务；每个长期线程使用独立 SQLAlchemy Engine/Session。</p>
          </div>
        </div>
        <div class="architecture" aria-label="EMS 架构流程">
          <div class="node"><strong>电网模拟器</strong><span>运行时钟 · YC/YX · YT/YK 执行</span></div>
          <div class="arrow">⇄</div>
          <div class="node primary"><strong>IO 工作线程</strong><span>TCP JSON Lines · 位置映射 · 断线重连</span></div>
          <div class="arrow">⇄</div>
          <div class="node database"><strong>ems.db</strong><span>设备、四遥、控制、历史与审计状态总线</span></div>
          <div class="arrow">⇄</div>
          <div class="node primary"><strong>Core + MMI</strong><span>数据刷新 · 策略计算 · 人机监视与操作</span></div>
        </div>
        <div class="callout">
          <strong>默认运行形态：</strong>只启动一个 operator_mmi 宿主；它自动启动 Core、IO 两个受管线程。独立 operator_core/operator_io 入口仅用于测试或显式兼容运维，不能与默认托管模式重复运行。
        </div>
      </section>

      <section class="section" id="timing">
        <div class="section-heading">
          <div>
            <span class="eyebrow">03 · TIME SEMANTICS</span>
            <h2>周期与时间语义</h2>
            <p>系统同时使用墙钟、单调墙钟和运行累计秒；三者不可混用。</p>
          </div>
        </div>
        <div class="grid cols-3">
          <article class="card"><h4>0.5 秒控制检查</h4><p>Core 常驻循环每 0.5 秒读取 operator_control，用于快速识别启动、停止、暂停和模式变化。</p></article>
          <article class="card"><h4>数据周期</h4><p>IO 按墙钟 data_period 读取 YC/YX；成功包携带权威运行时刻，Core 对新断面执行量测映射和历史记录。</p></article>
          <article class="card"><h4>决策周期</h4><p>Core 按单调墙钟 oper_period 触发一次策略，使用当时最新 data_time_curr；不因运行时钟跨越多个点而密集补跑。</p></article>
        </div>
        <div class="formula">四遥 time / data_time_curr / oper_time_curr = 运行累计秒 → 显示 HH:mm:ss（允许超过 24 小时）</div>
        <div class="formula">scada_rtu.refresh_time / operator_log.log_time = Unix 墙钟秒 → 显示 yyyy-MM-dd HH:mm:ss</div>
      </section>

      <section class="section" id="data-flow">
        <div class="section-heading">
          <div>
            <span class="eyebrow">04 · DATA PIPELINE</span>
            <h2>量测接入与设备状态更新</h2>
            <p>数据包的位置协议、有效性边界和本地定义保护共同保证点位不会错位或被远端重定义。</p>
          </div>
        </div>
        <ol class="flow">
          <li><strong>按原请求构造点号列表。</strong> YC/YX 请求允许乱序和重复；响应只返回 value/time，并严格保持请求位置，不排序、不去重。</li>
          <li><strong>恢复本地点号。</strong> IO 根据请求数组位置映射响应。未知点以 value=null、time=0 占位并写 WARNING；数组长度不一致时拒绝整批。</li>
          <li><strong>执行有效性边界。</strong> 只有 time&gt;0 的 YC/YX 才更新本地 value/time；Core/IO 只修改 value/time，不修改 pnt_no、name 等定义字段。</li>
          <li><strong>更新设备实时字段。</strong> YC 写 p_curr、桨距角、SOC 等量测；YX 实时覆盖设备 status 和 control_mode。相同业务字段存在多个候选点时，以更新时刻和点号排序选择最新定义。</li>
          <li><strong>计算理论可用功率。</strong> 风机 p_max_curr 只由风速和风机参数调用独立函数计算；光伏按辐照与额定功率计算，停机设备可用功率为 0。</li>
          <li><strong>形成断面。</strong> 写 operator_history，并把 time&gt;0 的 YC/YX/YT/YK 切面保存到对应历史表；无记录数或查询条数上限。</li>
        </ol>
      </section>

      <section class="section" id="algorithm">
        <div class="section-heading">
          <div>
            <span class="eyebrow">05 · DISPATCH</span>
            <h2>新能源优先有功调度算法</h2>
            <p>储能正功率表示放电，负功率表示充电；开环设备作为不可调固定贡献，闭环设备才进入 EMS 分配。</p>
          </div>
        </div>
        <h3>5.1 风光理论最大出力</h3>
        <div class="formula">风机：v &lt; v_in 或 v ≥ v_cut → 0；v_in ≤ v &lt; v_rated → P_rated × (v³-v_in³)/(v_rated³-v_in³)；v_rated ≤ v &lt; v_cut → P_rated</div>
        <div class="formula">光伏：P_max = clamp(P_rated × max(0, solar_radiation) / 1000, 0, P_rated)</div>
        <p class="muted">风机参数非法、非有限或阈值顺序错误时返回 0，避免 NaN/Inf 进入策略。</p>

        <h3>5.2 储能本周期可充/可放上限</h3>
        <div class="formula">P_charge_limit = min(P_charge_max, (SOC_max-SOC)×E_capacity / (Δt_hour×η_charge))</div>
        <div class="formula">P_discharge_limit = min(P_discharge_max, (SOC-SOC_min)×E_capacity×η_discharge / Δt_hour)</div>

        <h3>5.3 策略分支</h3>
        <div class="decision">
          <article class="branch">
            <h4>共同前置</h4>
            <p>先扣除开环运行设备的实时固定功率；闭环新能源按可用功率进入调度，运行柴发保持不低于 p_min。</p>
          </article>
          <article class="branch surplus">
            <h4>供给富余</h4>
            <p>新能源 + 柴发下限 ≥ 剩余负荷：柴发保持下限 → 储能充电吸收富余 → 仍有富余则削减新能源。</p>
          </article>
          <article class="branch deficit">
            <h4>供给不足</h4>
            <p>新能源 + 柴发下限 &lt; 剩余负荷：新能源全用 → 柴发增发至上限 → 储能放电 → 仍不足则记录未供电量。</p>
          </article>
        </div>
        <ol class="flow">
          <li><strong>新能源优先：</strong>汇总风电、光伏理论可用功率，先纳入负荷平衡。</li>
          <li><strong>柴发下限：</strong>所有可控运行柴油机至少保持各自 p_min，分配不超过 p_max。</li>
          <li><strong>储能充电：</strong>存在富余时按设备顺序吸收，受功率、效率、容量和 SOC_max 限制。</li>
          <li><strong>新能源削减：</strong>储能吸收后仍富余时形成 curtailment_kw；当前实现不可避免削减时先使用风电，再分配光伏。</li>
          <li><strong>柴发增发：</strong>新能源不足时在柴发下限基础上增加出力，直至 p_max 或覆盖缺额。</li>
          <li><strong>储能放电：</strong>柴发增发后仍有缺额时放电，受功率、效率、能量和 SOC_min 限制。</li>
          <li><strong>失供与平衡：</strong>剩余缺额记为 unserved_kw，并计算平衡误差与结构化告警。</li>
          <li><strong>设备状态判定：</strong>比较实时 YX 状态和目标状态，仅差异设备进入 YK 策略。</li>
        </ol>
        <div class="formula">balance_error = wind_set + solar_set + diesel_set + storage_set + unserved - overgeneration - load</div>
      </section>

      <section class="section" id="commands">
        <div class="section-heading">
          <div>
            <span class="eyebrow">06 · CLOSED LOOP</span>
            <h2>遥调 / 遥控生成与下发边界</h2>
            <p>预定义点位是唯一控制出口；Core 和 IO 都不创建新点，也不复写点名。</p>
          </div>
        </div>
        <div class="grid cols-2">
          <article class="card">
            <h4>YT 遥调</h4>
            <ul class="checklist">
              <li>仅当全局 control_status=闭环且设备 control_mode=闭环时生成。</li>
              <li>使用系统预定义功率设定点，每个闭环决策周期都刷新 value/time，即使值未变化。</li>
              <li>time 必须大于 0 才会被 IO 发送；偏航角和桨距角设定已永久过滤。</li>
            </ul>
          </article>
          <article class="card">
            <h4>YK 遥控</h4>
            <ul class="checklist">
              <li>只有实时状态与决策目标状态不一致时才生成。</li>
              <li>IO 发送出口再次使用最新有效 YX/设备状态复核；状态一致或未知均不发送。</li>
              <li>差异持续存在时可刷新命令时标；time≤0 的命令永不执行。</li>
            </ul>
          </article>
        </div>
        <div class="callout warning">
          <strong>开环边界：</strong>全局开环仍计算并写设备 p_set 供用户观察，但不生成 YT/YK。设备自身开环时，其 p_curr 作为固定贡献参与平衡，已有命令时标清零并在 IO 出口再次过滤。
        </div>
      </section>

      <section class="section" id="audit">
        <div class="section-heading">
          <div>
            <span class="eyebrow">07 · HISTORY & AUDIT</span>
            <h2>历史记录与决策可追溯性</h2>
            <p>策略输出和审计记录使用同一个短事务提交，避免“有命令无日志”或“有日志无输出”。</p>
          </div>
        </div>
        <div class="grid cols-3">
          <article class="card"><h4>运行断面</h4><p>operator_history 保存环境量、各类电源当前/理论/设定汇总、负荷、储能功率和 SOC 汇总。</p></article>
          <article class="card"><h4>四遥历史</h4><p>scada_yc/yx/yt/yk_his 以运行时刻 + 点号为联合主键；仅保存业务有效点，YK 还必须满足状态差异。</p></article>
          <article class="card"><h4>决策日志</h4><p>LOG_DECISION 保存 trigger、inputs、process、outputs、validation；MMI 按步骤逐行显示过程并保留完整原始 JSON。</p></article>
        </div>
        <p class="callout"><strong>查询策略：</strong>历史曲线按 data_period 自动刷新已勾选曲线，但不重建点树；运行日志不进行周期查询，只在进入页面、查询、重置或分页时按需读取。</p>
      </section>

      <section class="section" id="resilience">
        <div class="section-heading">
          <div>
            <span class="eyebrow">08 · RESILIENCE</span>
            <h2>SQLite 并发、任务切换与异常恢复</h2>
            <p>系统通过数据库级并发策略和严格恢复顺序保证长时间运行。</p>
          </div>
        </div>
        <div class="grid cols-2">
          <article class="card">
            <h4>SQLite 并发访问</h4>
            <ul class="checklist">
              <li>WAL 模式允许读写更好地并行。</li>
              <li>busy_timeout=10 秒，仅对 locked/busy 做有限指数退避重试。</li>
              <li>每线程独立 Engine/Session，网络调用和用户等待不占用事务。</li>
              <li>写操作使用短事务；不依赖只能保护单进程的 Python 全局锁。</li>
            </ul>
          </article>
          <article class="card">
            <h4>任务归零/时钟回退</h4>
            <ol class="checklist">
              <li>先停止并等待受管 Core 线程。</li>
              <li>在短事务内归零控制时钟并清理历史/日志。</li>
              <li>保留点号和点名，仅把四遥 value/time 归零。</li>
              <li>应用新任务元数据和首个有效 YC/YX 断面。</li>
              <li>提交成功后再重启 Core；SOC 不主动复位，后续从有效 YC 获取。</li>
            </ol>
          </article>
        </div>
      </section>

      <section class="section" id="points">
        <div class="section-heading">
          <div>
            <span class="eyebrow">09 · SCADA POINT CATALOG</span>
            <h2>正式数据库四遥点位总表</h2>
            <p>以下点位直接从 @@DB_NAME@@ 只读提取。数据库文件时刻：@@DB_TIME@@；值和时标只是生成文档时的运行断面，不是固定业务常数。</p>
          </div>
          <span class="badge" id="visibleCount">@@TOTAL_POINTS@@ / @@TOTAL_POINTS@@ 可见</span>
        </div>
        <div class="grid cols-4">
          <article class="card"><span class="metric">@@YC_COUNT@@</span><span class="label">YC 遥测点</span></article>
          <article class="card"><span class="metric">@@YX_COUNT@@</span><span class="label">YX 遥信点</span></article>
          <article class="card"><span class="metric">@@YT_COUNT@@</span><span class="label">YT 遥调点</span></article>
          <article class="card"><span class="metric">@@YK_COUNT@@</span><span class="label">YK 遥控点</span></article>
        </div>
        <div class="legend">
          <span>环境：1–3</span>
          <span>柴油：100xxx</span>
          <span>风电：200xxx</span>
          <span>光伏：300xxx</span>
          <span>负荷：400xxx</span>
          <span>储能：500xxx</span>
          <span>设备点号通常为 device_id × 100 + 属性号</span>
        </div>
        <div class="filter-bar" role="search">
          <input id="pointSearch" type="search" placeholder="搜索点号、点名、对象域、含义或单位" aria-label="搜索四遥点位">
          <button type="button" class="active" data-filter="all">全部</button>
          <button type="button" data-filter="scada_yc">YC</button>
          <button type="button" data-filter="scada_yx">YX</button>
          <button type="button" data-filter="scada_yt">YT</button>
          <button type="button" data-filter="scada_yk">YK</button>
        </div>
        <div class="callout">
          <strong>有效性规则：</strong>YC/YX/YT/YK 只有 time&gt;0 才参与业务响应或执行。YC/YX 请求中的 time=0 点仍必须按原位置返回以保持映射；YT/YK 的 time=0 表示无有效命令。
        </div>
        @@POINT_TABLES@@
      </section>

      <section class="section" id="operations">
        <div class="section-heading">
          <div>
            <span class="eyebrow">10 · OPERATIONS</span>
            <h2>运行核对清单</h2>
            <p>用于判断 EMS 是否真正处于健康、可控和可追溯状态。</p>
          </div>
        </div>
        <div class="grid cols-2">
          <article class="card">
            <h4>数据链路</h4>
            <ul class="checklist">
              <li>顶部“电网模拟器连接”为正常，RTU refresh_time 按墙钟推进。</li>
              <li>data_time_curr 推进，YC/YX time&gt;0 且与包级运行时刻一致。</li>
              <li>设备 status/control_mode 与最新有效 YX 一致。</li>
              <li>风机 p_max_curr 随风速和设备参数变化，光伏 p_max_curr 随辐照变化。</li>
            </ul>
          </article>
          <article class="card">
            <h4>控制链路</h4>
            <ul class="checklist">
              <li>决策实际间隔符合 oper_period，oper_time_curr 等于所用最新断面。</li>
              <li>闭环 YT 每个决策周期刷新时标；开环设备没有有效 YT/YK。</li>
              <li>YK 只在实时状态与目标状态不一致时出现并下发。</li>
              <li>LOG_DECISION 可浏览输入、逐行策略、输出和平衡校验。</li>
            </ul>
          </article>
        </div>
        <p class="callout warning"><strong>容量提醒：</strong>历史断面、四遥历史和日志不设置记录数上限。运行日志采用分页、历史曲线按选择查询，但仍需监控 ems.db 和 WAL 文件占用，并制定站点级备份/归档策略。</p>
      </section>
    </main>
  </div>

  <footer>
    本文档由 generate_ems_principles_html.py 根据当前源码规则和 @@DB_NAME@@ 点位定义生成。数据库当前 RTU：@@RTU_SUMMARY@@。生成后如在 MMI 中增删或改名四遥点，请重新运行生成脚本。
  </footer>
</div>
<script>
  (function () {
    var search = document.getElementById("pointSearch");
    var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-filter]"));
    var sections = Array.prototype.slice.call(document.querySelectorAll(".point-section"));
    var activeKind = "all";

    function applyFilter() {
      var query = search.value.trim().toLowerCase();
      var visible = 0;
      sections.forEach(function (section) {
        var sectionEnabled = activeKind === "all" || section.dataset.kind === activeKind;
        var sectionVisible = 0;
        Array.prototype.slice.call(section.querySelectorAll("tbody tr")).forEach(function (row) {
          var matched = sectionEnabled && (!query || row.dataset.search.indexOf(query) >= 0);
          row.classList.toggle("hidden-row", !matched);
          if (matched) {
            sectionVisible += 1;
            visible += 1;
          }
        });
        section.classList.toggle("hidden-section", sectionVisible === 0);
      });
      document.getElementById("visibleCount").textContent = visible + " / @@TOTAL_POINTS@@ 可见";
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        activeKind = button.dataset.filter;
        buttons.forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        applyFilter();
      });
    });
    search.addEventListener("input", applyFilter);
    applyFilter();
  }());
</script>
</body>
</html>
"""
    replacements = {
        "@@VERSION@@": html.escape(version),
        "@@GENERATED_AT@@": html.escape(generated_at.strftime("%Y-%m-%d %H:%M:%S %z")),
        "@@DB_NAME@@": html.escape(db_path.name),
        "@@DB_TIME@@": html.escape(database_time.strftime("%Y-%m-%d %H:%M:%S %z")),
        "@@INTEGRITY@@": html.escape(str(metadata["integrity"])),
        "@@TOTAL_POINTS@@": str(total_points),
        "@@YC_COUNT@@": str(counts["scada_yc"]),
        "@@YX_COUNT@@": str(counts["scada_yx"]),
        "@@YT_COUNT@@": str(counts["scada_yt"]),
        "@@YK_COUNT@@": str(counts["scada_yk"]),
        "@@DATA_PERIOD@@": str(control.get("data_period", "--")),
        "@@OPER_PERIOD@@": str(control.get("oper_period", "--")),
        "@@DATA_TIME@@": format_simu_time(control.get("data_time_curr", 0)),
        "@@POINT_TABLES@@": point_tables,
        "@@RTU_SUMMARY@@": html.escape(
            (
                f"RTU {rtu.get('id')}，{rtu.get('ip')}:{rtu.get('port')}，"
                f"状态={'正常' if int(rtu.get('status', 0)) == 1 else '中断'}"
            )
            if rtu
            else "无 RTU 定义"
        ),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    if "@@" in template:
        raise RuntimeError("HTML 模板仍包含未替换标记")
    return "\n".join(line.rstrip() for line in template.splitlines()) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从当前 EMS 实现和数据库点位生成独立 HTML 工程参考文档"
    )
    parser.add_argument("--db", default="ems.db", help="只读数据源，默认 ems.db")
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"输出 HTML，默认 {DEFAULT_OUTPUT}",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = root / db_path
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    if not db_path.is_file():
        raise FileNotFoundError(f"数据库不存在：{db_path}")

    points, metadata = load_database(db_path)
    document = render_html(root, db_path, points, metadata)
    output_path.write_text(document, encoding="utf-8", newline="\n")
    print(f"generated={output_path}")
    print(
        "points="
        + ", ".join(
            f"{table}:{len(points[table])}" for table, *_rest in POINT_TABLES
        )
    )


if __name__ == "__main__":
    main()
