# Power System Operator

这是一个基于 SQLite、SQLAlchemy 2 和 PyQt6 的微电网操作员系统。默认运行形态是一个 `operator_mmi` 宿主进程：MMI 启动时自动创建 Core、IO 两个受管工作线程，关闭时先停止 IO、再停止 Core 并等待退出。MMI、Core 线程和 IO 线程访问同一个数据库文件，但每个长期工作线程分别创建 SQLAlchemy engine，并始终使用短生命周期 Session。

工程组成：

- `power_operator/runtime_threads.py`：MMI 子线程生命周期控制器，按 Core → IO 启动、按 IO → Core 停止；模拟器新任务或时钟回退恢复时只重启受管 Core 线程。
- `operator_core.py`：Core 的独立测试/运维入口；默认桌面运行由 MMI 内的 Core 子线程每 0.5 秒读取控制表并执行数据处理与控制决策。
- `operator_io.py`：IO 的独立兼容入口；默认桌面运行由 MMI 内的 IO 子线程以 TCP Bridge 模式访问外部 `simulator_io`，拉取 YC/YX、发送 YT/YK并更新 `scada_rtu.status`。
- `operator_mmi.py`：默认唯一常驻 PyQt6 宿主，启动后自动启动 Core、IO 子线程，提供运行、停止、暂停、开环、闭环、双周期和双时钟操作。
- `operator_mmi_qt.ui`：Qt Designer 界面源文件。
- `operator_mmi_qt.py`：由 `pyuic6` 从 `.ui` 文件生成的界面代码。
- `simulator_io_mock.py`：独立的内存 TCP Mock，仅用于本地联调，不读写正式数据库。
- `rtu_client.py`：旧入站 Server 兼容模式的客户端示例。
- `import_power_definitions.py`：把用户已有 `power.db` 中的设备和 SCADA 定义按目标 ORM 列复制到 `ems.db`；源库始终只读。

## 安装与数据库初始化

推荐 Python 3.11 或更高版本：

```powershell
python -m pip install -e ".[test]"
python init_db.py --db ems.db
```

正式 `ems.db` 只初始化单例控制行，不自动创建设备、四遥点或历史数据。首页五条系统曲线直接在 MMI 代码中定义，不使用数据库配置表。若需要演示数据，建议使用单独文件，避免污染正式库：

```powershell
python seed_demo.py --db ems_demo.db
```

如果已有旧 `power.db`，可在正式库初始化后原子复制 5 张设备表以及 RTU、YC、YX、YT、YK 共 10 张定义/实时表：

```powershell
python import_power_definitions.py --source ..\power_system_simulator\power.db --target ems.db --replace
```

导入器不复制源库表结构、控制行、日志或历史记录；目标表结构始终来自 SQLAlchemy ORM。源端额外兼容列（例如 `scada_rtu.conn_num`）会被提示并忽略。源库使用 `mode=ro`、`query_only` 和单个只读事务取得一致快照；脚本会输出逐表记录数、目标列校验结果及可读取时的导入前后 SHA-256。若 Windows 正被外部进程占用而无法直接哈希，或哈希因外部模拟器继续写入而变化，脚本会明确提示，但不会把外部写入误判成导入器修改。目标定义表非空时必须显式使用 `--replace`，整个替换要么全部提交、要么全部回滚。

默认控制行：

| 字段 | 初值 | 含义 |
|---|---:|---|
| `oper_status` | 0 | 0=停止，1=运行，2=暂停 |
| `control_status` | 0 | 0=开环，1=闭环 |
| `io_connect_enabled` | 1 | 1=请求建立/保持连接，0=请求中断连接 |
| `data_period` | 1 | 数据采集周期，单位秒 |
| `oper_period` | 1 | 控制决策的单调墙钟周期，单位秒 |
| `data_time_curr` | 0 | 最近成功写入的数据时刻 |
| `oper_time_curr` | 0 | 最近完成控制的时刻 |
| `source_run_seq` | 0 | 当前已同步的模拟器任务序号 |
| `source_time_start` | 0 | 当前模拟器任务的起始运行时刻 |
| `source_runtime_ready` | 0 | 首个有效断面是否已就绪；1 时 Core 才可运行 |

## 推荐启动方式

使用演示库进行完整联调时，只需要两个终端：先启动外部 Mock，再启动 MMI。MMI 会自动按 Core → IO 的顺序启动两个子线程：

```powershell
python simulator_io_mock.py --host 127.0.0.1 --port 9200
python operator_mmi.py --db ems_demo.db --simulator-host 127.0.0.1 --simulator-port 9200
```

在 MMI 顶部选择开环或闭环，设置数据周期和决策周期，点击“保存参数”，然后点击“启动 / 继续”。数据周期和决策周期都按墙钟秒配置；Core 子线程在停止或暂停期间仍每 0.5 秒检查一次控制表，所以不需要重启程序。关闭 MMI 时，IO 和 Core 子线程会自动停止。`--no-workers` 只用于无头截图、测试或维护，不是正常运行方式。

一次性运维计算入口：

```powershell
python operator_core.py --db ems_demo.db --once
```

`--once` 直接执行一次本地数据处理和决策，适合检查策略及数据库写入；常驻模式始终遵守 `oper_status`。

## 数据库契约

业务表保留需求中的设备表拼写 `dev_diesal_gen`。原始名称 `battery capacity` 含空格，实际列名规范为 `battery_capacity`。`p_min` 和 `soc_min` 均按下限实现。

表清单：

- RTU 与实时四遥：`scada_rtu`、`scada_yc`、`scada_yx`、`scada_yt`、`scada_yk`
- 设备：`dev_diesal_gen`、`dev_wind_gen`、`dev_solar_gen`、`dev_estore`、`dev_load`
- 运行信息：`operator_log`、`operator_history`、`operator_control`
- 四遥历史：`scada_yc_his`、`scada_yx_his`、`scada_yt_his`、`scada_yk_his`

SQLAlchemy ORM 主键与索引：

- 四遥实时表以 `pnt_no` 为主键；设备表以 `id` 为主键。
- `operator_history` 以 `simu_time` 为主键。
- 四遥历史表以 `(time, pnt_no)` 为复合主键，并建立 `(pnt_no, time)` 索引。
- `operator_log` 增加自增 `id`；其中 `log_info` 使用 SQLite `TEXT` 保存不截断的完整决策审计 JSON。`operator_control` 增加固定为 1 的 `id`，以便稳定映射日志和单例控制行。`io_connect_enabled` 是连接请求，不能代替 `scada_rtu.status` 的实际连接结果。
- `init_db.py` 可重复执行，并会为旧库补充控制字段、把四遥历史旧列 `simu_time` 无损改名为 `time`、把旧 `operator_log.log_info VARCHAR(1024)` 无损升级为 `TEXT`、补建缺失索引，同时删除已经废弃的 `curve_def` 表。

时间语义：

- `scada_yc/scada_yx/scada_yt/scada_yk.time` 是运行累计秒数。
- 四遥统一有效性规则：只有 `time > 0` 的 YC/YX/YT/YK 才是有效断面或有效命令；`time <= 0` 表示尚未刷新或尚未下发，只保留点定义，不参与设备更新、历史快照、发送或执行。YK 还必须满足“目标启停状态与当前实际状态不一致”才有效。
- 闭环决策更新 YT 时不比较旧 `value`：每次都把已有预定义 YT 的 `value/time` 写为本轮结果和控制时刻。YK 仍只在目标状态与实时状态不一致时有效，但只要该状态差异持续存在，每个决策周期都刷新 YK `time`，不因目标值与上次命令相同而保留旧时标。
- `scada_*_his.time` 是运行累计秒数。
- `scada_rtu.refresh_time` 是最近一次成功 TCP 交换的 Unix 墙钟秒，只用于链路新鲜度，不参与控制策略或四遥标时。
- `scada_rtu.status=1` 表示最近一次 `operator_io` TCP 交换成功；网络异常、停止、暂停或 Bridge 正常退出时写为 0。连接中断不会覆盖最后一次成功的 `refresh_time`。
- `operator_control.data_time_curr` 与 `oper_time_curr` 分别表示数据时钟和控制时钟。
- `operator_log.simu_time` 与 `operator_history.simu_time` 保留既有数据库字段名，值仍是运行累计秒数。
- `operator_log.log_time` 同样使用 Unix 墙钟秒。
- `operator_history`、`operator_log` 和四张四遥历史表不设置记录数、保留时长或查询条数上限；除系统归零重启时按既定流程清空外，所有历史记录持续保留，主页曲线、历史曲线和运行日志查询均返回满足条件的全部数据。
- MMI 把数据时刻、控制时刻以及 YC/YX/YK/YT 的“刷新时刻”格式化为累计时长 `HH:mm:ss`，小时数超过 24 时继续累加、不回绕；其中小写 `mm` 表示分钟。RTU 的“刷新时刻”单独显示为本地墙钟 `yyyy-MM-dd HH:mm:ss`，例如 `2026-08-23 14:05:09`。

## 内核运行规则

### 停止切换为运行

内核检测到 `oper_status` 从停止变为运行后，执行一次运行数据复位：

- 清空 `operator_history`、`operator_log` 和四遥历史表。
- 保留实时四遥点号和名称，把值与时间归零。
- 把 RTU 状态和刷新时刻归零。
- 保留设备静态参数，把运行状态、实时功率和设定功率归零；储能 `soc_curr` 保留当前值，不做启动复位，后续只接受 `time > 0` 的有效 SOC YC 更新。
- 把数据时钟和决策时钟归零。

暂停后继续不会触发清理；停止后再次启动会重新清理。

### 数据处理

`operator_io` 成功获得一次响应后，原子更新 YC/YX、RTU 刷新时刻和 `data_time_curr`。YC/YX/YT/YK 的点号、点名和其他定义字段在运行期只读：`operator_core`、`operator_io` 及其他常驻运行进程只能修改本地已有点的 `value` 和 `time`，不能使用通信报文创建点位、覆盖 `name` 或修改 `pnt_no`。通信报文中的点名只用于告警上下文，不参与写库；本地 `pnt_no` 是运行期更新的唯一身份键。点位的创建或定义修改只能通过显式建库、定义导入或 MMI 四遥定义操作完成。

运行期收到本地表中不存在的 YC/YX/YT/YK 点号时，必须忽略该点且不得中断同批其他已定义点的更新，同时向进程日志输出 WARNING，并在 `operator_log` 中写入一条 `log_type=2` 的 UTF-8 JSON 告警。告警事件名为 `unknown_scada_point`，至少记录 `signal`、`pnt_no`、报文 `received_name`、`source`、`simu_time` 和便于用户浏览的中文 `message`。

内核发现数据时钟前进后：

1. 根据四遥点名称更新设备实时功率、SOC 和运行状态。实时量只取 `time > 0` 的 YC：除兼容 `设备表名.设备ID.字段名` 外，正式点表还按 `<设备名称>.当前有功/当前功率/当前负荷/当前SOC/当前桨距角` 等系统预定义点名映射到设备字段；多个 YC 指向同一字段时采用时刻最新的一条，不能创建重复点。已废弃的风机当前偏航角、“本步柴油消耗”、风机/光伏理论最大类和“有功功率设定值”YC，以及偏航角设定、桨距角设定 YT 及其历史数据，会在数据库升级时删除；IO 和旧库导入也会过滤这些点，避免重新创建、更新或产生周期性未知点告警。设备字段 `p_max_curr`、`angle_pitch_curr`、YC“当前桨距角”和正常有功设定 YT 不受影响。设备状态只取 `time > 0` 的实时 YX：兼容 `设备表名.设备ID.status`，正式点表按 `<设备名称>.运行状态` 匹配；必要时可通过同点号 YK 的 `<设备名称>.启停命令` 确认设备身份，但绝不使用 YK 目标值作为实际状态。多个 YX 指向同一设备时采用时刻最新的一条，YX 为 0 对应停止、非 0 对应运行。
2. 对每台运行风机，使用本断面的有效风速和该设备自身的 `p_rated`、`wind_in`、`wind_rated`、`wind_cut` 调用独立风功率函数，并把结果写入 `dev_wind_gen.p_max_curr`；停止风机写 0。该字段不读取、不接受“理论最大有功/理论最大功率/理论最大出力”YC 覆盖。光伏理论最大出力同理由 Core 根据太阳辐照和额定功率计算。
3. 写入 `operator_history`、运行日志和当前四遥历史断面。

设备更新和环境量读取只使用 `time > 0` 的 YC/YX；四遥历史快照只保存 `time > 0` 的 YC/YX/YT，以及确实要求状态变化的 YK。

四遥点名称映射格式：

```text
设备表名.设备ID.字段名
```

例如：

```text
dev_wind_gen.1.p_curr
dev_wind_gen.1.status
dev_estore.2.soc_curr
dev_load.1.p_curr
```

环境遥测点支持：

- 风速：`simu.wind`、`weather.wind`、`wind_speed` 或 `环境.当前风速`
- 太阳辐照：`simu.solar`、`weather.solar`、`solar_radiation`、`simu.sloar` 或 `环境.当前太阳辐照`
- 环境温度：`simu.temp`、`weather.temp`、`amb_temp` 或 `环境.当前温度`

### 控制决策

内核使用 `time.monotonic()` 按 `oper_period` 调度控制决策，不用模拟器运行时刻差代替墙钟周期。首次存在 `data_time_curr > oper_time_curr` 的待决策数据时立即执行一次；之后只有同时满足“存在更新的运行断面”和“距上次成功决策已达到 `oper_period` 个墙钟秒”时才再次执行。模拟器运行时刻即使在一个数据周期内跨过 60 秒或更多，也只使用最新断面决策一次，不密集补跑。完成后仍写入 `oper_time_curr=data_time_curr`，因此控制时刻保持为本次决策所用的权威运行时刻。

新能源优先策略为：

1. 运行中的风电与光伏优先按理论最大可用功率供电。
2. 柴油机先维持有功下限；电力不足时再按设备 ID 顺序提高至有功上限。
3. 新能源和柴油下限出力高于负荷时，储能在 SOC、效率和充电功率限制内吸收富余电力。
4. 柴油已在下限且储能已满或达到充电上限时，剩余部分形成新能源弃电；当前按先保留风电、再保留光伏的顺序分配新能源设定值。
5. 电力不足时，储能在 SOC、效率和放电功率限制内放电；仍不足的部分记录为失供电量。

储能功率符号：正值为放电，负值为充电。风机在切入风速到额定风速之间使用立方功率曲线，额定风速到切出风速之间为额定功率；光伏按 `辐照度 / 1000 W/m²` 线性计算并受额定功率限制。

风机最大理论出力已封装为不依赖数据库的纯函数，可供内核或其他程序直接调用：

```python
from power_operator import calculate_wind_max_power

p_max_curr = calculate_wind_max_power(
    current_wind_speed=8.5,
    p_rated=100.0,
    wind_in=3.0,
    wind_rated=11.0,
    wind_cut=25.0,
)
```

开环和闭环边界：

- 开环：策略照常计算，并刷新设备表 `p_set`，但不创建或修改 YT/YK 命令。
- 闭环：除刷新设备表外，每个控制决策周期都把各设备有功设定及本轮控制时刻写入预定义 YT，即使设定值与上次完全相同也刷新 `time`。YK 的 `value` 表示决策目标启停状态，只有该目标与最新有效 YX（必要时回退到设备表 `status`）不一致时，才写入 `time > 0` 的有效 YK；状态差异持续存在时即使目标值未变也按本轮控制时刻刷新 YK，状态已经一致时不创建新 YK，已有同状态 YK 保留点定义但重置为 `time=0`。当前功率调度策略保持设备原启停状态，因此不会产生回写当前状态的冗余 YK。

命令点号按设备类型分区：柴油机从 100001 开始，风机从 200001 开始，光伏从 300001 开始，储能从 400001 开始。YT 与 YK 位于不同表，可以使用相同点号分区。

### 决策过程审计日志

每个实际控制决策周期，无论开环或闭环，都在 `operator_log` 中写入一条 `log_type=4` 的完整 UTF-8 JSON 审计记录。它与设备 `p_set`、YT/YK、历史断面及 `oper_time_curr` 在同一短事务中提交；日志写入失败时控制输出整体回滚，避免输出与审计记录不一致。

决策日志包含：

- 唯一 `decision_id`、控制模式、数据周期、墙钟决策周期、当前数据时刻、上次决策时刻和墙钟时间。
- 风速、辐照、负荷，以及全部柴油机、风机、光伏、储能和负荷的状态、当前值、原设定值与约束。
- 本次使用的有效 YC/YX，以及因 `time <= 0` 被排除的点和原因。
- 新能源优先、柴油下限、储能充电、风光削减、柴油增发、储能放电、失供、启停判断和功率平衡等真实执行步骤；每一步同时记录执行或跳过原因。
- 每台可控设备的新设定值、YT/YK 是否生成及原因、削减、失供和功率平衡误差。

数据库 JSON 保留浮点计算精度；MMI 详情只在显示层格式化为三位小数。

## operator_io Bridge 协议

默认模式：

```text
operator_io --TCP client--> simulator_io
```

协议使用 UTF-8 JSON Lines，每次连接发送一行请求并接收一行响应，单行上限 2 MiB。

按 `data_period` 发出的数据读取请求：

```json
{
  "action": "read",
  "rtu_id": 1,
  "simu_time": 2,
  "data": {
    "yc": [102, 999, 101, 102, 103],
    "yx": [202, 201]
  }
}
```

读取响应：

```json
{
  "ok": true,
  "run_seq": 7,
  "simu_status": 1,
  "simu_time_start": 0,
  "runtime_ready": true,
  "simu_time": 2,
  "data": {
    "yc": [
      {"value":0.5,"time":2},
      {"value":null,"time":0},
      {"value":8.5,"time":2},
      {"value":0.5,"time":2},
      {"value":9.9,"time":0}
    ],
    "yx": [
      {"value":0,"time":2},
      {"value":1,"time":2}
    ]
  }
}
```

请求中的 YC/YX 点号数组是严格的位置映射协议。点号可以任意乱序，服务端必须完全按照原请求列表返回，不排序、不去重、不跳过未知点，也不跳过 `time=0` 点。第 N 个响应项必须对应第 N 个请求点号；重复点号必须在每个原位置重复返回。每个响应项只能包含 `value` 和 `time`，禁止返回 `pnt_no`、`name` 或其他字段。已定义但 `time=0` 的点原样返回自己的 `value/time`；未知点返回 `{"value":null,"time":0}` 占位，并由模拟器记录未知点告警。响应数组长度必须与对应请求数组完全相等，否则后续位置无法安全识别，Bridge 会拒绝整批响应并把 RTU 标记为中断。

顶层 `simu_time` 是模拟器返回的权威时钟，即使 YC/YX 查询数组为空也必须存在。Bridge 不得用请求中的建议时刻或本机墙钟补造该字段；收到成功响应后把它原样写入 `operator_control.data_time_curr`。点内 `time` 是该点自身的数据时刻，只有 `0 < time <= simu_time` 的项才更新本地已有 YC/YX；`time=0` 占位只用于保持位置，不产生业务响应。Bridge 根据原请求数组的位置恢复本地点号，只修改本地已有点的 `value/time`，绝不修改 `pnt_no/name`。RTU `refresh_time` 写本次成功交换的 Unix 墙钟，三种时刻语义不得混用。真实模拟器与开发 Mock 均使用 `data.yc/data.yx` 的相同位置协议。

新版模拟器响应还会成组返回 `run_seq`、`simu_time_start`、`simu_status` 和 `runtime_ready`。`run_seq` 是权威任务身份：序号变化时，即使新任务时刻前进或不变，也必须停止 Core 并清理旧任务运行数据。只有 `runtime_ready=true` 且本包至少包含一个正时标、非空值的 YC/YX，才认为首个有效断面已经就绪；在此之前 EMS 只同步任务元数据和包级时刻，Core 保持停止。首断面提交后再恢复 Core。完全不含 `run_seq` 的旧模拟器和开发 Mock 继续使用原有时钟回退兼容逻辑。

当 `run_seq` 变化，或同一任务的响应满足 `simu_time < operator_control.data_time_curr` 时，Bridge 必须严格执行以下恢复顺序：

1. IO 通过 MMI 注入的 `CoreThreadController` 设置停止事件并等待受管 Core 子线程退出；若停止失败或超时，禁止回退时钟或清理数据。
2. 在一个短事务中先把 `data_time_curr`、`oper_time_curr` 归零，再清空 `operator_history`、`operator_log` 和四张四遥历史表。
3. 保留四遥点号和名称，把所有 YC/YX/YT/YK 的值及 `time` 归零；首页曲线是代码内置配置，没有需要清理的曲线配置表。
4. 把 Bridge 的数据周期计时、控制发送周期计时、YT 游标和 YK 游标全部归零。
5. 在同一个事务中保存任务元数据；只有首个有效断面已就绪时才按原请求位置应用本批 `time > 0` 的 YC/YX。点的 `time` 使用各自响应项时刻，`data_time_curr` 更新为包级 `simu_time`，RTU `refresh_time` 使用 Unix 墙钟；零时刻占位保持归零状态且不影响后续位置。
6. 首个有效断面事务提交成功后，使用同一 MMI 线程控制器和数据库路径重新启动 Core 子线程；未就绪时保持 Core 停止，后续轮询不得重复停止同一线程。事务失败必须完整回滚，并按进入恢复前的状态决定是否恢复 Core。

默认 MMI 托管模式不为 Core 子线程创建独立 PID 文件，线程控制器只管理自己创建的线程对象、停止事件和线程代次，不能终止其他线程或 Python 进程。显式运行独立 `operator_core.py`/`operator_io.py` 时仍保留原 PID 文件和 `CoreProcessManager` 兼容机制，但不得与默认 MMI 托管模式同时运行。

Bridge 每 1 秒检查一次变化的 YT/YK，并发送：

```json
{
  "action": "write",
  "data": {
    "yt": [{"pnt_no":200001,"name":"dev_wind_gen.1.p_set","value":62.5,"time":10}],
    "yk": [{"pnt_no":200001,"name":"dev_wind_gen.1.status","value":1,"time":10}]
  }
}
```

Bridge 在组装载荷时会再次比较 YK 目标值与最新有效 YX；同点号 YX 优先，其次匹配同名状态点，最后回退到设备表。状态一致或无法确认当前状态的 YK 不下发。只有收到 `{"ok":true}` 后才推进已发送命令游标，失败时保留命令供下次重试；已确认不需要动作的 YK 可以安全越过。成功的数据读取在一个短事务内更新 YC/YX、RTU 和权威数据时钟，并将 RTU 状态置为 1；缺少 `simu_time`、读取或写入连接失败时立即把状态置为 0，但保留最后一次成功刷新时刻。

四遥协议同样执行 `time > 0` 业务有效性边界，但“返回位置”和“业务响应”必须区分：YC/YX 读取时 `time=0` 项仍须按原位置返回，Bridge 收到后只忽略该项本身，不能删除它或让后续项错位；YT/YK 则只有 `time > 0` 才能发送或执行。YK 还必须满足目标状态与当前状态不同。YT 每个闭环决策周期都会获得新时标；YK 在状态差异持续存在时也会获得新时标，两者都不以旧命令值是否相同作为时标刷新条件。偏航角设定和桨距角设定 YT 会在发送出口再次过滤。即使调用方传入负游标，也会按 0 作为有效命令下界。Mock 忽略 `time <= 0` 的控制项，并且只把有效项计入接收数量。

旧入站协议仍可显式启动，仅用于兼容已有 RTU 客户端：

```powershell
python operator_io.py --db ems.db --mode server --listen-host 127.0.0.1 --listen-port 9100
python rtu_client.py --host 127.0.0.1 --port 9100 --rtu-id 1 --period 1 --measurements sample_measurements.json
```

## SQLite 多线程与外部多进程并发

SQLite 允许多个并发读者，但同一时刻只有一个写事务。本工程不使用只能保护单进程的 Python 全局锁，而采用数据库级措施：

1. `PRAGMA journal_mode=WAL`：读进程通常不会阻塞写进程。
2. `PRAGMA busy_timeout=10000`：短暂写锁最多等待 10 秒。
3. 短事务：Bridge 交换、内核处理、MMI 保存均快速提交，不跨网络调用持有数据库写锁。
4. 有限重试：仅对 SQLite `locked`/`busy` 错误最多重试 7 次，采用指数退避和随机抖动；业务异常直接抛出。
5. 每个长期工作线程独立 engine：MMI、Core、IO 分别创建 `Database/Engine`，禁止跨线程传递 `Session`；外部工具或兼容独立进程同样必须创建自己的 engine。

若未来出现大量高频写者、长事务或远程数据库访问，应改用 PostgreSQL 等服务型数据库，而不是不断延长锁等待。

## MMI 与 Qt UI 生成

界面源文件先在 Qt Designer 中维护，再执行：

```powershell
pyuic6 operator_mmi_qt.ui -o operator_mmi_qt.py
python -m py_compile operator_mmi_qt.py operator_mmi.py
```

不要手工修改 `operator_mmi_qt.py`。MMI 页面包括：

- 顶部紧凑控制区：运行状态、控制模式、数据周期、决策周期、数据时刻、控制时刻、电网模拟器连接状态、四个运行操作按钮以及“建立连接”“中断连接”两个连接按钮；两个时刻均把数据库整数秒显示为 `HH:mm:ss`。连接状态在所有页面始终可见，并按 `scada_rtu.status` 显示绿色“正常”或红色“中断”。
- 连接按钮只写 `operator_control.io_connect_enabled`：中断时立即把 RTU 状态置 0 并暂停 Bridge 交换；建立时只恢复 Bridge 的真实连接尝试，成功前不得把状态伪装为“正常”。
- 系统主页：当前运行断面的只读文本框及可多选曲线。
- 设备定义：柴油机、风机、光伏、储能和负荷。页面顶部提供“保存设备修改”，双击浅黄色单元格可修改名称、额定值、上下限、风速阈值、效率、容量及 SOC 上下限等静态参数；灰色的设备 ID（已有记录）、状态、实时量和设定值只读。末尾空白行的 ID 和静态参数可用于新增设备。
- RTU / 四遥定义：顶部实时显示 `operator_io`“连接成功”或“连接中断”，并显示 RTU、对端地址、墙钟刷新时刻和在线数量；下部提供 RTU、YC、YX、YT、YK 五个表格子页。
- 运行日志：按日志类型、运行时刻和关键词查询全部匹配记录，不设置条数上限。选择控制决策后，可以分组浏览“触发条件、输入、决策过程、输出、平衡校验/警告”，也可以切换查看保持中文的格式化原始 JSON；旧普通文本日志仍可直接查看。
- 历史曲线：左侧多选四遥点，右侧按时间范围绘制多条曲线。
- 系统主页和历史曲线均支持鼠标数据游标：移动鼠标时吸附到最近的数据时刻，绘制竖向虚线和各曲线数据点，并显示 `HH:mm:ss` 时刻及所有已选曲线的三位小数值；鼠标离开、数据切换、缩放或复位后清除旧游标。

界面刷新与显示规则：

- 顶部运行状态、双时钟和连接状态每 1 秒刷新；系统主页也保持每 1 秒刷新。
- 当前页面是设备定义、RTU / 四遥定义、运行日志或历史曲线时，该页面的数据按 `operator_control.data_period` 刷新，不使用固定 1 秒周期；`oper_period` 只用于控制决策，不参与界面刷新。
- 切换到任一页面时立即刷新一次，不等待下一个数据周期；随后从切换时刻重新按 `data_period` 计时。
- 设备或四遥表格存在正在编辑或尚未保存的单元格时，页面切换刷新和周期刷新都会跳过该编辑表，防止覆盖用户输入；保存成功后恢复自动刷新。
- 保存设备修改使用单个 SQLAlchemy 写事务，只更新静态参数，不会把界面中可能已过时的 `status`、`p_curr`、`p_set`、`p_max_curr`、桨距角或 `soc_curr` 覆盖回数据库；类型校验或数据库写入失败时事务整体回滚，并保留界面输入和未保存状态供用户修正。设备名称参与系统预定义 YC/YX/YT/YK 点名匹配，改名后必须同步维护相关四遥点名。
- 历史曲线的周期刷新只重新查询并绘制已勾选曲线，不重建左侧点树；切换页面或手工刷新点表时即使点名发生变化，也按“历史表 + 点号”保留原勾选状态。
- 运行日志周期刷新保留当前过滤条件、选中记录、详情页签和滚动位置；用户正在查阅历史决策时不会被强制切换到最新记录。
- 所有表格、主页只读文本框、曲线数据和纵轴刻度中的浮点数统一显示三位小数；数据库仍保存原始浮点精度。
- 所有数据表格的列宽平均分布，最后一列不单独拉伸；窗口缩放、切换页面或周期刷新后保持等宽。

## 验证

```powershell
python -m pytest -q
python -m compileall -q .
```

测试覆盖建表与旧库迁移、`power.db` 只读定义导入、决策审计 JSON 和事务回滚、PRAGMA、线程和四进程并发写、调度边界、停止到运行的清理、四遥 `time > 0` 有效性、YK 状态差异判断与发送出口复核、双时钟处理、开闭环命令边界、Bridge 周期与游标、手动连接开关、连接失败状态回写、真实 TCP socket，以及 Qt 无头构造、五个主页面、连接按钮、RTU 页面、运行日志决策详情、运行周期页面刷新、切页立即刷新、未保存编辑保护、历史曲线勾选保护和三位小数显示。
