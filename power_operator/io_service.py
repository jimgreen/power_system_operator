from __future__ import annotations

import json
import logging
import socket
import socketserver
import time
from typing import Any, Callable, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import Database
from .models import (
    OperatorControl,
    OperatorHistory,
    OperatorLog,
    ScadaRtu,
    ScadaYc,
    ScadaYcHis,
    ScadaYk,
    ScadaYkHis,
    ScadaYt,
    ScadaYtHis,
    ScadaYx,
    ScadaYxHis,
)
from .status_commands import yk_requires_status_change
from .retired_measurements import (
    is_retired_operator_yc_measurement_name,
    is_retired_wind_angle_setpoint_name,
)

LOGGER = logging.getLogger(__name__)
LOG_WARNING = 2

_SCADA_SIGNAL_NAMES = {
    ScadaYc: "YC",
    ScadaYx: "YX",
    ScadaYt: "YT",
    ScadaYk: "YK",
}


class CoreProcessController(Protocol):
    """Minimal process lifecycle contract used during simulator clock rollback."""

    def stop_and_wait(self) -> None: ...

    def start(self) -> None: ...


class _NoopCoreProcessController:
    """Library default; the operator_io entry point injects the real manager."""

    def stop_and_wait(self) -> None:
        return None

    def start(self) -> None:
        return None


def _number(value: Any, kind: type[int] | type[float], field: str):
    try:
        return kind(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是{kind.__name__}") from exc


def _record_unknown_scada_point(
    session: Session,
    *,
    model,
    row: dict,
    pnt_no: int,
    simu_time: int,
    log_wall_time: int,
    source: str,
) -> None:
    signal = _SCADA_SIGNAL_NAMES[model]
    received_name = str(row.get("name", "")).strip()
    warning = {
        "message": f"收到未知 {signal} 点号 {int(pnt_no)}，已忽略",
        "schema_version": 1,
        "event": "unknown_scada_point",
        "source": str(source),
        "signal": signal,
        "pnt_no": int(pnt_no),
        "received_name": received_name,
        "simu_time": max(0, int(simu_time)),
    }
    session.add(
        OperatorLog(
            log_time=int(log_wall_time),
            simu_time=max(0, int(simu_time)),
            log_type=LOG_WARNING,
            log_info=json.dumps(warning, ensure_ascii=False, allow_nan=False),
        )
    )
    LOGGER.warning(
        "收到未知 %s 点号，已忽略：pnt_no=%d, name=%r, source=%s, simu_time=%d",
        signal,
        pnt_no,
        received_name,
        source,
        simu_time,
    )


def update_existing_scada_points(
    session: Session,
    model,
    rows: list[dict],
    now: int,
    *,
    log_wall_time: int | None = None,
    source: str = "operator_io",
    use_row_time: bool = False,
) -> None:
    """Update only value/time of operator-defined SCADA points.

    Point identity and names belong to the local definition tables. Runtime
    packets can neither create a point nor rename/reidentify an existing one.
    Every unknown point is ignored and persisted as a warning for operators.
    """

    if model not in _SCADA_SIGNAL_NAMES:
        raise ValueError(f"不支持的四遥模型: {model}")
    simu_time = int(now)
    warning_wall_time = (
        int(time.time()) if log_wall_time is None else int(log_wall_time)
    )
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("四遥数据项必须是对象")
        pnt_no = _number(row.get("pnt_no"), int, "pnt_no")
        point = session.get(model, pnt_no)
        if point is None:
            received_name = row.get("name", "")
            if model is ScadaYc and is_retired_operator_yc_measurement_name(
                received_name
            ):
                continue
            if model is ScadaYt and is_retired_wind_angle_setpoint_name(
                received_name
            ):
                continue
            _record_unknown_scada_point(
                session,
                model=model,
                row=row,
                pnt_no=pnt_no,
                simu_time=simu_time,
                log_wall_time=warning_wall_time,
                source=source,
            )
            continue
        point_time = simu_time
        if use_row_time:
            point_time = _number(row.get("time"), int, "time")
            if point_time < 0:
                raise ValueError("四遥数据项 time 不能为负数")
            if point_time > simu_time:
                raise ValueError("四遥数据项 time 不能晚于响应 simu_time")
        if point_time <= 0:
            continue
        if model is ScadaYc and is_retired_operator_yc_measurement_name(point.name):
            continue
        if model is ScadaYt and is_retired_wind_angle_setpoint_name(point.name):
            continue
        value_type = float if model in (ScadaYc, ScadaYt) else int
        point.value = _number(row.get("value"), value_type, "value")
        point.time = point_time


def _map_measurement_response_rows(
    rows: list[dict],
    requested_point_numbers: list[int],
    response_time: int,
    signal: str,
) -> tuple[list[dict], bool]:
    """Restore local point identities for positional value/time-only responses.

    The protocol returns exactly one item per requested point in the same
    order, and each item contains only ``value`` and ``time``.  Request order,
    duplicates, zero-time points and unknown-point placeholders are preserved
    because omitting any position would make an identifier-free response
    ambiguous.
    """

    if len(rows) != len(requested_point_numbers):
        raise ValueError(
            f"simulator_io 的 {signal} 响应数量与请求不一致："
            f"请求 {len(requested_point_numbers)}，返回 {len(rows)}"
        )
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"simulator_io 的 {signal} 数据项必须是对象")

    mapped: list[dict] = []
    for point_number, row in zip(requested_point_numbers, rows, strict=True):
        if set(row) != {"value", "time"}:
            raise ValueError(
                f"simulator_io 的 {signal} 数据项只能包含 value 和 time"
            )
        point_time = _number(row.get("time"), int, "time")
        if point_time < 0:
            raise ValueError(f"simulator_io 的 {signal} 数据项 time 不能为负数")
        if point_time > response_time:
            raise ValueError(
                f"simulator_io 的 {signal} 数据项 time 不能晚于 simu_time"
            )
        mapped.append(
            {
                "pnt_no": int(point_number),
                "value": row.get("value"),
                "time": point_time,
            }
        )
    return mapped, True


def apply_rtu_request(database: Database, request: dict[str, Any], now: int | None = None) -> dict:
    """Apply one JSON-line RTU exchange atomically."""

    if not isinstance(request, dict):
        raise ValueError("请求必须是 JSON 对象")
    wall_time = int(time.time()) if now is None else int(now)
    rtu_id = _number(request.get("rtu_id"), int, "rtu_id")
    last_yt_time = max(0, _number(request.get("last_yt_time", 0), int, "last_yt_time"))
    last_yk_time = max(0, _number(request.get("last_yk_time", 0), int, "last_yk_time"))
    yc_rows = request.get("yc", [])
    yx_rows = request.get("yx", [])
    if not isinstance(yc_rows, list) or not isinstance(yx_rows, list):
        raise ValueError("yc/yx 必须是数组")

    def exchange(session: Session) -> dict:
        control = session.get(OperatorControl, 1)
        database_simu_time = control.data_time_curr if control is not None else 0
        simu_time = _number(request.get("simu_time", database_simu_time), int, "simu_time")
        if simu_time < 0:
            raise ValueError("simu_time 不能为负数")
        rtu = session.get(ScadaRtu, rtu_id)
        if rtu is None:
            peer_ip = str(request.get("ip", ""))
            rtu = ScadaRtu(
                id=rtu_id,
                ip=peer_ip,
                port=_number(request.get("port", 0), int, "port"),
                status=1,
                refresh_time=wall_time,
            )
            session.add(rtu)
        else:
            rtu.status = 1
            rtu.refresh_time = wall_time
            if request.get("ip"):
                rtu.ip = str(request["ip"])
            if request.get("port") is not None:
                rtu.port = _number(request["port"], int, "port")

        update_existing_scada_points(
            session,
            ScadaYc,
            yc_rows,
            simu_time,
            log_wall_time=wall_time,
            source="operator_io.compat_server",
        )
        update_existing_scada_points(
            session,
            ScadaYx,
            yx_rows,
            simu_time,
            log_wall_time=wall_time,
            source="operator_io.compat_server",
        )
        session.flush()
        if control is not None and simu_time > 0:
            control.data_time_curr = max(int(control.data_time_curr), simu_time)
        yt_rows = [
            row
            for row in session.scalars(
                select(ScadaYt)
                .where(ScadaYt.time > last_yt_time)
                .order_by(ScadaYt.time, ScadaYt.pnt_no)
            ).all()
            if not is_retired_wind_angle_setpoint_name(row.name)
        ]
        yk_candidates = session.scalars(
            select(ScadaYk).where(ScadaYk.time > last_yk_time).order_by(ScadaYk.time, ScadaYk.pnt_no)
        ).all()
        yk_rows = [row for row in yk_candidates if yk_requires_status_change(session, row)]
        return {
            "ok": True,
            "server_time": wall_time,
            "simu_time": simu_time,
            "yt": [
                {"pnt_no": row.pnt_no, "name": row.name, "value": row.value, "time": row.time}
                for row in yt_rows
            ],
            "yk": [
                {"pnt_no": row.pnt_no, "name": row.name, "value": row.value, "time": row.time}
                for row in yk_rows
            ],
        }

    return database.write(exchange)


class RtuRequestHandler(socketserver.StreamRequestHandler):
    max_line_bytes = 2 * 1024 * 1024

    def handle(self) -> None:
        line = self.rfile.readline(self.max_line_bytes + 1)
        if len(line) > self.max_line_bytes:
            response = {"ok": False, "error": "请求过大"}
        else:
            try:
                request = json.loads(line.decode("utf-8"))
                request.setdefault("ip", self.client_address[0])
                request.setdefault("port", self.client_address[1])
                response = apply_rtu_request(self.server.database, request)  # type: ignore[attr-defined]
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                response = {"ok": False, "error": str(exc)}
            except Exception as exc:
                response = {"ok": False, "error": f"服务端错误: {exc}"}
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")


class ThreadingRtuServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], database: Database):
        self.database = database
        super().__init__(address, RtuRequestHandler)


class SimulatorIoClient:
    """UTF-8 JSON-line client for the external simulator_io service."""

    max_line_bytes = 2 * 1024 * 1024

    def __init__(self, host: str = "127.0.0.1", port: int = 9200, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def exchange(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8") + b"\n"
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as connection:
            connection.sendall(payload)
            response_file = connection.makefile("rb")
            line = response_file.readline(self.max_line_bytes + 1)
        if not line:
            raise ConnectionError("simulator_io 未返回数据")
        if len(line) > self.max_line_bytes:
            raise ValueError("simulator_io 响应过大")
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"simulator_io 响应不是有效 JSON: {exc}") from exc
        if not isinstance(response, dict):
            raise ValueError("simulator_io 响应必须是 JSON 对象")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error", "simulator_io 返回失败")))
        return response

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.exchange(request)


class OperatorIoBridge:
    """Persistent DB-to-simulator_io bridge with independent read/write periods."""

    def __init__(
        self,
        database: Database,
        transport: Callable[[dict[str, Any]], dict[str, Any]],
        rtu_id: int = 1,
        peer_ip: str = "",
        peer_port: int = 0,
        wall_clock: Callable[[], float] | None = None,
        core_process_manager: CoreProcessController | None = None,
    ):
        self.database = database
        self.transport = transport
        self.rtu_id = rtu_id
        self.peer_ip = peer_ip
        self.peer_port = peer_port
        self.wall_clock = wall_clock or time.time
        self.core_process_manager = core_process_manager or _NoopCoreProcessController()
        self._last_data_monotonic: float | None = None
        self._last_control_monotonic: float | None = None
        self._last_yt_time = 0
        self._last_yk_time = 0
        self._connection_status: int | None = None
        self._core_suspended_run_seq: int | None = None

    def mark_disconnected(self) -> None:
        """Persist the bridge connection state without changing its last good data time."""

        if self._connection_status == 0:
            with self.database.session() as session:
                current = session.get(ScadaRtu, self.rtu_id)
                if current is not None and int(current.status) == 0:
                    return

        def update(session: Session) -> None:
            rtu = session.get(ScadaRtu, self.rtu_id)
            if rtu is None:
                session.add(
                    ScadaRtu(
                        id=self.rtu_id,
                        ip=self.peer_ip,
                        port=self.peer_port,
                        status=0,
                        refresh_time=0,
                    )
                )
                return
            rtu.status = 0
            if self.peer_ip:
                rtu.ip = self.peer_ip
            if self.peer_port:
                rtu.port = self.peer_port

        self.database.write(update)
        self._connection_status = 0

    def _read_control(self) -> tuple[int, int, int, int]:
        with self.database.session() as session:
            control = session.get(OperatorControl, 1)
            if control is None:
                return 0, 1, 1, 0
            return (
                int(control.oper_status),
                int(control.io_connect_enabled),
                max(1, int(control.data_period)),
                max(0, int(control.data_time_curr)),
            )

    def _reset_period_state(self) -> None:
        self._last_data_monotonic = None
        self._last_control_monotonic = None
        self._last_yt_time = 0
        self._last_yk_time = 0

    @staticmethod
    def _clear_runtime_for_clock_rollback(session: Session) -> None:
        """Clear time-series/runtime SCADA state while preserving point definitions."""

        control = session.get(OperatorControl, 1)
        if control is not None:
            # The database clocks are deliberately flushed first so the SQL
            # execution order mirrors the required recovery protocol:
            # clock rollback -> runtime cleanup -> new packet application.
            control.data_time_curr = 0
            control.oper_time_curr = 0
            session.flush()
        for model in (
            OperatorHistory,
            OperatorLog,
            ScadaYcHis,
            ScadaYxHis,
            ScadaYtHis,
            ScadaYkHis,
        ):
            session.execute(delete(model))
        for model in (ScadaYc, ScadaYt):
            for point in session.scalars(select(model)).all():
                point.value = 0.0
                point.time = 0
        for model in (ScadaYx, ScadaYk):
            for point in session.scalars(select(model)).all():
                point.value = 0
                point.time = 0
        session.flush()

    def _pull_measurements(self, current_time: int, data_period: int) -> bool:
        requested_time = current_time + data_period
        with self.database.session() as session:
            control = session.get(OperatorControl, 1)
            saved_run_seq = int(control.source_run_seq) if control is not None else 0
            saved_time_start = (
                int(control.source_time_start) if control is not None else 0
            )
            saved_runtime_ready = (
                int(control.source_runtime_ready) if control is not None else 0
            )
            requested_yc = [
                int(point.pnt_no)
                for point in session.scalars(
                    select(ScadaYc).order_by(ScadaYc.pnt_no)
                ).all()
                if not is_retired_operator_yc_measurement_name(point.name)
            ]
            requested_yx = [
                int(point.pnt_no)
                for point in session.scalars(
                    select(ScadaYx).order_by(ScadaYx.pnt_no)
                ).all()
            ]
        response = self.transport(
            {
                "action": "read",
                "rtu_id": self.rtu_id,
                "simu_time": requested_time,
                "data": {"yc": requested_yc, "yx": requested_yx},
            }
        )
        if "simu_time" not in response:
            raise ValueError("simulator_io 的 YC/YX 响应缺少 simu_time")
        response_time = _number(response["simu_time"], int, "simu_time")
        if response_time < 0:
            raise ValueError("simulator_io 的 simu_time 不能为负数")
        has_run_metadata = "run_seq" in response
        if has_run_metadata:
            response_run_seq = _number(response["run_seq"], int, "run_seq")
            response_time_start = _number(
                response.get("simu_time_start"), int, "simu_time_start"
            )
            response_status = _number(
                response.get("simu_status"), int, "simu_status"
            )
            response_runtime_ready = response.get("runtime_ready")
            if response_run_seq < 0:
                raise ValueError("simulator_io 的 run_seq 不能为负数")
            if response_time_start < 0:
                raise ValueError("simulator_io 的 simu_time_start 不能为负数")
            if response_status not in (0, 1, 2):
                raise ValueError("simulator_io 的 simu_status 必须是0、1或2")
            if not isinstance(response_runtime_ready, bool):
                raise ValueError("simulator_io 的 runtime_ready 必须是布尔值")
        else:
            # Compatibility for development mocks and older peers.  A legacy
            # packet cannot declare a task boundary, so preserve the established
            # clock-rollback behavior until all peers have upgraded.
            response_run_seq = saved_run_seq
            response_time_start = saved_time_start
            response_runtime_ready = True
        refresh_wall_time = int(self.wall_clock())

        nested_data = response.get("data")
        if nested_data is not None:
            if not isinstance(nested_data, dict):
                raise ValueError("simulator_io 的 data 必须是对象")
            yc_rows = nested_data.get("yc", [])
            yx_rows = nested_data.get("yx", [])
        else:
            # Keep compatibility with the lightweight development Mock while
            # preferring the real simulator's data.yc/data.yx packet shape.
            yc_rows = response.get("yc", [])
            yx_rows = response.get("yx", [])
        if not isinstance(yc_rows, list) or not isinstance(yx_rows, list):
            raise ValueError("simulator_io 的 yc/yx 必须是数组")
        yc_rows, yc_uses_row_time = _map_measurement_response_rows(
            yc_rows,
            requested_yc,
            response_time,
            "YC",
        )
        yx_rows, yx_uses_row_time = _map_measurement_response_rows(
            yx_rows,
            requested_yx,
            response_time,
            "YX",
        )

        valid_measurement_received = any(
            int(row["time"]) > 0 and row.get("value") is not None
            for row in (*yc_rows, *yx_rows)
        )
        packet_ready = bool(response_runtime_ready and valid_measurement_received)
        run_changed = has_run_metadata and response_run_seq != saved_run_seq
        clock_rollback = response_time < current_time and not run_changed
        pending_run = has_run_metadata and saved_runtime_ready == 0
        first_sync_for_pending_run = (
            pending_run and self._core_suspended_run_seq != response_run_seq
        )
        lifecycle_reset = run_changed or clock_rollback or first_sync_for_pending_run
        manage_core = lifecycle_reset or pending_run

        def apply(session: Session) -> bool:
            control = session.get(OperatorControl, 1)
            if (
                control is None
                or int(control.oper_status) != 1
                or int(control.io_connect_enabled) != 1
            ):
                return False
            if lifecycle_reset:
                self._clear_runtime_for_clock_rollback(session)
            if has_run_metadata:
                control.source_run_seq = response_run_seq
                control.source_time_start = response_time_start
                control.source_runtime_ready = int(packet_ready)
            rows_are_usable = not has_run_metadata or packet_ready
            update_existing_scada_points(
                session,
                ScadaYc,
                yc_rows if rows_are_usable else [],
                response_time,
                log_wall_time=refresh_wall_time,
                source="operator_io.bridge",
                use_row_time=yc_uses_row_time,
            )
            update_existing_scada_points(
                session,
                ScadaYx,
                yx_rows if rows_are_usable else [],
                response_time,
                log_wall_time=refresh_wall_time,
                source="operator_io.bridge",
                use_row_time=yx_uses_row_time,
            )
            rtu = session.get(ScadaRtu, self.rtu_id)
            if rtu is None:
                rtu = ScadaRtu(
                    id=self.rtu_id,
                    ip=self.peer_ip,
                    port=self.peer_port,
                    status=1,
                    refresh_time=refresh_wall_time,
                )
                session.add(rtu)
            else:
                rtu.ip = self.peer_ip or rtu.ip
                rtu.port = self.peer_port or rtu.port
                rtu.status = 1
                rtu.refresh_time = refresh_wall_time
            # The simulator clock is authoritative.  Do not invent time from
            # the request or locally advance it; an equal or reset clock is a
            # valid synchronization result.
            control.data_time_curr = response_time
            return True

        if not manage_core:
            self.database.write(apply)
            self._connection_status = 1
            return False

        # Lifecycle actions stay outside the SQLite write transaction.  A new
        # run is authoritative even when its clock moves forward or is equal.
        core_already_suspended = (
            has_run_metadata
            and self._core_suspended_run_seq == response_run_seq
        )
        if not core_already_suspended:
            self.core_process_manager.stop_and_wait()
            self._core_suspended_run_seq = (
                response_run_seq if has_run_metadata else None
            )
        previous_period_state = (
            self._last_data_monotonic,
            self._last_control_monotonic,
            self._last_yt_time,
            self._last_yk_time,
        )
        self._reset_period_state()
        try:
            applied = self.database.write(apply)
        except Exception:
            (
                self._last_data_monotonic,
                self._last_control_monotonic,
                self._last_yt_time,
                self._last_yk_time,
            ) = previous_period_state
            try:
                if not has_run_metadata or saved_runtime_ready:
                    self.core_process_manager.start()
                    self._core_suspended_run_seq = None
            except Exception:
                LOGGER.exception("任务切换事务失败后恢复 operator_core 失败")
            raise

        if not applied:
            (
                self._last_data_monotonic,
                self._last_control_monotonic,
                self._last_yt_time,
                self._last_yk_time,
            ) = previous_period_state
            if not has_run_metadata or saved_runtime_ready:
                self.core_process_manager.start()
                self._core_suspended_run_seq = None
            return False
        if not has_run_metadata or packet_ready:
            try:
                self.core_process_manager.start()
                self._core_suspended_run_seq = None
            except Exception:
                # The reset is already committed. Leave the period state at
                # zero and surface the failure so RTU status becomes offline.
                raise
        self._connection_status = 1
        if run_changed:
            LOGGER.warning(
                "检测到电网模拟器新任务 run_seq=%d -> %d，时刻=%d -> %d；"
                "已清理本地运行数据，runtime_ready=%s",
                saved_run_seq,
                response_run_seq,
                current_time,
                response_time,
                packet_ready,
            )
        elif clock_rollback:
            LOGGER.warning(
                "检测到电网模拟器时钟回退 %d -> %d；已清理本地运行数据",
                current_time,
                response_time,
            )
        elif packet_ready:
            LOGGER.info(
                "电网模拟器任务 run_seq=%d 首个有效断面已就绪，Core 已恢复",
                response_run_seq,
            )
        return True

    @staticmethod
    def _point_payload(row) -> dict[str, Any]:
        return {"pnt_no": row.pnt_no, "name": row.name, "value": row.value, "time": row.time}

    def _push_changed_commands(self, _current_time: int) -> None:
        with self.database.session() as session:
            valid_after_yt = max(0, self._last_yt_time)
            valid_after_yk = max(0, self._last_yk_time)
            yt_candidates = session.scalars(
                select(ScadaYt)
                .where(ScadaYt.time > valid_after_yt)
                .order_by(ScadaYt.time, ScadaYt.pnt_no)
            ).all()
            yt_rows = [
                row
                for row in yt_candidates
                if not is_retired_wind_angle_setpoint_name(row.name)
            ]
            yk_candidates = session.scalars(
                select(ScadaYk)
                .where(ScadaYk.time > valid_after_yk)
                .order_by(ScadaYk.time, ScadaYk.pnt_no)
            ).all()
            yk_rows = [
                row for row in yk_candidates if yk_requires_status_change(session, row)
            ]
            latest_checked_yk_time = max(
                [self._last_yk_time, *[int(row.time) for row in yk_candidates]]
            )
            latest_checked_yt_time = max(
                [self._last_yt_time, *[int(row.time) for row in yt_candidates]]
            )
        if not yt_rows and not yk_rows:
            self._last_yt_time = latest_checked_yt_time
            self._last_yk_time = latest_checked_yk_time
            return
        self.transport(
            {
                "action": "write",
                "data": {
                    "yt": [self._point_payload(row) for row in yt_rows],
                    "yk": [self._point_payload(row) for row in yk_rows],
                },
            }
        )
        self._last_yt_time = latest_checked_yt_time
        self._last_yk_time = latest_checked_yk_time

    def tick(self, monotonic_time: float | None = None) -> None:
        now = time.monotonic() if monotonic_time is None else float(monotonic_time)
        oper_status, connect_enabled, data_period, current_time = self._read_control()
        if oper_status != 1 or connect_enabled != 1:
            self.mark_disconnected()
            self._last_data_monotonic = None
            self._last_control_monotonic = None
            return
        try:
            if (
                self._last_data_monotonic is None
                or now - self._last_data_monotonic >= data_period
            ):
                clock_rollback = self._pull_measurements(current_time, data_period)
                if clock_rollback:
                    return
                self._last_data_monotonic = now
                _, _, _, current_time = self._read_control()
            if self._last_control_monotonic is None:
                self._last_control_monotonic = now
            elif now - self._last_control_monotonic >= 1.0:
                self._push_changed_commands(current_time)
                self._last_control_monotonic = now
        except Exception:
            self.mark_disconnected()
            raise

    def run_forever(self, poll_seconds: float = 0.5, stop_event=None) -> None:
        LOGGER.info("operator_io 桥接循环已启动")
        while stop_event is None or not stop_event.is_set():
            started = time.monotonic()
            try:
                self.tick(started)
            except Exception:
                LOGGER.exception("operator_io 交换失败")
            delay = max(0.0, poll_seconds - (time.monotonic() - started))
            if stop_event is None:
                time.sleep(delay)
            elif stop_event.wait(delay):
                break
        LOGGER.info("operator_io 桥接循环已停止")
