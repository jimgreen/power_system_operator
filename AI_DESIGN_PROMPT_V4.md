# 电力系统操作员软件完整设计与代码生成提示词 V4

> 使用方法：将本文件从“提示词正文开始”到“提示词正文结束”的全部内容原样复制给具备文件读写和终端执行能力的 AI。该 AI 应在一个空目录或指定工程目录中生成完整工程、数据库、示例数据、自动化测试、运行说明和独立 HTML 技术与使用手册，并实际完成验证。

> 2026-08-24 更新：将 `power_system_operator_technical_user_manual_v1.0.html` 纳入强制交付物，补充手册内容边界、真实 MMI 界面截图、分步用户操作说明、Base64 单文件离线版式、响应式/A4 打印要求以及真实浏览器验收标准。

> 2026-08-24 发布规范更新：补充“保存修改、提交远方、重启服务”的完整执行边界，要求显式暂存清单、测试后提交、四方 Git SHA 一致性、按脚本和数据库精确重启 MMI 宿主及其两个受管子线程、保护外部电网模拟器，并以新宿主 PID、线程快照、端口归属、数据库、RTU 墙钟、运行日志和真实 MMI 窗口作为发布后健康证据。

> 2026-08-24 线程架构更新：默认运行形态改为一个 `operator_mmi` 宿主进程自动托管 Core、IO 两个工作线程；MMI 启动即按 Core → IO 启动，关闭时按 IO → Core 停止并等待，模拟器新任务或时钟回退恢复只停启受管 Core 线程。独立 Core/IO 入口仅保留给测试和兼容运维，不得与默认托管模式并行运行。

> 2026-08-24 MMI 单实例更新：`operator_mmi` 必须通过操作系统级原子单实例锁保证同一台主机、同一用户会话内最多只有一个宿主。Windows 使用命名 Mutex，其他平台使用等价的进程锁；禁止用存在竞态的进程扫描代替。入口必须在初始化 `ems.db`、创建 Qt 窗口和启动 Core/IO 线程之前获取锁；第二次启动必须输出明确告警并以非零退出码立即结束，不得触碰数据库或启动任何工作线程。锁随正常退出显式释放，进程异常退出时由操作系统自动释放，不能遗留永久假锁。

> 2026-08-24 运行日志分页更新：`operator_log` 继续永久保存且不设置记录总量上限，但 MMI 禁止一次性查询并渲染全部日志。日志页必须先对当前过滤条件执行总数查询，再使用数据库 `LIMIT/OFFSET` 分页读取；默认每页 100 条，可切换 50/100/200/500 条，提供总条数、当前页/总页数、上一页和下一页。查询或重置条件时回到第一页；周期刷新必须保留当前页和已选日志，若新日志导致所选记录移动到其他页，应自动计算其新页码并继续选中，避免页面堵塞或用户阅读位置丢失。

> 2026-08-25 参数编辑保护与曲线游标更新：顶部开闭环/双周期、设备静态参数及 RTU/四遥定义一旦被人工修改，控件或单元格必须立即使用醒目的橙黄色脏状态提示；保存或用户点击对应区域的“手动刷新参数”并确认放弃修改前，1 秒刷新、`data_period` 周期刷新和切页刷新均不得覆盖未保存值。运行状态、双时钟、连接状态等只读实时量继续刷新，运行/暂停/停止动作只能写 `oper_status`，不得顺带保存待提交参数。所有使用 `InteractivePlot` 的曲线板（当前包括系统主页和历史曲线）都必须让游标文字框跟随真实鼠标坐标移动；即使仍吸附同一个数据时刻，只要鼠标位置变化，文字框也必须同步移动。文字框接近绘图区边界时自动翻转并始终保持在绘图区内，背景颜色 alpha 必须严格等于 0，禁止使用实色或半透明填充，仅保留可读文字、曲线色标和轻量边框。

> 2026-08-25 RTU / 四遥页面去重更新：删除 RTU / 四遥定义页中重复的 `operator_io 连接状态` 信息栏及其“当前状态、连接成功/中断、RTU、对端、刷新时刻、在线数量”等控件。电网模拟器连接状态只在全局顶部控制区显示，继续复用 `scada_rtu.status` 并每 1 秒刷新；RTU / 四遥定义页从 RTU、YC、YX、YT、YK 子页开始，不得再次显示重复连接摘要。

> 2026-08-25 设备控制模式更新：柴油、风机、光伏、储能设备增加 `control_mode`（0 开环、1 闭环，默认 1）。Core 从每台设备实时 YX 属性 2（`设备ID*100+2`、点名“设备名称.控制模式”）更新本地模式；全局 `CONTROL_CLOSED` 与设备 `control_mode=1` 必须同时成立才允许生成、保留或发送该设备 YT/YK。设备开环时，其实时 `p_curr` 作为固定功率贡献从负荷需求中扣除，EMS 只调度剩余闭环设备；停机开环设备不计入固定贡献。旧库缺列时幂等补默认 1 并一次性补缺失的模式 YX，旧定义库缺该列时导入为 1。

> 2026-08-24 模拟器任务同步更新：YC/YX 成功响应携带 `run_seq`、`simu_time_start`、`simu_status`、`runtime_ready`。EMS 以 `run_seq` 变化识别新的模拟任务，不再只依赖运行时刻回退；新任务首个有效断面就绪前必须保持 Core 暂停，完成运行数据清理和首断面同步后再恢复 Core。项目 Mock 同步实现任务元数据和旧任务命令拒绝；完全没有任务元数据的旧对端继续使用原时钟回退兼容逻辑。

---

## 提示词正文开始

你是一名资深 Python、SQLAlchemy、SQLite、多线程/多进程并发服务、TCP 协议和 PyQt6 工程师。请直接创建一个完整、可运行、可测试的“电力系统操作员”桌面软件工程。

不要只给设计说明、伪代码、局部代码或文件清单。必须在当前工作目录中创建全部源文件、Qt `.ui` 文件、由 `pyuic6` 生成的 Python 文件、数据库初始化脚本、示例数据脚本、TCP Mock、自动化测试、README 和独立 HTML 技术与使用手册，并实际运行验证命令。若当前目录已有文件，应保留无关内容，不得重置、清理或覆盖不在本任务范围内的用户文件。

除非遇到确实无法从本提示词判断且会改变系统语义的问题，否则不要停下来反复询问。使用本提示词规定的默认值完成实现。

### 一、最终交付目标

交付一个默认采用“单一 MMI 宿主进程 + Core/IO 两个受管子线程”的完整工程：

1. `operator_mmi`：默认唯一常驻 PyQt6 桌面宿主进程。入口使用操作系统级原子单实例锁，第二个同名宿主必须在数据库初始化和工作线程启动前被拒绝。启动首个 MMI 后自动创建并启动 Core、IO 两个子线程，支持运行控制、开环/闭环、双周期、双时钟、主页曲线、设备定义、四遥定义、分页运行日志、历史曲线，以及建立/中断电网模拟器连接；关闭 MMI 时自动停止并等待两个子线程。运行日志必须通过数据库分页查询，并支持展开浏览每次控制决策的触发条件、完整输入、真实策略过程、完整输出和平衡校验。

2. Core 子线程：使用独立 SQLAlchemy `Database/Engine` 访问同一个 SQLite 文件，每 0.5 秒检查运行控制状态，根据数据时钟执行数据处理，根据决策时钟执行新能源优先策略。`operator_core.py` 只保留为一次性测试或显式独立运维入口。

3. IO 子线程：使用独立 SQLAlchemy `Database/Engine`，默认作为 TCP 客户端访问外部 `simulator_io`，按数据周期获取 YC/YX，每 1 秒发送变化的 YT/YK；时钟回退时通过受管线程控制器停启 Core 子线程。`operator_io.py` 只保留显式独立兼容入口和 TCP Server 模式。

4. `simulator_io_mock`：独立的 TCP Mock 服务，只在内存中维护数据，用于本地完整联调，不读写正式数据库。

5. SQLite 正式数据库 `ems.db`：使用 SQLAlchemy 2 ORM 创建。不得创建新的 `power.db`。如果用户提供已有旧库 `power.db`，它只能作为只读数据源，用于把设备定义和 SCADA 定义的行内容复制到 `ems.db`；目标表结构始终以本提示词规定的 ORM 为准。

6. 示例数据库：示例数据必须通过 `seed_demo.py --db ems_demo.db` 写入独立的 `ems_demo.db`，不得污染正式 `ems.db`。

7. 技术与用户操作手册：生成 `power_system_operator_technical_user_manual_v1.0.html`。它必须是中文 UTF-8、单文件、可离线打开、响应式并适合 A4 打印的完整成品，内容与最终实际代码、ORM、协议、界面和验证结果一致；必须内嵌至少 6 张由真实 PyQt6 程序直接渲染的 MMI 界面截图，并给出用户可逐步执行的操作说明，不能只是 README 的机械复制、界面示意图或尚未实现的规划说明。

### 二、技术栈和工程约束

- Python 3.11 或更高版本。

- SQLAlchemy 2.x ORM，使用 `DeclarativeBase`、`Mapped` 和 `mapped_column`。

- SQLite 3。

- PyQt6 6.5 或更高版本。

- psutil 5.9 或更高版本，只用于显式独立 Core/IO 兼容入口的进程归属校验；默认 MMI 托管模式不得通过 psutil 停启 Core/IO。

- pytest 8 或更高版本。

- 使用 `pyproject.toml` 声明依赖和 pytest 配置。

- MMI 宿主以及 Core、IO 子线程的业务运行不依赖 Web 浏览器或 Web 服务。HTML 手册只作为离线文档交付和验收对象，不得被引入业务运行依赖。

- 曲线控件优先使用 PyQt6 自带的 `QPainter` 实现，避免引入大型绘图库；若选择其他库，必须加入依赖并证明新环境安装后可以直接运行。

- 代码必须适用于 Windows PowerShell，同时避免写死仅能在 Windows 运行的业务逻辑。

- 所有程序入口支持 `--db` 参数，默认值为 `ems.db`。

- 使用 UTF-8 源文件和 UTF-8 JSON Lines TCP 协议。

- 中文界面统一使用“运行”“运行时刻”“数据时刻”“控制时刻”“运行周期”等术语，不使用其他旧界面术语。

- 数据库内部已经规定的字段名 `simu_time`、`wind_speed`、`solar_radiation` 必须保留，不能擅自改名；界面显示名称可以使用中文友好名称。

- `dev_wind_gen.angle_yaw_curr`、风机“偏航角设定”YT、风机“桨距角设定”YT 和 `dev_estore.soc_init` 已明确废弃：新 ORM、新数据库、示例数据、MMI、Core 有效字段和四遥定义中均不得出现。旧数据库和旧 `power.db` 中可能仍存在兼容列或废弃点，必须按本提示词的升级/导入规则删除或忽略，不能为了兼容而重新加入目标模型。风机设备字段 `angle_pitch_curr` 和 YC“当前桨距角”仍然保留。

- 最新强制口径：`scada_yc` 不得定义或保留“本步柴油消耗”“理论最大有功/理论最大功率/理论最大出力”“有功功率设定值”以及本提示词列出的兼容旧格式点。数据库升级时按点号同步删除其全部 YC 历史，旧库导入必须过滤，IO 再次收到时静默忽略，Core 不更新、不保存历史，MMI 不显示。这里禁止的是废弃 YC，不能误删设备表 `p_max_curr` 或正常有功设定 YT。

- 最新强制口径：`dev_wind_gen.p_max_curr` 是 Core 独占计算字段，不是外部量测。每个新数据断面必须使用本断面 `time > 0` 的有效环境风速以及每台风机自身的 `p_rated`、`wind_in`、`wind_rated`、`wind_cut` 调用独立纯函数 `calculate_wind_max_power()` 后写入；风机停止、无有效风速或参数非法时写 0。任何 YC、MMI、设备定义保存或电网模拟器报文都不得直接覆盖该字段。

- 最新强制口径：储能模型、新建库、示例数据、导入目标、Core 和 MMI 均不得出现 `soc_init`。旧库只删除该列并原样保留 `soc_curr`；停止→运行、暂停→继续、IO 断线、Core 重启和模拟器时钟回退都不得复位 `soc_curr`。只有能够唯一映射到设备且 `time > 0` 的实时 SOC YC 才能更新 `soc_curr`。

- YC、YX、YT、YK 使用统一有效性规则：只有 `time > 0` 才是有效数据或有效命令；`time <= 0` 只表示点定义尚未刷新或尚未下发，不得更新设备、进入四遥历史、发送或执行。YK 在正时刻条件之外，还必须满足“决策目标启停状态与当前实际启停状态不一致”。

### 三、必须生成的工程结构

至少生成以下文件：

```text
pyproject.toml
README.md
power_system_operator_technical_user_manual_v1.0.html
init_db.py
import_power_definitions.py
seed_demo.py
operator_core.py
operator_io.py
operator_mmi.py
operator_mmi_qt.ui
operator_mmi_qt.py
simulator_io_mock.py
rtu_client.py
sample_measurements.json

power_operator/
    __init__.py
    models.py
    database.py
    core.py
    core_process.py
    runtime_threads.py
    command_points.py
    measurement_points.py
    status_commands.py
    retired_measurements.py
    definition_import.py
    strategy.py
    io_service.py
    wind_power.py
    time_utils.py
    plot_widget.py

tests/
    test_database.py
    test_power_definition_import.py
    test_multiprocess_database.py
    test_strategy.py
    test_wind_power.py
    test_core.py
    test_core_v2.py
    test_core_process.py
    test_runtime_threads.py
    test_protocol.py
    test_tcp_service.py
    test_io_bridge.py
    test_simulator_io_mock.py
    test_time_utils.py
    test_mmi.py
```

可以增加必要的辅助文件，但不得缺少上述核心文件。

### 四、数据库模型

所有业务表使用以下精确表名和字段名。整数使用 SQLAlchemy `Integer`，小数使用 `Float`，普通短文本使用 `String`；只有明确标出的完整审计内容使用 SQLAlchemy `Text`。除明确允许为空的字段外，均设置合理的非空默认值。

#### 4.1 RTU 与实时四遥

`scada_rtu`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `id` | int | 主键，RTU 序号 |
| `ip` | string(64) | IP 地址，默认空字符串 |
| `port` | int | TCP 端口，默认 0 |
| `status` | int | 连接状态；1=最近一次 TCP 交换成功，0=连接中断/停止/暂停，默认 0 |
| `refresh_time` | int | 最近一次成功 TCP 交换的 Unix 墙钟秒，默认 0，只用于链路新鲜度 |

`scada_yc`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `pnt_no` | int | 主键，点号 |
| `name` | string(128) | 点名 |
| `value` | float | 遥测值 |
| `time` | int | 运行累计秒数 |

`scada_yx`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `pnt_no` | int | 主键 |
| `name` | string(128) | 点名 |
| `value` | int | 遥信值 |
| `time` | int | 运行累计秒数 |

`scada_yt`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `pnt_no` | int | 主键 |
| `name` | string(128) | 点名 |
| `value` | float | 遥调值 |
| `time` | int | 运行累计秒数 |

新建数据库、示例数据和任何显式点表编辑均不得定义风机偏航角设定或桨距角设定 YT。`scada_yt.name` 必须拒绝 `<设备名称>.偏航角设定`、`dev_wind_gen.<id>.angle_yaw_set`、`dev_wind_gen.<id>.angle_yaw_setpoint`、`<设备名称>.桨距角设定`、`dev_wind_gen.<id>.angle_pitch_set` 和 `dev_wind_gen.<id>.angle_pitch_setpoint`；MMI 四遥定义页保存时也必须执行同一校验并给出清晰错误，不能等待下次重启迁移才删除。该限制不得影响设备字段 `angle_pitch_curr`、YC“当前桨距角”、有功设定或功率设定。

`scada_yk`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `pnt_no` | int | 主键 |
| `name` | string(128) | 点名 |
| `value` | int | 遥控值 |
| `time` | int | 运行累计秒数 |

#### 4.2 设备表

保留需求中的 `diesal` 拼写，不改为 `diesel`。

`dev_diesal_gen`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int 主键 | 柴油发电机 ID，全网唯一 |
| `name` | string(128) | 柴油发电机名称 |
| `p_rated` | float | 额定功率，kW |
| `p_max` | float | 有功上限，kW |
| `p_min` | float | 有功下限，kW |
| `p_coeff` | float | 柴油消耗率，kg/kWh |
| `status` | int | 1=运行，0=停止 |
| `p_curr` | float | 当前出力，kW |
| `p_set` | float | 当前有功设定值，kW |
| `control_mode` | int | 0=设备开环，1=设备闭环；默认 1 |

`dev_wind_gen`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int 主键 | 风机 ID，全网唯一 |
| `name` | string(128) | 风机名称 |
| `p_rated` | float | 额定功率，kW |
| `wind_in` | float | 切入风速，m/s |
| `wind_rated` | float | 额定风速，m/s |
| `wind_cut` | float | 切出风速，m/s |
| `status` | int | 1=运行，0=停止 |
| `p_max_curr` | float | 当前理论最大出力，kW |
| `angle_pitch_curr` | float | 当前桨距角 |
| `p_curr` | float | 当前出力，kW |
| `p_set` | float | 当前有功设定值，kW |
| `control_mode` | int | 0=设备开环，1=设备闭环；默认 1 |

`dev_solar_gen`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int 主键 | 光伏 ID，全网唯一 |
| `name` | string(128) | 光伏名称 |
| `p_rated` | float | 额定功率，kW |
| `status` | int | 1=运行，0=停止 |
| `p_max_curr` | float | 当前理论最大出力，kW |
| `p_curr` | float | 当前出力，kW |
| `p_set` | float | 当前有功设定值，kW |
| `control_mode` | int | 0=设备开环，1=设备闭环；默认 1 |

`dev_estore`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int 主键 | 储能 ID，全网唯一 |
| `name` | string(128) | 储能名称 |
| `status` | int | 1=运行，0=停止 |
| `p_charge_max` | float | 充电功率上限，kW |
| `p_charge_eff` | float | 充电效率，0～1 |
| `p_discharge_max` | float | 放电功率上限，kW |
| `p_discharge_eff` | float | 放电效率，0～1 |
| `p_curr` | float | 当前功率，kW；正值放电，负值充电 |
| `p_set` | float | 当前功率设定值，kW；正值放电，负值充电 |
| `battery_capacity` | float | 电池容量，kWh；不要使用带空格的 SQL 列名 |
| `soc_curr` | float | 当前 SOC，0～1 |
| `soc_max` | float | SOC 上限 |
| `soc_min` | float | SOC 下限 |
| `control_mode` | int | 0=设备开环，1=设备闭环；默认 1 |

`dev_load`：

| 字段 | 类型 | 含义 |
|---|---|---|
| `id` | int 主键 | 负荷 ID，全网唯一 |
| `name` | string(128) | 负荷名称 |
| `status` | int | 1=运行，0=停止 |
| `p_curr` | float | 当前用电功率，kW |

#### 4.3 日志、历史和控制表

`operator_log`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `id` | int | 自增主键，为 ORM 稳定映射增加 |
| `log_time` | int | Unix 墙钟秒，建立索引 |
| `simu_time` | int | 运行累计秒数，建立索引 |
| `log_type` | int | 1=信息，2=警告，3=错误，4=控制决策审计 |
| `log_info` | text | 完整日志详情；使用 SQLAlchemy `Text`，不得截断决策输入、过程或输出 |

`operator_history`：

| 字段 | 类型 | 约束与含义 |
|---|---|---|
| `simu_time` | int | 主键，运行累计秒数 |
| `wind_speed` | float | 当前风速，m/s |
| `solar_radiation` | float | 当前太阳辐照，W/m² |
| `amb_temp` | float | 当前环境温度，°C |
| `diesal_power_curr_sum` | float | 柴油当前总出力，kW |
| `diesal_power_set_sum` | float | 柴油设定总出力，kW |
| `diesal_curr_sum` | float | 柴油累计消耗，kg |
| `wind_power_curr_sum` | float | 风机当前总出力，kW |
| `wind_power_max_sum` | float | 风机理论最大总出力，kW |
| `wind_power_set_sum` | float | 风机设定总出力，kW |
| `solar_power_curr_sum` | float | 光伏当前总出力，kW |
| `solar_power_max_sum` | float | 光伏理论最大总出力，kW |
| `solar_power_set_sum` | float | 光伏设定总出力，kW |
| `load_power_curr_sum` | float | 负荷总功率，kW |
| `estore_power_curr_sum` | float | 储能当前总功率，kW |
| `estore_power_set_sum` | float | 储能设定总功率，kW |
| `estore_power_soc_sum` | float | 储能 SOC 合计 |

`operator_control` 必须是单例表，固定使用 `id=1`：

| 字段 | 类型 | 初值与含义 |
|---|---|---|
| `id` | int 主键 | 固定 1 |
| `oper_status` | int | 初值 0；0=停止，1=运行，2=暂停 |
| `control_status` | int | 初值 0；0=开环，1=闭环 |
| `io_connect_enabled` | int | 初值 1；1=请求建立/保持连接，0=请求中断连接；这不是实际连接结果 |
| `data_period` | int | 初值 1；数据采集周期，秒，最小 1 |
| `oper_period` | int | 初值 1；控制决策的单调墙钟周期，秒，最小 1 |
| `data_time_curr` | int | 初值 0；最近成功写入数据的运行时刻 |
| `oper_time_curr` | int | 初值 0；最近完成控制决策的运行时刻 |
| `source_run_seq` | int | 初值 0；当前已同步的模拟器任务序号；变化表示新的权威任务边界 |
| `source_time_start` | int | 初值 0；当前模拟器任务的起始运行时刻 |
| `source_runtime_ready` | int | 初值 0；0=新任务首个有效断面尚未就绪，1=已就绪且允许 Core 运行 |

控制表物理列顺序必须为：

```text
id, oper_status, control_status, io_connect_enabled, data_period,
oper_period, data_time_curr, oper_time_curr,
source_run_seq, source_time_start, source_runtime_ready
```

#### 4.4 四遥历史表

四张历史表都必须使用字段名 `time`，不能使用 `simu_time`：

`scada_yc_his`：`time(int)`、`pnt_no(int)`、`value(float)`。

`scada_yt_his`：`time(int)`、`pnt_no(int)`、`value(float)`。

`scada_yx_his`：`time(int)`、`pnt_no(int)`、`value(int)`。

`scada_yk_his`：`time(int)`、`pnt_no(int)`、`value(int)`。

每张表均使用：

```text
PRIMARY KEY (time, pnt_no)
INDEX (pnt_no, time)
```

#### 4.5 四遥点定义所有权和运行期写权限

四张实时四遥表必须严格区分“点定义字段”和“运行字段”：

```text
点定义字段：pnt_no、name
运行字段：  value、time
```

权限边界必须固定为：

| 程序或操作 | 创建点位 | 修改 `pnt_no/name` | 修改 `value/time` |
|---|---:|---:|---:|
| `init_db.py`、定义导入、示例库生成 | 允许 | 允许 | 允许 |
| MMI 四遥定义页的显式用户保存 | 允许 | 允许 | 允许 |
| `operator-core` 受管子线程 / 独立兼容入口 | 禁止 | 禁止 | 仅限本地已有点 |
| `operator-io` 受管子线程 / 兼容 Server | 禁止 | 禁止 | 仅限本地已有点 |
| 其他运行期后台线程或进程 | 禁止 | 禁止 | 仅限本地已有点 |

运行期必须以本地数据库中的 `pnt_no` 作为唯一身份键。通信报文里的 `name` 是不可信的描述信息，只允许用于告警上下文和调试展示，不能参与 INSERT、不能覆盖本地点名，也不能改变点号。收到同点号但不同点名的报文时，必须保留本地 `name`，只更新 `value/time`。

必须提供一个由所有四遥运行期接收路径共用的更新函数，同时支持 YC、YX、YT、YK：

1. 校验数据项和 `pnt_no` 类型。
2. 用模型和 `pnt_no` 查询本地点定义。
3. 已定义点且 `time > 0` 时，只赋值 `point.value` 和 `point.time`。
4. 未定义点不得创建、不得修改任何四遥表记录，也不得阻断同批其他已定义点提交。
5. 每个未知点都必须向进程日志输出 WARNING，并在 `operator_log` 写入一条 `log_type=LOG_WARNING` 的 UTF-8 JSON 告警；`event=unknown_scada_point`，至少包含中文 `message`、`schema_version`、`source`、`signal`、`pnt_no`、`received_name`、`simu_time`。
6. `simu_time <= 0` 时已定义点不响应、不更新；若点号本身未知，仍记录未知点告警。
7. 停止启动清理、模拟器时钟回退清理只能把已有四遥的 `value/time` 归零，必须保留 `pnt_no/name`。

当前协议的数据接收方向是模拟器向 EMS 返回 YC/YX，控制发送方向是 EMS 从本地预定义点表读取 YT/YK 后发送。即使当前没有从外部反向接收 YT/YK 的入口，统一更新函数和测试也必须覆盖四种四遥，防止未来扩展绕过定义保护。

YC/YX 的 TCP 读取响应采用无点号的位置协议：请求携带本地预定义点号数组，响应项只包含 `value/time`，Bridge 必须根据“原请求数组索引”恢复本地点号后再调用统一更新函数。不得根据数据库查询顺序、返回值大小或其他特征猜测身份；不得接受响应中的 `pnt_no/name` 覆盖位置身份。

#### 4.6 主页曲线定义

不得创建 `curve_def` 表，也不得保留对应 ORM。旧数据库若存在该表，初始化迁移必须直接删除。MMI 不提供独立的“曲线定义”页面或增删改入口，以下 5 条首页曲线作为不可变配置直接写在 `operator_mmi.py` 中：

```text
负荷总功率     operator_history.load_power_curr_sum    #eb5757
风电当前功率   operator_history.wind_power_curr_sum    #2f80ed
光伏当前功率   operator_history.solar_power_curr_sum   #f2c94c
柴油当前功率   operator_history.diesal_power_curr_sum  #6f4e37
储能当前功率   operator_history.estore_power_curr_sum  #27ae60
```

正式库首次初始化时只写入 1 条控制行，不写入设备、四遥点、历史数据或曲线配置记录。

### 五、数据库初始化、迁移和并发

创建 `Database` 封装类，每个进程都必须创建自己的 SQLAlchemy engine，禁止跨进程共享 engine 或 Session。

SQLite 每个连接必须执行：

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=10000;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
```

SQLAlchemy `connect_args` 至少包括：

```python
{"timeout": 10.0, "check_same_thread": False}
```

所有写操作通过一个统一的短事务方法执行：

- 成功后提交。

- 异常后回滚。

- 仅当错误信息包含 SQLite `locked` 或 `busy` 时重试。

- 最多重试 7 次。

- 使用指数退避并加入少量随机抖动。

- 网络连接、等待和耗时计算不得放在数据库写事务内部。

- 成功完成一次 Bridge TCP 读取后把对应 `scada_rtu.status` 写为 1；读取或控制写入失败时立即写为 0，但不得覆盖最后一次成功的 `refresh_time`。

不要使用只能保护一个进程的 Python 全局锁来代替 SQLite 的跨进程并发机制。

`initialize_database()` 必须幂等，并支持已有旧库升级：

1. 旧 `operator_control` 缺少 `control_status`、`io_connect_enabled`、`data_period`、`data_time_curr`、`source_run_seq`、`source_time_start`、`source_runtime_ready` 时补列，其中 `io_connect_enabled` 的迁移默认值必须为 1，以保持既有自动连接行为；三个来源任务字段的迁移默认值必须为 0。

2. 若旧控制表字段顺序不同，在确认没有未知扩展列的前提下安全重建表，保留原值并调整到规定顺序。

3. 四遥历史表存在旧列 `simu_time` 且不存在 `time` 时，使用 SQLite `RENAME COLUMN` 无损改名。

4. `create_all()` 后对 ORM 声明的索引执行 `checkfirst=True` 的补建，保证旧表也能获得缺失索引。

5. 旧库若存在已经废弃的 `curve_def`，必须在初始化期间执行 `DROP TABLE curve_def`；不得重新创建或迁移其数据。

6. 旧 `operator_log.log_info` 若声明为 `VARCHAR(1024)` 或其他定长字符串，必须安全重建 `operator_log`，把该列升级为不限制决策审计长度的 SQLite `TEXT`，完整保留已有日志的 `id`、时刻、类型、内容和索引；迁移前后记录数与内容必须一致。

7. 旧 `dev_estore` 若存在已经废弃的 `soc_init`，必须删除该列并完整保留设备 ID、名称、状态、充放电参数、容量、`soc_curr`、`soc_max` 和 `soc_min`。新建库不得包含 `soc_init`。数据库初始化或升级只做结构迁移，不修改 `soc_curr`；停止后重新启动运行时也不得把 `soc_curr` 复位到 `soc_min`、0 或其他默认值。

8. 旧 `dev_wind_gen` 若存在已经废弃的 `angle_yaw_curr`，必须删除该列并完整保留其他风机字段；同时删除 `scada_yc` 中点名为 `<设备名称>.当前偏航角` 或 `dev_wind_gen.<id>.angle_yaw_curr` 的旧点定义及其 `scada_yc_his` 历史，并删除 `scada_yt` 中点名为 `<设备名称>.偏航角设定`、`dev_wind_gen.<id>.angle_yaw_set`、`dev_wind_gen.<id>.angle_yaw_setpoint`、`<设备名称>.桨距角设定`、`dev_wind_gen.<id>.angle_pitch_set` 或 `dev_wind_gen.<id>.angle_pitch_setpoint` 的旧点定义及其 `scada_yt_his` 历史。迁移必须幂等，不得删除当前有功、设备字段 `angle_pitch_curr`、YC“当前桨距角”、正常有功设定 YT 或其他非目标数据。

9. 删除 `scada_yc` 中已经废弃的“本步柴油消耗”“理论最大有功/理论最大功率/理论最大出力”“有功功率设定值”点定义以及这些点号在 `scada_yc_his` 中的全部历史。兼容删除 `dev_diesal_gen.<id>.diesal_curr`、`dev_diesal_gen.<id>.diesel_curr`、`dev_diesal_gen.<id>.step_diesel_consumption`、`dev_wind_gen.<id>.p_max_curr`、`dev_solar_gen.<id>.p_max_curr` 和 `dev_wind_gen.<id>.p_set` 等旧格式。这里删除的是 YC 定义：风机、光伏设备表的 `p_max_curr` 字段必须保留，正常有功设定 YT 也必须保留。迁移必须按被删除 YC 的点号同步清除历史，并且重复执行结果不变。

10. 四类可控设备表缺少 `control_mode` 时增加 `INTEGER NOT NULL DEFAULT 1`。只在本次确实迁移列时，为迁移设备一次性 `INSERT OR IGNORE` 本地 YX 属性 2，名称为“设备名称.控制模式”、值 1、时刻 0；已有同点号定义不得改名或覆盖。定义导入时源表允许缺少这一新增列，缺列按 1 导入，其他必需列仍严格校验。

#### 5.1 从已有 `power.db` 复制定义数据

必须生成独立脚本 `import_power_definitions.py`，支持：

```powershell
python import_power_definitions.py --source power.db --target ems.db --replace
```

该脚本用于落实“把 `power.db` 中的设备定义与 SCADA 定义在 `ems.db` 中复制一份”的要求，并必须遵守：

1. `power.db` 是用户已有的可选旧库，只能以 SQLite 只读 URI 打开，并同时启用 `PRAGMA query_only=ON`；10 张表的读取必须位于同一个只读事务中，以便源库被外部模拟器继续更新时仍获得一致快照。脚本不得创建、迁移、修改或删除源库，也不得把源路径与目标路径解析为同一个文件。

2. 只复制以下 10 张定义/实时表的行内容：

```text
scada_rtu
scada_yc
scada_yx
scada_yt
scada_yk
dev_diesal_gen
dev_wind_gen
dev_solar_gen
dev_estore
dev_load
```

不得复制 `operator_control`、`operator_log`、`operator_history` 或任何四遥历史表。

3. 目标 `ems.db` 必须先由 `initialize_database()` 创建 17 张规定业务表。不得复制源库 DDL、索引、触发器或 `sqlite_sequence`，不得为了适配源库增加目标字段；这就是“表结构不变，只改变内容”。

4. 按目标 ORM 列名逐列复制全部目标列的值，保留主键、点号、名称、浮点精度、状态、功率、四遥值和时刻。源表允许存在目标模型没有的历史兼容列，例如 `scada_rtu.conn_num`、`dev_wind_gen.angle_yaw_curr` 和 `dev_estore.soc_init`；这类源端多余列必须忽略并记录提示。源 `scada_yc` 中当前偏航角、“本步柴油消耗”、风机/光伏理论最大类、“有功功率设定值”及本节列出的对应旧格式点，以及源 `scada_yt` 中三种偏航角设定旧点和三种桨距角设定旧点，必须从可导入快照中剔除，不写入目标库。旧源四类可控设备表允许缺少新增 `control_mode`，此时明确填默认 1；除此之外任一目标必需列缺失都必须中止并给出表名和列名，不得静默填造业务值。

5. `--replace` 表示在一个目标短事务中先清空上述 10 张目标表，再写入源库记录；整个复制要么全部提交，要么全部回滚。未给出 `--replace` 且目标表非空时必须拒绝执行，防止无意覆盖。目标写入仍使用统一的 WAL、busy timeout 和锁冲突重试机制。

6. 脚本结束时打印每张表过滤后的有效源记录数、写入记录数和校验结果，并复查目标记录数、主键集合以及所有目标列的值与本次已剔除废弃点的只读快照完全一致。源端多余列和已废弃风机角度点不参与相等性校验。导入前后应尽力计算源文件 SHA-256；如果 Windows 共享占用导致无法直接哈希，或者外部模拟器在只读快照结束后继续写源库导致哈希变化，必须明确报告“哈希不可用”或“源库被外部更新”，不能把它误判为导入器修改源库，也不能在目标事务已经提交后因此谎报整个导入失败。只读 URI、`query_only` 和无任何源端写语句才是导入器不修改源库的强制安全边界。

7. 如果 AI 执行任务时当前目录或用户明确给出的路径中确实存在 `power.db`，必须实际运行一次复制脚本，把这 10 张表复制到正式 `ems.db` 并完成只读源库校验；如果不存在，不能伪造源库，应保留导入脚本和自动化测试，并使用下一节的独立演示数据完成运行验收。

### 六、示例数据

`seed_demo.py` 必须写入用户通过 `--db` 指定的数据库。README 中必须推荐使用 `ems_demo.db`，不得默认污染正式 `ems.db`。这里的确定性示例数据是没有旧 `power.db` 时的独立演示数据，不能冒充从旧库复制的真实定义数据。

示例控制参数：

```text
oper_status=0
control_status=1
data_period=1
oper_period=5
data_time_curr=0
oper_time_curr=0
source_run_seq=0
source_time_start=0
source_runtime_ready=0
```

示例设备：

```text
柴油发电机 1：id=1, p_rated=120, p_max=120, p_min=25,
              p_coeff=0.245, status=1, p_curr=25, p_set=25

柴油发电机 2：id=2, p_rated=80, p_max=80, p_min=18,
              p_coeff=0.255, status=1, p_curr=18, p_set=18

风机 1：id=1, p_rated=100, wind_in=3, wind_rated=11,
        wind_cut=25, status=1, p_curr=35

光伏阵列 1：id=1, p_rated=80, status=1, p_curr=40

电池储能 1：id=1, status=1, p_charge_max=50, p_charge_eff=0.95,
            p_discharge_max=50, p_discharge_eff=0.95,
            p_curr=0, p_set=0, battery_capacity=300,
            soc_curr=0.55, soc_max=0.9, soc_min=0.1

综合负荷 1：id=1, status=1, p_curr=145
```

示例 YC：

```text
1     simu.wind                       8.5
2     simu.solar                    750.0
3     amb_temp                       20.0
1001  dev_diesal_gen.1.p_curr        25.0
1002  dev_diesal_gen.2.p_curr        18.0
2001  dev_wind_gen.1.p_curr           35.0
3001  dev_solar_gen.1.p_curr          40.0
4001  dev_estore.1.p_curr              0.0
4002  dev_estore.1.soc_curr            0.55
5001  dev_load.1.p_curr              145.0
```

示例 YX：

```text
1001  dev_diesal_gen.1.status  1
1002  dev_diesal_gen.2.status  1
2001  dev_wind_gen.1.status    1
3001  dev_solar_gen.1.status   1
4001  dev_estore.1.status      1
5001  dev_load.1.status        1
```

示例实时四遥的 `time` 初值为 0，因此这些行初始只代表点定义，尚未成为有效断面。只有外部数据刷新或内核生成命令并把 `time` 更新为正数后，才允许响应执行。

### 七、独立风机最大理论出力函数

必须创建不依赖数据库、Session 或 ORM 模型的纯函数：

```python
def calculate_wind_max_power(
    current_wind_speed: float,
    p_rated: float,
    wind_in: float,
    wind_rated: float,
    wind_cut: float,
) -> float:
    ...
```

放在 `power_operator/wind_power.py`，并从 `power_operator.__init__` 公开导出。

功率曲线：

```text
v < wind_in:
    P = 0

wind_in <= v < wind_rated:
    P = p_rated * (v^3 - wind_in^3) / (wind_rated^3 - wind_in^3)

wind_rated <= v < wind_cut:
    P = p_rated

v >= wind_cut:
    P = 0
```

所有结果限制在 `[0, p_rated]`。下列情况返回 0，不能让 NaN 或无穷大进入调度：

- `p_rated <= 0`。

- `wind_in < 0`。

- `wind_rated <= wind_in`。

- `wind_cut <= wind_rated`。

- 任一参数不是有限数值。

内核可以保留接收 `DevWindGen` ORM 对象的兼容适配器，但实际计算必须委托给该纯函数。

光伏当前理论最大出力采用：

```python
p_max_curr = clamp(p_rated * max(0, irradiance) / 1000.0, 0, p_rated)
```

设备停止时，风机和光伏 `p_max_curr` 均为 0。

### 八、计算内核 `operator_core`

状态常量：

```python
OPER_STOPPED = 0
OPER_RUNNING = 1
OPER_PAUSED = 2

CONTROL_OPEN = 0
CONTROL_CLOSED = 1

LOG_INFO = 1
LOG_WARNING = 2
LOG_ERROR = 3
LOG_DECISION = 4
```

#### 8.1 常驻循环

- 默认每 0.5 秒读取一次 `operator_control`。

- 停止和暂停时不执行数据处理或决策，但继续轮询控制表。

- 新数据断面处理由数据库中的权威运行时钟驱动；控制决策间隔由 `time.monotonic()` 单调墙钟驱动。墙钟只判断周期是否到达，决策输入时刻、四遥时刻和 `oper_time_curr` 始终使用模拟器运行时刻，禁止用墙钟冒充运行时刻。

- 提供 `--poll`、`--pid-file` 参数和 `--once` 一次性运行入口。

- 默认 Core 以 MMI 的受管子线程运行，循环必须接受 `threading.Event` 等停止事件并用可中断等待替代不可控的永久 `sleep`；线程创建自己的 `Database/Engine`，不使用 MMI Session。显式独立运行 `operator_core.py` 时才使用按数据库绝对路径生成的 PID 文件，并保留 `--poll`、`--pid-file` 和 `--once`。

#### 8.2 停止切换为运行

内核必须记录上一次状态。检测到 `OPER_STOPPED -> OPER_RUNNING` 时执行一次运行数据复位：

- 清空 `operator_history`。

- 清空 `operator_log`。

- 清空四张四遥历史表。

- 保留 YC/YX/YT/YK 的点号和名称，但把值及 `time` 归零。

- 把 RTU `status` 和 `refresh_time` 归零。

- 保留设备名称、额定参数、上下限、效率和容量等静态参数。

- 把所有设备运行状态、当前功率和设定功率归零。

- 风机的理论最大出力、桨距角归零。

- 光伏理论最大出力归零。

- 储能 `soc_curr` 不复位、不清零。运行切换清理四遥值和时刻后，设备表保留清理前的 `soc_curr`，直到收到新的有效 SOC YC 后再更新。

- `data_time_curr` 和 `oper_time_curr` 归零。

暂停后继续运行不清理；必须只有停止后再次启动才清理。

#### 8.3 数据处理

当满足以下条件时处理一次新数据：

```text
oper_status == OPER_RUNNING
且 data_time_curr > 内核实例已处理的最后数据时刻
```

从实时表读取后必须先过滤有效点：

```text
有效 YC/YX：time > 0
无效 YC/YX：time <= 0，只保留定义，不更新任何设备字段
验收标签：valid YC/YX
```

点名映射格式：

```text
设备表名.设备ID.字段名
```

YC 可以更新：

```text
p_curr
angle_pitch_curr
soc_curr
```

除兼容 `设备表名.设备ID.字段名` 外，Core 必须直接使用系统已经定义好的中文 YC 点位，不得创建重复点。按设备名称精确匹配以下量测后缀：柴油、风机、光伏的“当前有功/当前功率/当前出力”更新 `p_curr`；风机“当前桨距角”更新 `angle_pitch_curr`；储能“当前功率/当前有功/当前出力”更新 `p_curr`，“当前SOC/SOC/当前荷电状态”更新 `soc_curr`；负荷“当前负荷/当前负荷值/当前功率/当前有功”更新 `p_curr`。只处理 `time > 0` 的 YC；多个有效点映射到同一设备字段时采用 `time` 最新的一条，重复设备名称无法唯一识别时保持原值而不能猜测。`soc_curr` 只能由有效 SOC YC 更新：启动或时钟回退后如果尚未收到有效 SOC YC，必须保留设备表当前 `soc_curr`，不得用 `soc_min`、0、历史设定值或策略推算值覆盖。

风机设备表 `p_max_curr` 不是外部 YC 量测：每次处理新数据断面时，Core 必须读取本断面有效环境风速，并对每台风机使用其自身 `p_rated`、`wind_in`、`wind_rated`、`wind_cut` 调用 `calculate_wind_max_power()`，把结果写入该设备的 `p_max_curr`；停止设备写 0。任何名为“理论最大有功/理论最大功率/理论最大出力”或兼容字段名 `dev_wind_gen.<id>.p_max_curr` 的 YC 都不能创建、更新或覆盖该字段。光伏 `p_max_curr` 同理由 Core 根据太阳辐照和额定功率计算，不从理论最大类 YC 取值。

已废弃的当前偏航角 YC、“本步柴油消耗”YC、风机/光伏理论最大类 YC、“有功功率设定值”YC、偏航角设定 YT、桨距角设定 YT 及其历史数据在数据库升级时删除，旧库定义导入必须过滤，IO 再次收到时必须静默忽略，Core 不更新、不保存历史，MMI 不显示；不能把这些已知废弃点误报为普通未知点。设备字段 `p_max_curr`、`angle_pitch_curr`、YC“当前桨距角”和正常有功设定 YT 继续正常使用。

YX 更新 `status` 和 `control_mode`，且只允许使用 `time > 0` 的实时遥信。状态除兼容 `设备表名.设备ID.status` 外，还必须识别 `<设备名称>.运行状态`；必要时可用同点号预定义 YK 识别设备，但绝不能把 YK 目标当实际状态。控制模式必须识别 `<设备名称>.控制模式` 或固定属性 2 点号 `设备ID*100+2`，值严格等于 1 时写闭环，其他整数安全回退开环。多个有效 YX 映射到同一字段时使用时刻最新的一条；`time=0` 不覆盖上次有效模式。

支持的环境遥测名称：

```text
风速：simu.wind、weather.wind、wind_speed、环境.当前风速
辐照：simu.solar、weather.solar、solar_radiation、simu.sloar、环境.当前太阳辐照
环境温度：simu.temp、weather.temp、amb_temp、环境.当前温度
```

每次新数据处理必须：

1. 更新设备实时值和状态。

2. 调用独立纯函数更新每台运行风机的 `p_max_curr`。

3. 计算每台运行光伏的 `p_max_curr`。

4. 保存或合并该时刻的 `operator_history`。

5. 只把 `time > 0` 的 YC/YX/YT，以及确实要求设备状态变化的正时刻 YK 保存到四遥历史表；`time <= 0` 或目标状态已经与实际状态一致的 YK 不能产生历史记录。

6. 写入一条数据处理完成日志。

7. 柴油累计消耗按以下公式增加，并且同一数据时刻只能累计一次：

```text
diesal_curr_sum += sum(max(p_curr, 0) * max(p_coeff, 0))
                   * data_period / 3600
```

#### 8.4 新能源优先策略

首次出现尚未决策的有效运行断面时立即执行一次决策；以后当同时满足以下条件时执行下一次决策：

```text
data_time_curr > oper_time_curr
且 monotonic_now - last_successful_decision_monotonic >= oper_period
```

模拟器运行时刻可能在一个数据采集周期内跨过 60 秒或更多；不能因此按跨过的运行秒数补执行多个决策，也不能每个数据断面都绕过墙钟周期。每个 Core `tick` 最多执行一次决策，并使用当时最新的 `data_time_curr` 作为该次决策时刻。只有决策事务成功提交后才更新进程内的上次成功决策单调时刻；失败允许后续轮询重试。停止、暂停或停止后重新启动时清除进程内调度基准，恢复运行后仍按“首次有待决策的新断面立即执行，后续按墙钟周期执行”的规则重新计时。

负荷取所有运行负荷的非负当前功率合计。四类设备先按实时 `control_mode` 分组：运行且开环设备不进入 EMS 可控集合，其实时 `p_curr`（储能可为负）作为固定功率贡献；停机开环设备不计入。闭环且运行设备才进入新能源、柴发和储能可控集合，调度净需求为 `load_kw - open_loop_fixed_power_kw`。

策略顺序：

1. 风电和光伏优先使用理论最大可用功率。

2. 运行中的柴油机必须先保持在各自的 `p_min`，但不得超过 `p_max`。

3. 当新能源和柴油下限之和高于负荷时，先让储能充电吸收富余功率。

4. 储能充电后仍有富余时形成新能源弃电。

5. 不可避免的新能源削减按“先保留风电，再保留光伏”的顺序分配，即先使用风电，剩余需求再使用光伏。

6. 新能源不足时，柴油机按设备 ID 顺序从 `p_min` 增加到 `p_max`。

7. 新能源与柴油最大出力仍不足时，储能按设备 ID 顺序放电。

8. 仍不足的功率记录为 `unserved_kw`。

9. 计算并返回总负荷、新能源设定、柴油设定、储能设定、弃电、失供和功率平衡误差。

储能充放电限制必须同时考虑功率、SOC、容量、效率和决策周期：

```text
hours = max(oper_period, 1) / 3600

room_kwh = max(0, (soc_max - soc_curr) * battery_capacity)
energy_kwh = max(0, (soc_curr - soc_min) * battery_capacity)

charge_limit = min(
    max(p_charge_max, 0),
    room_kwh / (hours * clamp(p_charge_eff, 1e-6, 1))
)

discharge_limit = min(
    max(p_discharge_max, 0),
    energy_kwh * clamp(p_discharge_eff, 1e-6, 1) / hours
)
```

策略只使用当前 YC 提供的 SOC 计算限制，不在内核内猜测或重复推进 SOC；下一数据断面由外部数据源更新 `soc_curr`。

如果当前数据断面没有任何可唯一映射且 `time > 0` 的 SOC YC，则本周期保留设备表已有 `soc_curr`；不得因为 YC 被清零、连接暂时中断、系统重新启动或模拟器时钟回退而自行重置 SOC。

#### 8.5 开环和闭环

系统全局控制方式与设备 `control_mode` 是两级门控。每次决策只对闭环设备计算可控设定；开环设备的本地 `p_set` 同步为其当前 `p_curr`，仅用于固定贡献记录，不得生成外部命令。

开环 `CONTROL_OPEN`：

- 全部设备不创建新的 YT/YK。

- 不发送已有 YT/YK；审计原因保持 `open_loop`。

闭环 `CONTROL_CLOSED`：

- 仅 `device.control_mode == 1` 的设备进入可控集合并生成/发送 YT/YK。`control_mode=0` 设备的既有 YT/YK 时刻必须清零，审计原因记为 `device_open_loop`；IO 发送边界还必须按设备模式二次过滤，防止遗留命令外发。

- 每次闭环控制决策都把有功设定写入预定义 YT，并无条件把 `time` 刷新为本轮控制时刻；不得先比较 YT 原 `value` 再决定是否更新时间。即使新旧设定值完全相同，也必须重写 `value/time`、生成本轮有效 YT、进入本轮历史并由 IO 按新时标发送。决策审计中值相同时记录 `reason=setpoint_time_refreshed`。

- YK 的 `value` 表示决策后的目标启停状态。先保存当前实际状态，再计算目标状态；只有 `current_status != target_status` 时才创建或更新 YK，并将 `time` 设置为当前控制时刻。

- 当前实际状态优先取最新有效 YX；找不到对应有效 YX 时才回退到设备表 `status`。无法确认当前状态时必须禁止下发，而不能猜测状态。

- `current_status == target_status` 时不得新增 YK；如果同一点已有旧 YK，则保留点号和点名、把值同步为当前目标并将 `time` 重置为 0，防止旧命令进入历史或再次发送。

- 状态差异持续存在时，允许后续决策使用新的控制时刻再次生成同一目标命令；状态反馈一致后立即停止生成。

- 当前新能源优先功率策略不主动改变设备启停状态，因此不得把设备当前 `status` 原样回写成有效 YK。只有未来或扩展的启停策略明确产生不同目标状态时才生成 YK。

命令点号：

```text
柴油机：100000 + id
风机：  200000 + id
光伏：  300000 + id
储能：  400000 + id
```

YT 点名示例：

```text
dev_wind_gen.1.p_set
```

YK 点名示例：

```text
dev_wind_gen.1.status
```

完成决策后更新 `oper_time_curr=data_time_curr`，保存历史断面和完整决策审计日志。`oper_time_curr` 只表示最近一次决策实际使用的运行断面时刻，不得反过来用于计算墙钟决策间隔。

#### 8.6 决策过程审计日志

每一次实际触发的控制决策，无论开环还是闭环，都必须向 `operator_log` 写入且只写入一条 `log_type=LOG_DECISION` 的完整决策审计记录，供用户在 MMI 中浏览和查阅。不能只记录“决策完成”“控制成功”或几个汇总数字。

决策日志要求：

1. `log_time` 使用生成该决策记录时的 Unix 墙钟秒；`simu_time` 使用本次 `data_time_curr`，并与本次写入 `oper_time_curr` 的值一致。

2. `log_info` 保存 UTF-8 JSON 对象，使用 `json.dumps(..., ensure_ascii=False, allow_nan=False)` 序列化；非有限浮点数必须在决策校验阶段拒绝或转换成带原因的结构化警告，不能写出非标准 JSON。禁止保存 Python `repr`、无法解析的拼接字符串或截断后的 JSON。JSON 至少采用以下稳定结构：

```json
{
  "schema_version": 1,
  "event": "control_decision",
  "decision_id": "decision-3600-000001",
  "mode": "open",
  "trigger": {},
  "inputs": {},
  "process": [],
  "outputs": {},
  "validation": {}
}
```

3. `decision_id` 在数据库中必须可追踪且不重复，至少包含控制时刻和进程内决策序号；ID 生成器应支持测试注入，不能依赖不稳定的对象内存地址。

4. `trigger` 必须记录本次决策为什么被触发，至少包含：

```text
oper_status
control_status / mode
data_period
oper_period
data_time_curr
previous_oper_time_curr
decision_wall_time
```

5. `inputs` 必须保存算法实际使用的完整输入断面，至少包含：

- 当前风速、太阳辐照和负荷总功率。

- 所有负荷的 ID、名称、当前状态、当前功率、是否纳入本次计算及未纳入原因。

- 所有柴油机的 ID、名称、状态、`p_curr`、原 `p_set`、`p_rated`、`p_min`、`p_max`、`p_coeff`、是否纳入计算及原因。

- 所有风机的 ID、名称、状态、`p_curr`、原 `p_set`、`p_rated`、`wind_in`、`wind_rated`、`wind_cut`、`p_max_curr`、是否纳入计算及原因。

- 所有光伏的 ID、名称、状态、`p_curr`、原 `p_set`、`p_rated`、`p_max_curr`、是否纳入计算及原因。

- 所有储能的 ID、名称、状态、`p_curr`、原 `p_set`、充放电功率上限、充放电效率、容量、`soc_curr`、`soc_min`、`soc_max`、本周期可充/可放功率限制、是否纳入计算及原因。

- 本次断面中供策略输入追溯使用的有效 YC/YX 点，包括点号、点名、值和运行时刻。只有 `time > 0` 的点可以列入 `valid_yc`、`valid_yx`；被排除的点应在 `excluded_points` 中记录点号、点名、时刻和排除原因。

6. `process` 必须按真实执行顺序记录控制策略的每一步，而不是事后拼一个固定模板。每个步骤至少包含 `step`、`name`、`before`、`action`、`after` 和 `reason`。按是否实际发生记录：新能源优先分配、柴油机下限、储能充电、风光削减及分配、柴油机增发、储能放电、失供计算和启停状态判定。没有执行的分支也要通过 `executed=false` 和原因说明为什么跳过。

7. `outputs` 必须记录完整计算结果：

- 每台可控设备的设备类型、ID、名称、当前状态、目标状态、`p_curr`、原 `p_set`、新 `p_set` 和设定值变化量。

- 每个拟生成或失效的 YT/YK 的点号、点名、值、时刻、`generated` 标记和原因。开环模式必须明确记录“仅计算设备 `p_set`，不生成 YT/YK”；闭环 YT 值相同但刷新时标时必须记录 `reason=setpoint_time_refreshed`；状态一致而未生成 YK 时必须记录 `current_status`、`target_status` 和 `reason=status_unchanged`。

- 风电、光伏、柴油、储能的当前总值和新设定总值、负荷总值、储能充电量、储能放电量、新能源削减量 `curtailment_kw`、失供量 `unserved_kw`。

8. `validation` 至少记录功率平衡公式的各项、`balance_error_kw`、允许误差和 `within_tolerance`。如果存在削减、失供、输入异常、达到设备/SOC边界或平衡误差超限，必须同时给出结构化 `warnings`；严重错误使用单独的 `LOG_ERROR` 日志，并且不能伪装成成功决策。

9. 决策输入断面、设备 `p_set`、YT/YK、`operator_history`、`oper_time_curr` 和 `LOG_DECISION` 日志必须在同一个短数据库事务中提交。应先在事务外完成纯计算和 JSON 构造，再开启短事务写入；任一写入失败时整体回滚，不能出现“设备设定已经改变但没有对应决策日志”或“有日志但决策输出未提交”的情况。

10. 浮点数在 JSON 中保留计算精度，不能为了界面显示而反写成三位小数；MMI 展示输入、过程和输出时统一格式化为三位小数。

### 九、`operator_io` Bridge 和 TCP 协议

默认结构必须是：

```text
operator_mmi/operator-io 子线程 --TCP client--> simulator_io
```

采用 UTF-8 JSON Lines：一条 TCP 连接发送一行 JSON 请求并接收一行 JSON 响应。限制单行最大 2 MiB，设置连接超时，检查 JSON 类型和 `ok` 状态。

#### 9.1 数据读取

只有 `oper_status == 1` 时工作。按照墙钟 `data_period` 周期向 `simulator_io` 发起：

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

请求中的 `simu_time` 是供项目 Mock 和不维护自身时钟的兼容对端使用的建议值，默认为：

```text
当前 data_time_curr + data_period
```

成功响应示例：

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
      {"value": 0.5, "time": 2},
      {"value": null, "time": 0},
      {"value": 8.5, "time": 2},
      {"value": 0.5, "time": 2},
      {"value": 9.9, "time": 0}
    ],
    "yx": [
      {"value": 0, "time": 2},
      {"value": 1, "time": 2}
    ]
  }
}
```

要求：

- `data.yc/data.yx` 请求数组由本地预定义 YC/YX 点号组成，两张表可以使用完全不同的列表；点号允许任意乱序，显式空数组返回空数组。

- 响应采用严格的位置映射。服务端必须逐项遍历原请求列表返回结果，禁止排序、去重、按集合迭代、跳过未知点或跳过 `time=0` 点。第 N 个响应项必须对应第 N 个请求点号，重复点号在每个原位置重复返回。

- 每个响应项的键集合必须严格等于 `{"value","time"}`，不得返回 `pnt_no`、`name`、索引或其他字段。Bridge 只能根据原请求位置恢复本地点号；YC/YX 响应数量必须分别与对应请求数量完全相等，数量不符、混入额外字段或数据项类型错误时拒绝整批响应并把 RTU 状态置为 0。

- 已定义点无论 `time` 是否为 0 都必须返回自身 `value/time`。未知点必须在原位置返回 `{"value":null,"time":0}`，不得省略；`simulator_io` 同时向进程日志和模拟器日志表记录未知 YC/YX 点号告警。告警可按表聚合同一批未知点，但不能影响成功响应中其他位置。

- YC/YX/YT/YK 的 `pnt_no`、`name` 和其他定义字段在运行期只读。Core/IO 子线程、显式兼容入口和其他后台工作单元只能修改本地已有点的 `value` 和 `time`；严禁根据通信报文创建点位、覆盖 `name` 或修改 `pnt_no`。本地 `pnt_no` 是运行期更新的唯一身份键，报文 `name` 不参与写库；点位创建和定义修改只能由显式建库、定义导入或 MMI 四遥定义操作完成。

- 收到本地表中不存在的 YC/YX/YT/YK 点号时，不得创建点位，也不得使同批其他有效点回滚。必须忽略该未知点，向进程日志输出 WARNING，并在 `operator_log` 写入一条 `log_type=LOG_WARNING` 的 UTF-8 JSON 告警。JSON 的 `event` 固定为 `unknown_scada_point`，至少包含便于浏览的中文 `message`、`schema_version`、`source`、`signal`、`pnt_no`、`received_name` 和 `simu_time`。四种四遥必须使用同一个运行期更新/校验函数，避免不同入口出现不同定义保护行为。

- 成功的 YC/YX 响应顶层必须显式包含非负整数 `simu_time`。它是模拟器权威运行时刻，Bridge 不得在字段缺失时用请求建议值或本机墙钟代替。

- 新版模拟器和项目 Mock 的成功响应必须成组携带任务元数据：非负整数 `run_seq`、非负整数 `simu_time_start`、取值为 0/1/2 的整数 `simu_status` 和布尔值 `runtime_ready`。只要出现 `run_seq`，其他三个字段就都必须存在且类型、范围正确，否则拒绝整批响应。`run_seq` 是权威任务身份；即使新任务时刻等于或大于本地旧时刻，只要任务序号变化也必须执行 9.2 的新任务恢复流程。为兼容尚未升级的旧对端，完全不含 `run_seq` 的响应继续使用已有时钟回退判断，不得伪造任务边界。

- 对带任务元数据的响应，只有 `runtime_ready=true` 且本包至少包含一个 `value != null`、`time > 0` 的 YC/YX 项时，才认为首个有效断面已经就绪。未就绪包可以同步任务序号、任务起始时刻和包级运行时刻，但不得把零时刻占位应用为业务量测，也不得启动 Core；首个有效断面到达并成功提交后才把 `source_runtime_ready=1` 并恢复 Core。

- 响应时刻允许等于本地时刻，也允许在模拟器归零重启后小于本地旧时刻；Bridge 必须把 `operator_control.data_time_curr` 精确同步为响应值，不能使用 `max()` 阻止时钟复位。小于本地时刻时必须先执行 9.2 的完整恢复流程，禁止直接覆盖时钟。

- YC/YX 点内 `time` 是该点自身的数据时刻。Bridge 只应用 `0 < point.time <= response.simu_time` 的项；`time=0` 的已定义点或未知点占位仍参与位置映射，但不更新本地值、不更新设备、不进入历史。负时刻或晚于包级 `simu_time` 的点必须拒绝。兼容 Server 收到 `simu_time <= 0` 的 YC/YX 时同样不得写入这些点。

- 响应成功后在一个短事务中原子更新有效 YC、有效 YX、RTU 和 `data_time_curr`；`data_time_curr` 使用包级 `simu_time`，每个有效 YC/YX 的本地 `time` 使用对应响应项的 `time`，RTU `refresh_time` 使用本次成功交换的 Unix 墙钟。

- YC/YX 点内 `time`、包级 `simu_time` 和 RTU 墙钟 `refresh_time` 三者不得混用。

- 成功后把 RTU `status` 写为 1；读取失败时把状态写为 0，并保留最后一次成功的刷新时刻。

- 每次 Bridge tick 先读取 `operator_control.io_connect_enabled`。其值为 0 时不得发起任何 TCP 读取或写入，立即把 RTU `status` 置为 0，但保留 `refresh_time`，并重置周期计时，使其恢复为 1 后下一次 tick 立即尝试连接。

#### 9.2 模拟器新任务与时钟回退恢复

新版响应满足以下任一条件时，必须进入生命周期恢复流程：

```text
response.run_seq != operator_control.source_run_seq
或
response.simu_time < operator_control.data_time_curr 且 run_seq 未变化
```

第一种条件表示权威模拟任务已经切换，即使新任务时刻前进或相等也必须清理；第二种表示同一任务内模拟器时钟回退。完全不含任务元数据的旧响应仍按第二种条件兼容。必须严格执行以下顺序，禁止调换：

```text
停止并确认受管 Core 子线程退出
→ 本地数据时钟和控制时钟回退到 0
→ 清理历史、日志、四遥实时值/时刻和 Bridge 周期状态
→ 保存任务元数据并应用已就绪的首个有效 YC/YX 断面和权威时刻
→ 提交事务
→ 仅在首个有效断面就绪后重新启动受管 Core 子线程
```

具体要求：

1. 默认 IO 子线程必须注入同一 MMI 所有的 `CoreThreadController`，只能停止和重启该控制器创建的 Core 线程，禁止扫描或终止 Python 进程。显式独立 `operator_io.py` 兼容入口仍可注入 `CoreProcessManager`，但不得与 MMI 托管模式同时运行。

2. 线程控制器必须持有明确的线程对象、停止事件、线程代次、数据库绝对路径和有界停止超时；只接受自己创建的活动线程。停止失败或退出超时时立即报错，禁止修改本地时钟、历史、日志和四遥数据。

3. Core 停止与重启必须在 SQLite 写事务之外；停止后必须 `join` 并确认旧线程已退出，才能进入清理事务。

4. 清理和新数据应用使用同一个短事务：先把 `data_time_curr=0`、`oper_time_curr=0`，删除 `operator_history`、`operator_log`、`scada_yc_his`、`scada_yx_his`、`scada_yt_his`、`scada_yk_his`；保留 YC/YX/YT/YK 点号和名称，把所有实时四遥的 `value` 与 `time` 归零；写入 `source_run_seq`、`source_time_start` 和本包计算得到的 `source_runtime_ready`。若首断面已就绪，则按原请求位置应用本批 `time > 0` 的 YC/YX；未就绪时不应用零时刻占位。各点 `time` 使用各自响应项时刻，`data_time_curr=response.simu_time`，零时刻占位保持归零且不能导致后续点错位。该清理事务不得修改任何设备的 `soc_curr`；后续 Core 只在本批或后续批次包含有效 SOC YC 时更新 SOC。

5. `curve_def` 不存在，无曲线配置表需要清理；历史曲线数据已经包含在 `operator_history` 和四张四遥历史表的删除范围内。不得清空设备静态定义。

6. Bridge 内存中的数据采集周期计时、1 秒控制发送周期计时、YT 已发送游标、YK 已检查游标都归零。成功恢复的当前 tick 不得继续发送旧命令。

7. 本批成功交换的 `scada_rtu.refresh_time` 继续使用 Unix 墙钟，绝不能写成回退后的 `simu_time`；RTU 状态在完整恢复成功后为 1。

8. 清理或数据应用失败时，SQLAlchemy 事务必须完整回滚；如果 Core 在进入本次恢复前处于可运行状态，必须尝试恢复 Core。首断面尚未就绪时 Core 保持停止，不得因为轮询下一包而重复停止同一代线程。首断面提交后重启失败必须抛出并把连接状态标为中断，不能伪装成功。

9. 仅当 `run_seq` 未变化且响应时刻等于或大于本地时刻时，才走普通原子更新，不停止 Core、不清历史、不清日志。任务序号变化的优先级高于时刻方向。

#### 9.3 控制命令发送

Bridge 每 1 秒检查一次 `scada_yt` 和 `scada_yk` 中 `time > max(0, 本进程发送游标)` 的命令。任何 `time <= 0` 的 YT/YK 都不得进入发送载荷。发送出口必须再次按点名过滤已经废弃的风机偏航角设定和桨距角设定 YT；即使旧点在进程运行期间被外部工具意外写回，也不得发送，并且必须按已检查候选点的最大 `time` 推进 YT 发送游标，避免每秒重复扫描同一废弃点。兼容 Server 返回 YT 时执行相同过滤。对 YK 必须在发送出口再次比较目标值与最新有效 YX/设备状态：只发送状态不一致的 YK；状态一致或当前状态未知的 YK 不得进入载荷。状态差异仍存在时，不比较旧 YK `value` 是否相同，每次控制决策都用本轮控制时刻刷新 YK `time`。兼容 Server 返回 YK 时执行同样复核。每个非空写请求顶层必须携带当前 `operator_control.source_run_seq`，使模拟器能拒绝来自旧任务的迟到控制命令。

请求示例：

```json
{
  "action": "write",
  "run_seq": 7,
  "data": {
    "yt": [
      {
        "pnt_no": 200001,
        "name": "dev_wind_gen.1.p_set",
        "value": 62.5,
        "time": 10
      }
    ],
    "yk": [
      {
        "pnt_no": 200001,
        "name": "dev_wind_gen.1.status",
        "value": 1,
        "time": 10
      }
    ]
  }
}
```

只有响应是对象、明确包含 `{"ok": true}`，并且没有非空 `rejected` 字段时才推进 YT/YK 游标。连接失败、超时、非对象响应、`ok!=true` 或任何部分拒绝都必须抛错并保留游标，使命令可以整批再次发送，同时把对应 RTU 状态写为 0。

系统停止、暂停、`io_connect_enabled=0` 或 Bridge 正常退出时，也要把对应 RTU 状态写为 0。Bridge 再次成功交换后恢复为 1。禁止仅因 `io_connect_enabled=1` 就把 RTU 状态写为 1。

#### 9.4 兼容 Server 模式

`operator_io.py` 支持：

```text
--mode bridge|server
--simulator-host
--simulator-port
--rtu-id
--poll
--listen-host
--listen-port
```

默认 `--mode bridge`、目标 `127.0.0.1:9001`，与真实 `simulator_io` 默认监听端口一致。旧 Server 模式必须显式指定 `--mode server`，默认监听 `127.0.0.1:9100`，并提供 `rtu_client.py` 示例。

### 十、`simulator_io_mock`

实现可多线程处理连接的 JSON Lines TCP Server：

- 默认监听 `127.0.0.1:9200`。

- `action=read` 读取 `data.yc/data.yx` 点号数组并返回前述严格位置协议，把请求中的 `simu_time` 原样作为响应时刻；顶层同时返回可配置的非负 `run_seq`、`simu_time_start`、0/1/2 `simu_status` 和布尔 `runtime_ready`。Mock 必须逐项保持乱序和重复点，已定义点在 `time=0` 时仍返回自身值及零时刻，未知点返回 `{"value":null,"time":0}` 占位并输出 WARNING；返回项不得包含 `pnt_no/name`。

- 风速和辐照可以基于时刻生成简单、可重复的变化。

- `action=write` 从 `data.yt/data.yk` 接收控制点，校验顶层 `run_seq`。序号与当前 Mock 任务不一致时返回 `ok=false`、当前任务序号和当前运行时刻，且不得执行任何命令；一致时记录日志，并返回当前任务序号、运行时刻和接收数量。

- Mock 只执行 `time > 0` 的 YT/YK；`time <= 0` 的控制项必须忽略，且不得计入 `accepted_yt` 或 `accepted_yk`。

- 收到设备 `p_set` 后，可以在后续 `read` 中把对应值作为该设备的 `p_curr`，形成可观察的闭环演示。

- Mock 只使用内存，不创建或修改 `ems.db`。

### 十一、时间格式工具

创建独立函数：

```python
format_simu_time(seconds) -> str
parse_simu_time(text) -> int
format_wall_time(unix_seconds) -> str
parse_wall_time(text) -> int
```

要求：

- `3661 -> "01:01:01"`。

- `90000 -> "25:00:00"`，小时超过 24 不回绕。

- 运行秒数的统一显示格式写作 `HH:mm:ss`：`HH` 为累计小时，`mm` 为分钟，`ss` 为秒。这里的大小写采用 Qt 格式语义，分钟必须是小写 `mm`，不得误写成表示月份的大写 `MM`。小时可以大于 23，因此不能直接使用会在 24 小时回绕的 `QTime`；应按整数秒手工计算并格式化，解析时分钟和秒必须位于 0～59。

- YC、YX、YK、YT 实时表的 `time`、四遥历史曲线、数据时刻、控制时刻和曲线横轴均使用 `HH:mm:ss` 展示。

- `format_wall_time()` 把正 Unix 秒按本机时区显示为“年-月-日 时:分:秒”，具体输出为 `yyyy-MM-dd HH:mm:ss`，例如 `2026-08-23 14:05:09`，0 显示为 `--`；Python 格式串必须使用 `%Y-%m-%d %H:%M:%S`，Qt 日期时间格式串使用 `yyyy-MM-dd HH:mm:ss`。`parse_wall_time()` 支持该显示格式及原始 Unix 整数。RTU `refresh_time` 只能使用这组墙钟函数，不能调用 `format_simu_time()`。

### 十二、PyQt6 MMI

必须先创建 `operator_mmi_qt.ui`，再实际执行：

```powershell
pyuic6 operator_mmi_qt.ui -o operator_mmi_qt.py
```

`operator_mmi.py` 导入 `Ui_OperatorMainWindow`。禁止只手写一个 Python UI 而不生成 `.ui`；也禁止手工伪造 `operator_mmi_qt.py`。

窗口默认尺寸约 1480×920，最小尺寸约 1050×680。整体上下结构：

#### 12.1 顶部运行控制区

高度尽量小，但不能层叠。采用两行紧凑网格加右侧紧凑按钮组：

第一行：

```text
运行状态 | 停止/运行/暂停组合框
控制模式 | 开环/闭环组合框
数据时刻 | HH:mm:ss
控制时刻 | HH:mm:ss
```

第二行：

```text
数据周期（秒） | SpinBox
决策周期（墙钟秒） | SpinBox
说明文本
```

按钮：

```text
保存参数     | 手动刷新参数
启动 / 继续 | 暂停 | 停止
```

顶部中间空白区域必须增加始终可见的“电网模拟器连接”状态卡，与控制区保持同一行且不遮挡按钮：任一 `scada_rtu.status=1` 时显示绿色“正常”，没有 RTU 或全部状态为 0 时显示红色“中断”。该卡片是界面唯一的连接状态摘要，无论用户当前打开哪个页面都每 1 秒刷新；RTU / 四遥定义页不得重复放置连接状态卡或连接详情信息栏。

在连接状态卡右侧、运行操作按钮组左侧增加两个紧凑按钮：`建立连接`、`中断连接`。两者必须通过 `operator_control.io_connect_enabled` 控制常驻 Bridge，不能由 MMI 启停外部进程，也不能直接把 `scada_rtu.status` 置为 1：

- 点击“中断连接”：写 `io_connect_enabled=0`，并立即把已有 RTU 的 `status` 置 0，保留 `refresh_time`；“建立连接”启用，“中断连接”禁用。

- 点击“建立连接”：只写 `io_connect_enabled=1`，由 Bridge 下一次 tick 发起连接；真实 TCP 成功前 RTU 状态和顶部状态仍为“中断”；“建立连接”禁用，“中断连接”启用。

- 默认 `io_connect_enabled=1`，因此初始“建立连接”禁用、“中断连接”启用。按钮的启用状态表示连接请求，不表示 TCP 实际结果。

保存参数一次写入 `control_status`、`data_period`、`oper_period`。用户修改任一参数控件后，该控件使用橙黄色背景和边框标记未保存状态，保存按钮启用；固定 1 秒刷新仍更新运行状态、双时钟和连接状态，但不得覆盖这些脏参数。状态组合框以及启动、暂停、停止按钮只写 `oper_status`，绝不能顺带写入或保存待提交的控制参数。“手动刷新参数”在存在未保存修改时先确认是否放弃，确认后才从数据库重载并清除脏状态；取消时必须原样保留用户输入。界面使用两个相互独立的 `QTimer`：第一个固定每 1 秒刷新顶部控制状态、双时钟、连接状态和系统主页；第二个以数据库已保存的 `max(1, data_period) * 1000` 毫秒为周期，只刷新当前的设备定义、RTU / 四遥定义、运行日志或历史曲线页。数据周期被外部进程或界面保存后，页面定时器必须在下一次 1 秒状态刷新时同步新间隔。`oper_period` 只驱动控制决策，绝不能用于界面刷新定时器。

切换任一主页面时必须立即刷新目标页面一次，不等待页面数据周期；进入设备定义、RTU / 四遥定义、运行日志或历史曲线后，从切换时刻重新启动页面周期计时，进入系统主页后停止该页面定时器。

#### 12.2 下部五个页面

使用高度自适应的 `QTabWidget`：

1. 系统主页：

   - 从最新 `operator_history` 读取所有字段。

   - 使用只读文本框网格展示当前运行断面。

   - 下部左右分栏：左边为可多选的首页曲线列表，右边为曲线板。

   - 支持全选和清空。

2. 设备定义：

   - 包含柴油机、风机、光伏、储能、负荷五个子页。

   - 表格显示 ORM 的全部字段。

   - 页面顶部设置紧凑的编辑说明、“手动刷新参数”和“保存设备修改”按钮。静态参数单元格使用浅黄色背景，双击、单击已选单元格或按编辑键均可进入编辑；已有设备 ID 和实时字段使用浅灰色背景并保持只读。人工修改后的单元格立即改为橙黄色，并使所属表格显示橙色边框；保存按钮只有在设备表存在未保存修改时才启用。

   - 允许编辑的静态参数必须严格限定为：

     - 柴油机：`name`、`p_rated`、`p_max`、`p_min`、`p_coeff`；
     - 风机：`name`、`p_rated`、`wind_in`、`wind_rated`、`wind_cut`；
     - 光伏：`name`、`p_rated`；
     - 储能：`name`、`p_charge_max`、`p_charge_eff`、`p_discharge_max`、`p_discharge_eff`、`battery_capacity`、`soc_max`、`soc_min`；
     - 负荷：`name`。

   - `status`、`p_curr`、`p_set`、`p_max_curr`、`angle_pitch_curr`、`soc_curr` 等实时运行字段必须只读；其中风机 `p_max_curr` 只能由控制内核依据有效环境风速和该风机静态参数计算，不能由 YC、MMI 或电网模拟器直接写入。保存设备参数时只更新上述白名单静态字段，绝不能把界面加载时的旧实时值写回并覆盖其他进程的新数据。

   - 已有记录的主键 `id` 只读；末尾空白行的 `id`、`name` 和相应静态参数可编辑，用于新增设备，未填写的数值字段使用 ORM 默认值。设备名称用于匹配系统预定义 YC/YX/YT/YK 点名，界面必须提示用户改名后同步维护相关四遥点名，不得擅自新建或重命名系统点位。

   - 保存前按 ORM 字段类型校验输入，五张设备表的修改在同一个 SQLAlchemy 写事务中提交。解析、约束或数据库写入失败时必须整体回滚，保留用户输入、脏状态和可用的保存按钮，并显示包含行号和字段名的错误；成功后重新读取数据库、清除脏状态并禁用保存按钮。

   - 支持修改现有记录，并通过末尾空白行新增设备。

   - 当前停留在本页时按 `data_period` 自动刷新；切换进入本页时立即刷新。

   - 只要任一设备表格正在编辑或已经产生未保存修改，切页刷新和周期刷新都必须跳过设备表重载，不能覆盖用户输入；只有保存成功，或用户点击“手动刷新参数”并确认放弃未保存修改后，才允许重新加载并清除脏状态。

3. RTU / 四遥定义：

   - 页面不得重复显示 `operator_io 连接状态` 卡片，也不得再放置“当前状态、连接成功/中断、RTU、对端、刷新时刻、在线数量”等摘要控件；连接状态统一查看全局顶部“电网模拟器连接”状态卡。

   - 全局顶部连接状态仍每 1 秒刷新；RTU 和四遥表格只按 `data_period` 自动刷新，切换进入本页时立即刷新。

   - 页面提供“手动刷新参数”和“保存 RTU / 四遥修改”按钮。人工修改后的单元格立即使用橙黄色背景，所属表格显示橙色边框并启用保存按钮。只要任一 RTU / 四遥表格正在编辑或已经产生未保存修改，切页刷新和周期刷新都必须跳过表格重载，不能覆盖用户输入；只有保存成功，或用户点击“手动刷新参数”并确认放弃未保存修改后，才允许重新加载并清除脏状态。

   - 下部必须展示 RTU、YC、YX、YT、YK 五个子页，而不是只有四个四遥子页。

   - RTU 表展示并允许保存 `scada_rtu`；`refresh_time` 列的中文表头显示为“刷新时刻”，按本地墙钟“年-月-日 时:分:秒”显示，具体格式为 `yyyy-MM-dd HH:mm:ss`，数据库仍保存 Unix 秒。

   - YC、YX、YT、YK 四个子页。

   - YC、YX、YK、YT 表格中的 `time` 列中文表头统一显示为“刷新时刻”，单元格以运行累计时间 `HH:mm:ss` 显示，保存时转换回整数秒。四遥刷新时刻不得套用 RTU 墙钟格式，RTU 刷新时刻也不得套用四遥累计时间格式。

4. 运行日志：

   - 页面用于浏览和查阅普通运行日志以及完整控制决策过程，所有控件只读，不允许在 MMI 中修改或删除日志。

   - 顶部提供日志类型、运行开始时刻、运行结束时刻、关键词过滤，以及“查询”“重置”按钮。日志类型至少包含“全部、信息、警告、错误、控制决策”；时刻过滤使用 `HH:mm:ss`，关键词可以匹配 `decision_id`、设备名、点名、策略步骤、警告和输出字段。

   - 主区域使用可调 `QSplitter`：左侧或上部为匹配的 `operator_log` 分页列表，至少显示墙钟时刻、运行时刻、类型、决策 ID 和摘要；右侧或下部为选中日志的详情区。日志数据的保存总量和用户可访问总量不得设置上限，但单次数据库查询和表格渲染必须分页，严禁先 `.all()` 全部匹配记录再在内存切片。

   - 分页区显示总条数、当前页/总页数，提供上一页、下一页和每页条数组合框；每页选项固定包含 50、100、200、500，默认 100。查询、重置过滤条件或修改每页条数时回到第一页；页码超出新总页数时收敛到最后一页。

   - `simu_time` 以 `HH:mm:ss` 显示；`log_time` 必须以本地墙钟 `yyyy-MM-dd HH:mm:ss` 显示，不能只显示原始 Unix 整数。

   - 对 `log_type=LOG_DECISION` 的记录，必须解析 `log_info` JSON，在详情区用“触发条件、输入、决策过程、输出、平衡校验/警告”五个清晰分组展示。输入和输出中的设备列表使用表格或树形列表；过程必须按 `step` 顺序展示每个策略分支及其执行/跳过原因，不能要求用户阅读一行未经格式化的 JSON 才能了解决策。

   - 详情区提供“查看原始记录”切换项，允许用户查阅经过格式化、带缩进且保持中文的原始 JSON。若遇到历史遗留的非 JSON `log_info`，界面不得崩溃，应直接以普通文本显示。

   - 决策详情中所有浮点数只在显示层格式化为三位小数，数据库 JSON 保留原始精度；状态、点号、运行时刻和墙钟不得按浮点数格式化。

   - 当前停留在本页时按 `data_period` 自动刷新；切换进入本页时立即刷新。周期刷新必须保留当前过滤条件、已选日志、详情区当前分组、原始记录开关和滚动位置；如果选中记录仍存在，不能自动跳到最新记录。

5. 历史曲线：

   - 左右结构。

   - 左侧按 YC/YX/YT/YK 分组显示树型点表，每个点带复选框，支持同时选择多个点。

- 右侧提供开始时刻和结束时刻过滤，格式 `HH:mm:ss`，结束留空表示全部。

   - 每条曲线必须查询满足时间范围的全部历史点，不得设置记录数、保留时长或查询条数上限。

- 曲线板同时绘制多个勾选点，横轴使用 `HH:mm:ss`。

   - 当前停留在本页时按 `data_period` 自动查询并重绘曲线；周期刷新只更新右侧曲线，不重建左侧树，绝不能改变用户勾选状态。

   - 切换进入本页时立即刷新点树和曲线；点树重建时以“历史表名 + 点号”作为稳定身份保留勾选状态，不能把可变点名作为勾选身份的一部分。

#### 12.3 样式和曲线控件

- 使用浅灰蓝背景、白色卡片、圆角边框、蓝色主按钮、浅色表头。

- 顶部控制组使用 `Maximum` 垂直策略，下部标签页使用剩余空间。

- 系统主页和历史曲线的两个左右分栏 `QSplitter` 禁止子控件完全折叠，并设置合理初始宽度。

- 所有数据表格的列宽在可视区域内平均分布，禁止最后一列单独拉伸；窗口缩放、切页和周期刷新后都必须保持等宽，隐藏垂直表头。

- 所有表格单元格、系统主页只读文本框、曲线数据和纵轴刻度中的浮点数必须统一显示恰好三位小数，例如 `1` 显示为 `1.000`、`1.23456` 显示为 `1.235`；只改变显示精度，不得修改数据库原始精度。时间、整数、字符串和墙钟格式不受该规则影响。

- `InteractivePlot` 至少支持多条曲线、图例、网格、时间横轴、滚轮缩放、左键平移、双击复位和鼠标数据游标。游标必须吸附到离鼠标最近的可见数据时刻，绘制竖向虚线并在所有已显示曲线上标记对应数据点；文字框跟随实际鼠标坐标移动，根据剩余空间自动选择鼠标左/右和上/下侧并始终限制在绘图区内，背景必须完全透明，仅保留可读文字、曲线色标和轻量边框。文字框以 `HH:mm:ss` 显示游标时刻，并以三位小数列出每条曲线的值；即使鼠标仍吸附同一个数据时刻，只要鼠标位置变化，文字框也必须同步移动。鼠标离开绘图区、数据源改变、缩放或复位时必须清除旧游标，不能显示过期数据。

- 当无数据时显示“暂无曲线数据”，不能崩溃。

- 支持 `QT_QPA_PLATFORM=offscreen` 下构造和截图，以便自动测试。

### 十三、示例启动流程

正式库初始化：

```powershell
python -m pip install -e ".[test]"
python init_db.py --db ems.db
```

正式 `ems.db` 必须保持停止、开环、无设备运行数据的干净初始状态。

如果已有用户提供的旧 `power.db`，在初始化后复制其设备和 SCADA 定义内容；下面只是路径示例，README 必须同时给出相对路径和绝对路径写法：

```powershell
python import_power_definitions.py --source ..\power_system_simulator\power.db --target ems.db --replace
```

执行该命令后，正式库允许包含这 10 张表中从源库精确复制的内容；这些内容属于用户定义，不是 `seed_demo.py` 的演示数据。其余控制、日志和历史表仍保持初始化状态。

完整本地演示：

```powershell
python seed_demo.py --db ems_demo.db
python simulator_io_mock.py --host 127.0.0.1 --port 9200
python operator_mmi.py --db ems_demo.db --simulator-host 127.0.0.1 --simulator-port 9200
```

README 必须说明：先启动 Mock，再只启动 MMI；MMI 自动按 Core → IO 启动两个子线程，关闭时按 IO → Core 停止并等待。最后在 MMI 选择控制模式、保存参数并点击“启动 / 继续”。

### 十四、自动化测试要求

必须使用 pytest，至少覆盖以下行为：

1. 建库后包含全部 17 张业务表，且不存在 `curve_def`、`dev_wind_gen.angle_yaw_curr` 和 `dev_estore.soc_init`；`scada_yc` 不包含当前偏航角、“本步柴油消耗”、理论最大类或“有功功率设定值”点，`scada_yt` 不包含偏航角设定或桨距角设定点；风机表保留 `p_max_curr` 和 `angle_pitch_curr`，YC 保留当前桨距角，正常有功设定 YT 保留，储能表保留 `soc_curr`、`soc_max` 和 `soc_min`。

1.1. 使用带旧 `soc_init`、`angle_yaw_curr`、中文当前偏航角 YC、三类废弃 YC、各自旧格式点名、中文/旧格式偏航角设定 YT、中文/旧格式桨距角设定 YT 及对应 YC/YT 历史的临时库验证初始化迁移：两个废弃列和全部废弃四遥点/历史被删除，其他风机、储能、YC、YT 和历史记录的主键、数值及精度完整保留，尤其不得误删设备字段 `p_max_curr`、`angle_pitch_curr`、YC“当前桨距角”和正常有功设定 YT；重复初始化结果不变。

1.2. 使用临时静态源库验证 `import_power_definitions.py`：10 张表按目标列完整复制；源端额外的 `scada_rtu.conn_num`、`dev_wind_gen.angle_yaw_curr`、`dev_estore.soc_init` 被忽略，当前偏航角 YC、三类废弃 YC、偏航角设定 YT 和桨距角设定 YT 被剔除；目标表结构和点表不重新出现废弃定义；静态源库文件哈希在导入前后不变；目标主键、有效记录数和全部目标列值一致。另以并发写源库或哈希读取被 Windows 占用的测试替身验证：10 张表来自同一个只读事务快照，脚本给出明确并发提示且不在目标提交后误报失败。

1.3. 验证源表缺少目标必需列时原子失败、目标原内容不变；目标非空但没有 `--replace` 时拒绝覆盖；源目标为同一文件时拒绝执行；导入过程中注入异常时 10 张表整体回滚。

2. `operator_control` 字段名和物理顺序正确。

3. 四遥历史表使用 `time`，不使用 `simu_time`。

4. 旧控制表补列、重排且保留值。

5. 旧四遥历史时间列无损改名且历史记录保留。

5.1. 旧 `operator_log.log_info=VARCHAR(1024)` 可以无损升级为 SQLite `TEXT`，已有普通日志和长内容完整保留，主键及索引不变。

6. 已有历史表缺失的 `(pnt_no, time)` 索引可以补建。

7. SQLAlchemy 连接验证 WAL、busy timeout 和外键已启用。

8. 8 个线程并发写日志，记录数量完整。

9. 4 个独立 Python 进程同时写同一个 SQLite 文件，不出现锁失败且记录数量完整。

10. 风机纯函数覆盖负风速、切入点、立方区间、额定点、额定区间、切出点、非法参数和非有限数值；数据刷新测试必须放入一个数值明显错误的旧“理论最大有功”YC，验证设备表 `p_max_curr` 仍严格等于实时风速和该设备参数的函数计算结果，且该废弃 YC 不进入历史。

11. 光伏理论最大出力边界。

12. 新能源充足、柴油下限、储能充电、储能放电、弃电和失供边界。

13. 停止切换为运行时清理历史与四遥值，但保留点号和点名。

13.1. 停止切换为运行时保留每台储能原有 `soc_curr`，不得复位到 `soc_min`、0 或其他默认值，也不得读取或创建 `soc_init`；只有新的 `time > 0` SOC YC 可以更新 `soc_curr`。风机复位只清理理论最大出力、桨距角、当前功率和设定值，不得访问偏航角字段。

13.2. 分别验证停止→运行、暂停→继续、IO 暂时断线和模拟器时钟回退：在没有有效 SOC YC 时 `soc_curr` 始终保持原值；收到 `<设备名称>.当前SOC`、`<设备名称>.SOC`、`<设备名称>.当前荷电状态` 或兼容 `dev_estore.<id>.soc_curr` 且 `time > 0` 的 YC 后才更新，多个有效 SOC YC 采用时刻最新的一条，`time <= 0` 不得生效。

14. 暂停后继续不清理。

15. `time <= 0` 的 YC/YX 不更新设备、不影响环境量和汇总值，也不进入历史；`time <= 0` 的 YT/YK 不进入历史、不发送、不执行。

16. 新数据时钟前进后只使用有效四遥更新设备并保存历史断面。

17. 开环更新设备 `p_set` 但不写 YT/YK。

18. 闭环每次决策都写 YT 并把时标更新为本轮控制时刻，不能因为 YT 数值未变化而沿用旧时标；只在决策目标启停状态与当前状态不一致时写有效 YK，状态差异持续存在时即使目标 YK 数值未变化也必须刷新为本轮时标；当前功率策略保持启停状态时不得生成冗余 YK。

18.0. 注入可控单调时钟，设置 `data_period=1`、`oper_period=5`，并令模拟器每个墙钟秒把 `data_time_curr` 增加 60。验证数据断面和数据历史每秒更新，但控制决策仅在第 0、5、10……个墙钟秒执行；跨过多个运行秒不得密集补跑，相邻实际决策之间至少间隔 5 个单调墙钟秒，`oper_time_curr` 等于各次决策执行时最新的运行时刻。

18.1. 每个实际决策周期无论开环或闭环都恰好产生一条 `LOG_DECISION`；`log_info` 是未截断的合法 UTF-8 JSON，包含规定的 `schema_version`、唯一 `decision_id`、`trigger`、`inputs`、真实 `process`、`outputs` 和 `validation`。

18.2. 分别使用新能源富余、柴油增发、储能充电、储能放电、弃电和失供场景检查决策日志：输入设备参数和有效 YC/YX 完整；实际执行和跳过的策略分支及原因正确；逐设备 `p_set`、YT/YK 生成或抑制原因、削减、失供和平衡误差与数据库最终输出完全一致。

18.3. 开环决策日志明确记录不生成 YT/YK；闭环状态一致时记录 `reason=status_unchanged` 且不产生有效 YK；状态不一致时日志中的 YK 点号、值、时刻与实际写入记录一致。

18.4. 注入 `operator_log` 写入异常，验证设备 `p_set`、YT/YK、历史断面、`oper_time_curr` 和决策日志整体回滚；正常提交时这些输出与日志在同一事务中同时可见。测试大量设备输入，证明 `log_info` 不受原 1024 字符长度截断。

19. Bridge 按 `data_period` 获取数据。

20. Bridge 每 1 秒只发送 `time > 0` 且时标晚于发送游标的命令；非空写请求顶层携带当前 `source_run_seq`。YT/YK 是否刷新时标均不以旧 `value` 是否相同为条件。YK 还必须通过最新 YX/设备状态差异复核，状态一致、状态未知或负游标命中的 `time=0` 命令都不能放出。对端返回非对象、`ok!=true` 或非空 `rejected` 时不得推进 YT/YK 游标。

21. 读取或命令发送失败时不推进相应进度，并将 RTU 状态写为 0；恢复成功后写回 1。

21.1. 成功读取时四遥 `time` 和 `data_time_curr` 使用响应顶层 `simu_time`，而 `scada_rtu.refresh_time` 使用可注入测试的 Unix 墙钟；测试必须证明两种时刻不会互相覆盖。

21.2. 当响应时刻小于本地时刻时，用假的 Core 管理器记录并验证严格顺序：停止动作看到旧数据；重启动作只能看到已提交的新时钟、新 YC/YX 和已清空的历史/日志；YT/YK 仍为零；四个 Bridge 周期/游标状态归零。另用 `run_seq` 变化但新时刻前进的场景验证仍会停止 Core、清理旧任务数据并保存新任务元数据；`runtime_ready=false` 或只有零时刻占位时 Core 保持停止，直到同一 `run_seq` 的首个有效断面提交后才启动。只有任务序号未变化且响应时刻相等或前进时，才不得停启 Core、不得清历史。

21.3. Core 线程停止失败时运行数据完全不回退；清理/应用异常时事务完整回滚并尝试重启 Core。使用临时数据库和受管线程验证停止事件、`join`、有界超时、线程代次、启动/停止顺序以及时钟回退后的 Core 重启；另为显式独立兼容入口保留 PID 文件归属测试。严禁测试正式数据库或正式运行线程。

21.4. 分别对 YC、YX、YT、YK 预置本地点号和点名，再输入同点号但不同 `name` 的报文，验证四张实时表都只改变 `value/time`，本地 `name/pnt_no` 保持不变。再对四种四遥各输入一个未知点号，验证不创建记录、同批已定义点仍成功更新，并为每个未知点写入一条包含正确类型、点号、报文点名、来源和运行时刻的 `LOG_WARNING/unknown_scada_point` 告警。

21.5. 对真实 `simulator_io`、Mock 和 Bridge 分别验证 YC/YX 严格位置协议：请求使用乱序列表并混入重复点、未知点和已定义 `time=0` 点；响应数组长度与请求完全相等，第 N 项严格对应第 N 个请求点，重复点重复返回，未知点为 `value=null,time=0` 且产生告警，零时刻已定义点返回自身值但不更新 EMS。断言所有响应项键集合严格等于 `{"value","time"}`。再分别注入缺项、超项、`pnt_no/name` 额外字段、负时刻和晚于包级时刻的数据，验证 Bridge 拒绝整批响应、不会错配后续点且把 RTU 标记为中断。使用超过 SQLite 单次变量上限的大型倒序列表验证分块查询后仍按原请求顺序组装，而不是按数据库查询顺序返回。

22. 兼容 TCP Server 完成真实 socket 往返，并忽略零时刻 YC/YX 和 YT/YK，同时过滤目标状态已与当前状态一致的 YK。

23. Mock 完成真实 `read`/`write` socket 往返；`read` 严格保持乱序、重复、未知和零时刻请求位置且只返回 `value/time`，`write` 只执行正时刻命令，并在下一断面反馈有效设定值。

24. `format_simu_time(3661) == "01:01:01"`、`format_simu_time(90000) == "25:00:00"`；测试四遥 YC/YX/YK/YT 的 `time=3661` 均在“刷新时刻”列显示为 `01:01:01`，且超过 24 小时不回绕。

24.1. 固定本地日期时间后验证 `format_wall_time()` 精确输出 `yyyy-MM-dd HH:mm:ss`，例如 `2026-08-23 14:05:09`；RTU 表“刷新时刻”和连接详情使用这一墙钟格式，0 显示为 `--`，不得显示成四遥的累计时长格式。

25. Qt 无头构造成功，五个主页面、五个设备子页及 RTU、YC、YX、YT、YK 五个数据子页全部存在；界面中不存在“曲线定义”标签页。

25.1. 风机设备表不存在 `angle_yaw_curr` 列，储能设备表不存在 `soc_init` 列；旧当前偏航角、“本步柴油消耗”、理论最大类、“有功功率设定值”YC，以及偏航角设定、桨距角设定 YT，即使由旧库导入或运行期数据再次传入，也不会被创建、更新、显示、发送或出现在四遥定义及历史曲线中；设备字段 `p_max_curr`、`angle_pitch_curr`、YC“当前桨距角”和正常有功设定 YT 正常保留。

25.2. 在临时库中于初始化后手工插入正时刻偏航角设定和桨距角设定 YT，分别验证运行期四遥更新入口不修改其 `value/time`、Bridge 写请求和兼容 Server 响应都不包含这些点、Bridge 仍推进已检查候选点的 YT 发送游标；MMI 尝试把任一 YT 点名修改成两类废弃中文或旧英文格式时必须拒绝保存，正常有功设定仍可保存与发送。

26. MMI 正确显示开闭环、双周期、数据时刻、控制时刻和顶部电网模拟器“正常/中断”；两个时刻都必须由整数秒格式化为 `HH:mm:ss`。RTU / 四遥定义页不得出现第二套 `operator_io` 连接状态或连接详情控件，顶部状态必须直接来自 `scada_rtu.status`。

27. 历史树有四组，支持勾选多个点形成多条曲线。

28. YK 状态命令覆盖三个边界：状态一致时不生成并把旧命令失效为 `time=0`；状态不一致时生成正时刻命令；Bridge 和兼容 Server 均不会下发状态一致或状态未知的 YK。

29. MMI 的“建立连接”“中断连接”按钮正确修改 `io_connect_enabled` 和按钮启用状态；Bridge 在关闭时零 TCP 请求并保持最后刷新时刻，恢复后下一 tick 立即读取；建立请求不能伪造 RTU 连接成功。

30. MMI 首页五条系统曲线来自代码内置不可变配置，不查询任何曲线配置表。

31. MMI 固定 1 秒定时器只刷新顶部状态和系统主页；设备、RTU / 四遥、日志、历史曲线使用独立定时器并严格跟随 `data_period`，即使 `oper_period` 不同也不得误用。切换五个主页面时目标页面都立即刷新，不能等待周期。

32. 顶部控制参数、设备表或四遥表人工修改后，控件/单元格和所属表格立即显示橙黄色脏状态；固定 1 秒、切页和周期刷新均不覆盖未保存值。状态动作只更新 `oper_status`，不能误保存脏参数。三个参数区域都提供“手动刷新参数”，存在脏值时必须确认放弃，确认后从数据库重载并清色，取消时保留原值；保存成功同样清色并恢复自动刷新。历史曲线周期刷新不重建点树，页面切换或点名改变后已勾选点仍按表名和点号保持勾选。

33. 表格、只读文本框、曲线数据和纵轴刻度中的浮点数统一为三位小数；测试至少验证 `1.23456 -> 1.235`，并证明数据库值没有被显示格式化反写。

34. 运行日志页可以按类型、运行时刻和关键词查询；先执行匹配总数查询，再用 `LIMIT/OFFSET` 分页读取，默认每页 100 条且可选 50/100/200/500，页面显示总条数、当前页/总页数并正确控制上一页/下一页。至少插入 1205 条日志，证明首页只查询和渲染 100 条、共有 13 页，而数据库全部 1205 条仍可通过翻页访问；禁止全量 `.all()` 后内存切片。选择 `LOG_DECISION` 后正确展示“触发条件、输入、决策过程、输出、平衡校验/警告”五个分组，策略步骤顺序和执行/跳过原因完整，原始 JSON 可以切换查看，非 JSON 旧日志不会导致界面异常。

35. 运行日志页中的墙钟、运行时刻和浮点数格式正确；按 `data_period` 刷新时保留过滤条件、当前页、选中决策、详情分组、原始记录开关和滚动位置，且不会把用户正在查阅的记录强制切换为最新日志。新日志插入导致所选记录从当前页移动到下一页时，必须按倒序 ID 排名计算新页码并继续选中该记录。

36. 系统主页和历史曲线均支持数据游标；移动鼠标后游标吸附最近的可见数据时刻，所有已显示曲线同时出现点标记，文字框完整显示时刻和所有曲线的三位小数值。文字框背景 alpha 必须为 0，并跟随鼠标位置移动、自动翻转和限制在绘图区内；测试必须在同一吸附时刻改变鼠标纵向位置并证明文字框矩形随之变化。离开、换数据、缩放和复位后旧游标消失。

### 十五、技术与使用手册 HTML

#### 15.1 文件和版式

- 必须生成仓库根目录文件 `power_system_operator_technical_user_manual_v1.0.html`。它是可直接交付给最终用户的成品，不得只在最终回复中粘贴 HTML 代码，也不得用 Markdown、README、网页截图或临时 PDF 代替。

- 手册必须使用 `<!DOCTYPE html>`、`<html lang="zh-CN">` 和 UTF-8 `meta charset`。页面标题固定为“Power System Operator 技术与使用手册 V1.0”。正文不得出现乱码或 Unicode 替换字符 `U+FFFD`。

- 采用真正的单文件离线设计：CSS、JavaScript、系统架构图和全部 MMI 截图均内嵌；PNG 截图使用 `data:image/png;base64,...` 数据 URL，不得依赖同目录图片文件。不得引用 CDN、外部字体、远程图片、外部脚本或运行中的本地服务。用户双击 HTML 文件即可浏览；临时 HTTP 服务只允许用于验收，验收后必须停止。

- 桌面端采用固定目录加正文的工程手册布局；窄屏采用紧凑顶部栏、可展开目录和单列正文。目录链接必须与章节锚点一一对应，支持滚动定位和当前章节高亮；移动端选择章节后目录应自动收起。

- 页面提供“打印”按钮并调用浏览器打印功能。内置 `@page { size: A4; ... }` 和 `@media print`，打印时隐藏目录、移动端顶部栏、按钮等交互元素，避免表格、代码块、告警框和标题被不合理截断。最终交付物仍然是 HTML，PDF 只能作为可选的打印验收产物。

- 手册必须响应桌面和手机宽度；页面本身不得出现横向滚动。宽表格必须放在独立横向滚动容器中，代码块允许自身滚动或自动换行，内联 SVG 架构图按容器自适应缩放。

- 视觉风格应与 `operator_mmi_qt.ui` 的蓝色工程监控界面协调，保持足够对比度、清晰的标题层级、状态/注意/警告色块和适合长篇阅读的行高，不追求与业务界面逐像素相同。

- 文档版本使用 `V1.0`；软件版本必须从最终 `pyproject.toml` 或包元数据读取。若仓库已有 Git 提交，手册显示生成时真实短哈希；若没有提交，显示“未提交”，不得伪造哈希。发布日期使用实际生成日期，不得复制过期日期。

#### 15.2 真实界面截图和用户操作说明

- 截图必须来自最终生成的 `operator_mmi.py`、`operator_mmi_qt.py` 和实际 SQLAlchemy 数据库，由真实 PyQt6 控件直接渲染；不得使用 AI 生成图片、重新绘制的界面模型、空白占位图、设计稿或其他应用窗口冒充 MMI。截图中的运行时刻、功率、点值和日志条数必须标明是采集断面，不能写成固定业务结果。

- 统一使用 MMI 默认桌面尺寸 1480×920 截取完整窗口。截图必须保留顶部运行控制区和目标主页面的关键操作区域，不得为了排版裁掉运行状态、控制模式、双周期、双时钟或电网模拟器连接状态。截图采集不得修改正式业务数据；可以使用正式库的只读断面或独立联调库，但必须说明数据来源。

- 手册至少内嵌以下 6 张 1480×920 PNG 截图，每张都要有准确的中文 `alt` 和编号图注：

  1. 系统主页与顶部运行控制区：同时显示运行/暂停/停止、开闭环、数据/决策周期、数据/控制时刻、保存参数、手动刷新参数、建立/中断连接、当前断面、曲线树和系统曲线。
  2. 设备定义：至少选择风力发电机子页，清楚显示浅黄色可编辑静态字段、橙黄色人工修改字段、灰色实时只读字段、“手动刷新参数”和“保存设备修改”。
  3. RTU 定义：显示 RTU 表及其 ID、IP、端口、状态、墙钟刷新时刻；截图顶部保留全局“电网模拟器连接”状态卡，RTU / 四遥页面内部不得重复显示连接摘要栏。
  4. 遥测 YC 点表：显示点号、点名、值和 `HH:mm:ss` 刷新时刻，并覆盖环境、设备功率、桨距角或 SOC 等代表性点。
  5. 控制决策运行日志：左侧至少有一条真实 `LOG_DECISION`，右侧显示触发条件、输入、决策过程、输出、平衡校验/警告和原始记录页签。
  6. 四遥历史曲线：实际勾选至少 3 个 YC/YX/YT/YK 点并绘制多条历史曲线，状态栏显示曲线数和历史点数。

- 第 10 章必须围绕上述截图形成真正的用户操作使用手册，不得只列控件名称。至少给出以下 6 组可执行步骤：顶部控制与连接、系统主页监视、设备参数修改保存/手动刷新、RTU/四遥核对与定义维护/手动刷新、决策日志查询审计、历史多点曲线查询。每组都要说明操作入口、点击/输入顺序、成功判据、时刻格式、错误或风险提示、橙黄色脏状态、自动刷新/编辑保护和透明跟随游标行为。

- 截图必须包在带边框、背景和图注的 `<figure>` 中，桌面端按正文宽度清晰缩放，窄屏保持单列且不造成页面级横向溢出。A4 打印时图片、图注和相邻标题尽量保持在同页；不得用 CSS 隐藏截图来规避打印布局问题。

- 截图不得包含密码、令牌、个人聊天、其他项目窗口或与本系统无关的敏感信息。若窗口标题相近或前台遮挡导致无法确认目标，必须改用同一 PyQt6 程序的确定性控件渲染抓图并逐张目视检查，不能截取不确定窗口。

#### 15.3 必须包含的 18 个章节

目录和正文必须完整包含以下 18 个一级章节，顺序不得遗漏：

1. **手册概览**：说明系统用途、适用对象、一个 MMI 宿主进程与两个受管工作线程、外部模拟器接口、正式库与示例库边界，以及本手册对应的软件版本。

2. **快速开始**：给出 Python 版本检查、环境创建、依赖安装、`pyuic6`、正式库初始化、独立示例库、Mock 联调以及只启动 MMI 的可复制 PowerShell 命令；说明 MMI 自动启动/停止 Core、IO 子线程的顺序和首次运行检查点。

3. **系统架构**：使用内联 SVG 展示外部 `simulator_io`、MMI 宿主、IO/Core 子线程和共享 `ems.db`，并分别解释量测数据流、控制命令流、线程生命周期和数据库访问关系。

4. **线程职责与调度**：说明 Core 的 0.5 秒轮询、数据周期与决策周期的单调墙钟调度、IO 的读取/写入周期、MMI 页面刷新周期、子线程自动启停，以及逻辑“停止”和关闭 MMI 进程的区别。

5. **安装、初始化与部署**：说明依赖、目录、数据库初始化/迁移、正式库不得写示例数据、旧 `power.db` 只读导入、配置参数、日志、MMI 宿主和子线程生命周期管理。

6. **数据库设计**：逐表列出当前 ORM 的 17 张业务表、主键/复合主键、关键字段、单位、索引、约束和默认值；明确不存在 `curve_def`，历史表时间字段为 `time`，RTU `refresh_time` 是 Unix 墙钟。

7. **时间模型与数据有效性**：区分数据时刻、控制时刻、包级 `simu_time`、点级 `time`、进程单调墙钟和 RTU 墙钟；解释 `HH:mm:ss` 可超过 24 小时、RTU `yyyy-MM-dd HH:mm:ss`、`time > 0` 有效性和时钟回退恢复顺序。

8. **四遥通信协议**：给出 JSON Lines 请求/响应示例，说明 YC/YX 严格位置映射、不排序、不去重、不跳过重复/未知/零时刻点，未知点 `{"value":null,"time":0}` 占位并告警，响应项只能含 `value/time`，运行期只修改本地已定义点的 `value/time`；同时说明 YT/YK 的发送、确认、时标和状态差异规则。

9. **新能源优先控制策略**：说明输入、设备约束、风机纯函数、光伏可用功率、储能 SOC 边界以及“新能源优先、柴油下限、储能充电、新能源限发、柴油增发、储能放电、失供与平衡校验”的实际执行顺序；约定储能正值放电、负值充电。

10. **MMI 使用说明**：只描述当前存在的系统主页、设备定义、四遥定义、运行日志、历史曲线五个主页面，不得加入“曲线定义”；结合至少 6 张真实界面截图，逐步说明运行/停止/暂停、开环/闭环、双周期、双时钟、建立/中断连接、主页监视、设备参数保存、RTU/四遥核对、决策日志审计、历史多点查询、编辑保护、页面切换刷新、三位小数和曲线游标。

11. **标准操作流程**：分别给出首次部署、正常运行、暂停恢复、开闭环切换、连接中断恢复、停止后重新运行和模拟器时钟回退的操作步骤、预期状态和注意事项。

12. **运行日志与历史数据**：说明 `LOG_DECISION` 的触发条件、输入、策略步骤、输出和平衡校验结构；说明历史断面和四遥历史的写入时机、查询方式以及“无自动存储/查询条数上限但必须监控磁盘”的运维要求。

13. **SQLite 多线程与外部多进程并发可靠性**：说明每个长期工作线程独立 Engine/Session、WAL、`busy_timeout`、外键、短事务、写重试、事务边界、网络调用期间不持有事务和 SQLite 单写者事实。

14. **备份、恢复与数据归档**：给出一致性备份、停机恢复、完整性检查、WAL 文件注意事项、长期历史归档和回滚前备份方法；不得把直接复制一个正在写入的主数据库文件描述为可靠备份。

15. **故障排查**：至少覆盖模拟器连接失败、RTU 状态中断、未知点号、数据库锁、数据/决策周期不正确、YC/YX 时刻无效、YT/YK 未发送、SOC 不一致、负荷或历史曲线异常、MMI 编辑被刷新覆盖等问题，并给出可执行检查步骤。

16. **测试、版本与维护**：列出 `pyuic6`、编译、pytest、数据库审计、Mock 开闭环联调、Qt 无头/视觉检查、版本基线和修改后回归测试要求。

17. **安全、权限与应用边界**：明确当前 TCP JSON Lines 是明文且没有 TLS、鉴权或消息签名，只适合本机联调或受控实验网络；说明数据库文件权限、备份保护和生产化前需要补充的安全能力。

18. **附录与上线验收清单**：汇总运行状态、控制状态、日志类型、时间格式、关键命令、端口、数据库检查 SQL、上线前检查项和版本信息。

#### 15.4 必须与代码保持一致的手册事实

- 手册必须以最终生成并验证的代码为准。先检查 ORM、策略、Bridge、入口脚本、UI 和测试，再写手册；禁止照抄本提示词中已经被实际实现调整掉的细节，也禁止把待办事项写成已经具备的能力。

- 明确系统共有 17 张业务表、5 个 MMI 主页面、1 个正式 MMI 宿主进程和 2 个受管工作线程；`simulator_io_mock` 是外部联调工具。不得出现 `curve_def`、`soc_init`、偏航角字段、偏航角设定 YT 或桨距角设定 YT。

- 明确停止切到运行会清空历史、日志及实时四遥的 `value/time`，但不会复位储能 `soc_curr`；暂停再继续不会执行停止到运行的清理。逻辑停止只改变业务状态，不等价于杀死进程。

- 明确有效 YC/YX 才能更新设备状态和实时字段，设备 `status` 来自有效实时 YX；风机 `p_max_curr` 来自 `calculate_wind_max_power()`，不能来自废弃理论最大出力 YC；SOC 只来自能够唯一映射的有效 SOC YC。

- 明确开环会计算并更新设备设定但不会生成有效 YT/YK；闭环每轮决策刷新 YT 时标，YK 只有目标状态与当前实际状态不一致时才有效。状态差异持续存在时，不能因为 YK 数值与上一轮相同而沿用旧时标。

- 所有浮点数“三位小数”只属于显示层，不得把舍入结果描述为数据库精度；历史记录没有自动保存条数或查询条数上限，但手册必须提醒磁盘增长、备份和归档责任。

- 启动、初始化、导入、测试和联调命令必须与实际 CLI 参数一致，并在当前工程中验证后再写入。端口、文件名、状态枚举、日志类型和示例 JSON 不得凭空猜测。

#### 15.5 HTML 成品验收

生成后必须进行静态结构检查并报告结果：

- 文件存在且可用严格 UTF-8 解码，无 `U+FFFD`；记录文件字节数、行数和 SHA-256。

- DOCTYPE、`lang="zh-CN"`、UTF-8 charset 和 `<title>` 正确；`html/head/body/style/script` 标签闭合。

- 18 个 `<section>` 与 18 个目录链接齐全；所有目录 `href="#..."` 都有对应唯一 `id`，缺失锚点和重复 ID 都必须为 0。

- 表格、SVG、代码块、JavaScript 和 CSS 结构闭合；存在 A4 `@page`、`@media print` 和至少一个窄屏 `max-width` 响应式断点；不存在外部网络资源引用。

- 至少存在 6 个 MMI 截图 `<figure>`、6 个非空中文图注和 6 个非空中文 `alt`；所有截图 `src` 都必须是 `data:image/png;base64,...`。逐项 Base64 解码后必须具有 PNG 签名且原始尺寸为 1480×920；截图占位符残留、外部图片引用和无法解码图片均为 0。报告截图数量、PNG 总字节数、每张尺寸和 HTML 最终 SHA-256。

- 检查第 10 章包含顶部控制与连接、主页、设备、RTU/四遥、日志、历史曲线 6 组分步操作模块；每组都必须出现具体操作动作和成功判据，不能只有功能简介。

还必须使用真实浏览器完成视觉和交互验收：

- 在约 1440×900 的桌面视口打开手册，确认固定目录、封面、正文、表格、代码块、架构图和 6 张 MMI 截图可读，页面无整体横向溢出；浏览器中 6 张图片必须全部加载完成，`naturalWidth/naturalHeight` 均为 1480×920。

- 在约 390×844 的窄屏视口打开手册，确认顶部栏和目录按钮可见，目录能打开，“MMI 使用说明”能定位并自动收起目录，正文为单列，宽表格只在自身容器滚动，SVG 和 MMI 截图不超出正文；图注和分步操作不得重叠或被截断。

- 检查目录当前章节高亮、打印按钮存在、移动端长标题不重叠；浏览器控制台不得有 JavaScript 错误。若发现排版或交互问题，修改 HTML 后必须重新加载并复验。

- 浏览器验收不得依赖未验证的“端口已监听”推断；必须实际加载目标 HTML 并读取页面标题/关键 DOM 或截图。临时 HTTP 服务只停止本次明确启动的进程，不能误停 Core、IO、MMI 或外部模拟器。

### 十六、实际验收要求

完成代码后，必须实际执行并报告结果：

```powershell
pyuic6 operator_mmi_qt.ui -o operator_mmi_qt.py
python -m py_compile operator_mmi_qt.py operator_mmi.py
python -m compileall -q .
python -m pytest -q
python init_db.py --db ems.db
```

若验收环境存在真实旧库，再执行：

```powershell
python import_power_definitions.py --source <已有 power.db 的路径> --target ems.db --replace
```

并报告源库只读校验、10 张表的逐表记录数和目标列值一致性结果；不得在不存在真实源库时创建一个同名文件来伪装完成该项验收。

数据库审计至少包括：

```sql
PRAGMA integrity_check;
PRAGMA journal_mode;
PRAGMA busy_timeout;
PRAGMA foreign_keys;
PRAGMA table_info(operator_control);
PRAGMA table_info(dev_wind_gen);
PRAGMA table_info(dev_estore);
PRAGMA table_info(scada_yc_his);
PRAGMA index_list(scada_yc_his);
```

注意：`busy_timeout` 和连接级外键配置必须通过项目的 SQLAlchemy engine 连接检查，不能只用一个没有安装连接事件的外部 SQLite CLI 连接下结论。

还必须执行一次独立 MMI 宿主联调：

1. 使用独立 `ems_demo.db`。

2. 启动 Mock，再只启动 MMI；证明 MMI 自动按 Core → IO 创建两个子线程，且系统中没有独立 Core/IO 进程。

3. 先设置开环并运行至少一个决策周期，证明设备 `p_set` 已更新且 YT/YK 为 0 条；查询对应 `LOG_DECISION`，证明日志包含实际输入、逐步策略过程、逐设备输出，并明确记录开环未生成 YT/YK。

4. 切换闭环再运行至少一个决策周期，证明 YT/YK 已生成，并证明 Mock 收到命令；核对决策日志中的设备设定、YT/YK、削减/失供和功率平衡结果与数据库及 Mock 实际结果一致。

5. 检查 `data_time_curr`、`oper_time_curr`、RTU `refresh_time`、日志和历史记录均前进，并证明 `refresh_time` 接近当前 Unix 墙钟而不是模拟器运行秒数。

6. 联调结束后正常关闭本次 MMI，证明先停止 IO、再停止 Core 并完成 `join`；只停止 PID 已明确记录的 MMI 宿主，不得误停其他 Python 进程。

Qt 视觉验收至少包括：

- 1480×920 下渲染系统主页，顶部控制区无重叠，“建立连接”“中断连接”两个按钮完整可见。

- 分别渲染连接请求开启和关闭状态，确认按钮启用状态与顶部实际状态语义分离。

- 在历史页实际勾选至少 3 个点，确认绘制至少 3 条曲线且横轴为 `HH:mm:ss`。

- 在运行日志页选择一条真实 `LOG_DECISION`，确认触发条件、输入、决策步骤、输出和平衡校验均能直接浏览；切换格式化详情和原始 JSON 后内容一致，长设备列表没有截断或布局重叠。

- 在设备定义页核对风机表只有当前桨距角列、没有偏航角列，风机 `p_max_curr` 随有效环境风速和设备参数按功率曲线变化；储能表没有 `soc_init`。在四遥定义和历史曲线树中确认不存在被迁移/过滤的当前偏航角、“本步柴油消耗”、理论最大类、“有功功率设定值”YC，以及偏航角设定或桨距角设定 YT，并确认 YC“当前桨距角”和正常有功设定 YT 仍存在。

#### 16.1 版本保存、远端提交与服务重启验收

当用户或验收任务明确要求“保存修改、提交远方、重启服务”时，必须把它作为一次完整发布任务执行，不能只保存文件、只运行 `git push`、只启动进程或只证明端口存在。

提交前必须：

1. 重新读取当前 `git status --short --branch`、`git diff`、当前分支、上游和 `git remote -v`，识别用户已有或其他并行任务产生的修改。只能暂存本任务实际修改的文件或补丁块，必须在提交前列出并复核显式暂存清单以及 `git diff --cached`；不得使用会混入无关文件的全仓库宽泛暂存。

2. 默认排除正式/运行数据库及其旁文件、运行状态和临时产物，包括 `ems.db`、`*.db-wal`、`*.db-shm`、`.runtime/`、`*.log`、备份、缓存、临时截图和测试输出；只有用户明确要求把某个成品纳入版本时才允许提交。不得通过 `reset --hard`、`clean`、覆盖、删除或暂存后撤销来处理用户的无关修改。

3. 对本次修改执行与风险相称的真实验证，至少包括适用的 `py_compile`/`compileall`、`pytest`、数据库审计、HTML/Qt 渲染检查和 `git diff --check`。测试失败时不得提交并伪称发布成功；若失败与本任务无关，也必须保留原状并给出可复现证据。

4. 使用能够概括实际改动的提交信息提交并推送用户指定分支；未指定时使用当前已有上游分支，禁止擅自改写远端历史或强制推送。推送后必须分别读取本地 `HEAD`、`@{u}`、本地远端跟踪引用和 `git ls-remote` 返回的实时远端分支 SHA，并确认四者完全一致。最终报告完整提交 SHA、短 SHA、提交信息、提交文件清单和最终工作区状态。

服务重启前必须建立新的实时基线：

1. 通过完整脚本绝对路径、数据库绝对路径和完整命令行识别唯一的 `operator_mmi.py` 宿主进程；同时检查并清除旧架构遗留的独立 `operator_core.py`/`operator_io.py`，防止它们与新子线程重复运行。不得只按 `python.exe`、模糊名称、父进程或窗口标题结束进程，不得批量杀死其他 Python 服务。

2. 单独识别外部 `simulator_io.py` 以及 TCP `9001` 监听者，记录操作前的实时 PID 和命令行。外部模拟器可能在不同检查之间自行重启并更换 PID，因此必须以本次停止动作之前的最后一次实时检查为保护基线，不得沿用旧报告中的过期 PID，也不得把外部 PID 自行变化错误归因于本次发布。

3. 除非用户明确要求重启外部模拟器，否则禁止停止、重启或修改外部 `simulator_io`，并禁止抢占其 `9001` 监听端口。重启本项目后必须证明外部基线 PID 仍存活且 `9001` 仍由它监听；若外部服务在发布过程中自行变化，必须重新核对命令行和端口归属并如实报告，不能伪称“未变化”。

本项目默认只重启 MMI 宿主进程：

```text
停止：关闭 operator_mmi → MMI 内部停止并等待 operator-io → operator-core
启动：启动 operator_mmi → MMI 内部自动启动 operator-core → operator-io
```

- 关闭 MMI 后必须确认宿主 PID 已退出；正常关闭路径必须先停止 IO 线程，再停止 Core 线程并完成有界 `join`。启动时使用 `pythonw.exe` 或等价无控制台方式保持 MMI 窗口可见，并传入数据库、Bridge 主机、端口、RTU 和轮询参数。默认模式不得再启动独立 Core/IO 进程，也不创建 Core 子线程 PID 文件。

- MMI 宿主应使用独立、带时刻或发布标识的 `.runtime` 标准输出/错误日志，并在日志中带线程名，明确记录 Core、IO 子线程的启动、线程标识、停止和异常。启动后必须有合理的稳定等待窗口，不得把 `Start-Process` 返回 PID 或短暂存活当作完整健康证据。

发布后至少完成以下验收：

1. 重新列出唯一 MMI 宿主的完整命令行，报告旧 PID → 新 PID；确认不存在独立 Core/IO 进程。通过线程控制器快照、日志或测试确认 `operator-core`、`operator-io` 两个子线程各一个、均存活且由同一 MMI 生命周期管理。首实例运行时再次执行同一启动命令，验证第二实例在初始化数据库和工作线程之前被单实例锁拒绝、以非零码退出，且首实例 PID、窗口、线程和 RTU 墙钟均不受影响；首实例正常或异常退出后还必须验证锁可重新获取。

2. 检查外部模拟器命令行和 `0.0.0.0:9001`/实际配置地址的监听归属，证明没有误停或端口漂移。

3. 使用 SQLAlchemy 项目连接或只读数据库连接检查 `PRAGMA integrity_check=ok`、`journal_mode=wal`、`operator_control` 的运行/开闭环/连接请求/双周期/双时钟以及 `scada_rtu.status`。`scada_rtu.refresh_time` 必须按 Unix 墙钟秒计算新鲜度，不能拿模拟器时刻判断连接健康。

4. 扫描本次 MMI 宿主及 Core/IO 线程日志中的 `Traceback`、`ERROR`、未处理 `Exception` 和致命错误。若 IO 收到回退的模拟器时刻并按规定清理历史/日志、把双时钟归零，必须结合操作前后时刻解释这是时钟回退恢复结果，不能误报为历史上限或随机数据丢失。

5. 在真实 Windows/Qt 环境中按精确标题筛选且只能得到一个“电力系统操作员人机界面”窗口；读取真实控件或截图，确认系统主页、运行状态、控制模式、双时钟和“电网模拟器连接：正常/中断”与数据库及 RTU 状态一致。不得使用其他前台程序、标题近似窗口或旧截图冒充 MMI 验收。

6. 最后再次检查 `git status --short --branch` 和 `git status --porcelain`；如工作区存在用户的预有修改，必须列出并说明它们未被提交。只有提交范围、远端 SHA、MMI 宿主与两个子线程、外部模拟器、TCP、数据库、日志和窗口全部得到真实证据后，才能报告发布完成。

### 十七、交付质量和禁止事项

必须遵守：

- 不得生成或修改 `power.db`；用户已有的 `power.db` 只允许作为定义数据的只读迁移源。

- 不得把示例设备和历史数据写入正式 `ems.db`。

- 不得把四遥历史时间列写成 `simu_time`。

- 不得把 `scada_rtu.refresh_time` 写成模拟器运行时刻；它必须是最近成功 TCP 交换的 Unix 墙钟秒。

- 不得把 `data_time_curr` 和 `oper_time_curr` 合并成一个字段。

- 不得让开环模式写 YT/YK。

- 不得把 `time <= 0` 的 YC/YX 当成有效量测，也不得发送或执行 `time <= 0` 的 YT/YK。

- 不得对 YC/YX 请求点号排序或去重，不得从响应中省略未知点、重复点或 `time=0` 点，不得在 YC/YX 响应项中返回 `pnt_no/name`；必须使用等长 `value/time` 数组按原请求位置映射。

- 不得在网络调用期间持有 SQLite 写事务。

- 不得让 `operator_io` 连接失败后继续把 RTU 显示为连接成功。

- 不得依赖一个 Python 全局锁替代 SQLite 事务、WAL 和忙等待；MMI/Core/IO 线程及外部多进程访问都必须遵守数据库并发边界。

- 不得省略储能设备页和储能策略。

- 不得定义、创建、显示、计算、导入、更新、发送或回写 `angle_yaw_curr`、风机偏航角设定 YT、风机桨距角设定 YT 和 `soc_init`；不得生成 `<设备名称>.当前偏航角`、`dev_wind_gen.<id>.angle_yaw_curr` 或本提示词列出的六种禁用风机角度 YT 点位。旧库升级、旧库导入和 IO 数据包处理均不得使这些废弃定义重新出现。不得误删或禁用设备字段 `angle_pitch_curr`、YC“当前桨距角”及有功设定。不得在启动、暂停恢复、连接中断或时钟回退时复位 `soc_curr`；SOC 只接受有效 YC 更新。

- 不得只生成 `.ui` 而不运行 `pyuic6`。

- 不得只生成 Python UI 而没有 `.ui` 源文件。

- 不得把 `operator_io` 默认实现成入站 Server；默认必须是访问 `simulator_io` 的 Bridge 客户端。

- 不得只声称服务可运行；必须提供真实 socket、数据库和 Qt 渲染证据。

- 不得为了通过测试而跳过真实业务逻辑或写死测试返回值。

- 不得只写“开始决策”“决策完成”之类的摘要日志来替代输入、过程和输出审计；不得截断、覆盖或以不可解析字符串保存 `LOG_DECISION.log_info`。

- 不得让决策输出与对应决策日志分属两个可独立提交的事务。

- 不得省略 `power_system_operator_technical_user_manual_v1.0.html`，不得用 README、聊天文本、截图或只有目录没有正文的空壳文件代替；不得让手册依赖 CDN、外网字体、远程图片或正在运行的 Web 服务。

- 不得在手册中保留已经废弃的表、字段、点位、页面和策略顺序，不得伪造软件版本、Git 哈希、测试数量、进程状态或浏览器验收结果。手册的命令和事实必须来自最终代码及本次真实验证。

最终回复必须以“已经生成并验证的结果”为中心，列出：

1. 主要文件及路径。

2. 数据库表数量、控制初值、完整性检查和并发参数。

3. `pyuic6` 是否真实执行。

4. pytest 通过数量。

5. 独立 MMI 宿主开环与闭环联调数据，以及 Core/IO 子线程自动启动、时钟回退重启和关闭顺序证据。

6. Qt 主页与历史多曲线渲染结果。

7. 正式库是否保持无示例运行数据。

8. 废弃字段 `angle_yaw_curr`、`soc_init`，废弃的当前偏航角、“本步柴油消耗”、理论最大类、“有功功率设定值”YC，以及偏航角设定、桨距角设定 YT 的建库、旧库迁移、历史清理、导入过滤、IO 防重建/防更新及 MMI 验收结果；风机 `p_max_curr` 只由风速和设备参数计算、不受旧 YC 覆盖的测试结果；YT/YK 相同值仍刷新本轮有效命令时标的测试结果；以及 `soc_curr` 不复位、只由有效 SOC YC 更新的四种状态场景测试结果。

9. HTML 技术与用户操作手册的绝对路径、文档/软件版本、字节数、章节数、UTF-8 与锚点检查、6 张真实 MMI 截图的来源/尺寸/PNG 总字节数/图注与 Base64 解码结果、6 组操作步骤、响应式/A4 打印检查、桌面与窄屏真实浏览器渲染结果、控制台错误数量和 SHA-256。

10. 若本次任务包含版本发布和服务重启：显式暂存清单、完整提交 SHA 和提交信息、四方 Git SHA 一致性、MMI 宿主旧/新 PID与完整参数、Core/IO 子线程快照及启动停止顺序、不存在独立 Core/IO 进程的证据、外部模拟器和 `9001` 端口保护结果、数据库/RTU 墙钟新鲜度、本次线程日志、唯一 MMI 窗口以及最终工作区状态。

在所有上述工作完成并验证前，不要把任务标记为完成。

## 提示词正文结束
