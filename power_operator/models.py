from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ScadaRtu(Base):
    __tablename__ = "scada_rtu"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    port: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    refresh_time: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYc(Base):
    __tablename__ = "scada_yc"

    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[float] = mapped_column(Float, default=0.0)
    time: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYx(Base):
    __tablename__ = "scada_yx"

    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[int] = mapped_column(Integer, default=0)
    time: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYk(Base):
    __tablename__ = "scada_yk"

    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[int] = mapped_column(Integer, default=0)
    time: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYt(Base):
    __tablename__ = "scada_yt"

    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[float] = mapped_column(Float, default=0.0)
    time: Mapped[int] = mapped_column(Integer, default=0)


class DevDiesalGen(Base):
    """Diesel generator; table name keeps the spelling from the requirement."""

    __tablename__ = "dev_diesal_gen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    p_rated: Mapped[float] = mapped_column(Float, default=0.0)
    p_max: Mapped[float] = mapped_column(Float, default=0.0)
    p_min: Mapped[float] = mapped_column(Float, default=0.0)
    p_coeff: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    p_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_set: Mapped[float] = mapped_column(Float, default=0.0)


class DevWindGen(Base):
    __tablename__ = "dev_wind_gen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    p_rated: Mapped[float] = mapped_column(Float, default=0.0)
    wind_in: Mapped[float] = mapped_column(Float, default=0.0)
    wind_rated: Mapped[float] = mapped_column(Float, default=0.0)
    wind_cut: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    p_max_curr: Mapped[float] = mapped_column(Float, default=0.0)
    angle_pitch_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_set: Mapped[float] = mapped_column(Float, default=0.0)


class DevSolarGen(Base):
    __tablename__ = "dev_solar_gen"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    p_rated: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[int] = mapped_column(Integer, default=0)
    p_max_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_set: Mapped[float] = mapped_column(Float, default=0.0)


class DevEstore(Base):
    __tablename__ = "dev_estore"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    p_charge_max: Mapped[float] = mapped_column(Float, default=0.0)
    p_charge_eff: Mapped[float] = mapped_column(Float, default=1.0)
    p_discharge_max: Mapped[float] = mapped_column(Float, default=0.0)
    p_discharge_eff: Mapped[float] = mapped_column(Float, default=1.0)
    p_curr: Mapped[float] = mapped_column(Float, default=0.0)
    p_set: Mapped[float] = mapped_column(Float, default=0.0)
    # The requirement says "battery capacity". SQL identifiers use snake_case.
    battery_capacity: Mapped[float] = mapped_column(Float, default=0.0)
    soc_curr: Mapped[float] = mapped_column(Float, default=0.0)
    soc_max: Mapped[float] = mapped_column(Float, default=1.0)
    soc_min: Mapped[float] = mapped_column(Float, default=0.0)


class DevLoad(Base):
    __tablename__ = "dev_load"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    p_curr: Mapped[float] = mapped_column(Float, default=0.0)


class OperatorLog(Base):
    __tablename__ = "operator_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    log_time: Mapped[int] = mapped_column(Integer, index=True)
    simu_time: Mapped[int] = mapped_column(Integer, index=True)
    log_type: Mapped[int] = mapped_column(Integer, default=0)
    # Decision audit records contain complete inputs, process steps, and
    # outputs.  SQLite TEXT avoids the old 1024-character presentation limit.
    log_info: Mapped[str] = mapped_column(Text, default="")


class OperatorHistory(Base):
    __tablename__ = "operator_history"

    simu_time: Mapped[int] = mapped_column(Integer, primary_key=True)
    wind_speed: Mapped[float] = mapped_column(Float, default=0.0)
    solar_radiation: Mapped[float] = mapped_column(Float, default=0.0)
    amb_temp: Mapped[float] = mapped_column(Float, default=0.0)
    diesal_power_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    diesal_power_set_sum: Mapped[float] = mapped_column(Float, default=0.0)
    diesal_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    wind_power_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    wind_power_max_sum: Mapped[float] = mapped_column(Float, default=0.0)
    wind_power_set_sum: Mapped[float] = mapped_column(Float, default=0.0)
    solar_power_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    solar_power_max_sum: Mapped[float] = mapped_column(Float, default=0.0)
    solar_power_set_sum: Mapped[float] = mapped_column(Float, default=0.0)
    load_power_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    estore_power_curr_sum: Mapped[float] = mapped_column(Float, default=0.0)
    estore_power_set_sum: Mapped[float] = mapped_column(Float, default=0.0)
    estore_power_soc_sum: Mapped[float] = mapped_column(Float, default=0.0)


class OperatorControl(Base):
    __tablename__ = "operator_control"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    oper_status: Mapped[int] = mapped_column(Integer, default=0)
    control_status: Mapped[int] = mapped_column(Integer, default=0)
    # Connection command requested by the MMI.  The actual TCP state remains
    # in scada_rtu.status and is updated only by operator_io.
    io_connect_enabled: Mapped[int] = mapped_column(Integer, default=1)
    data_period: Mapped[int] = mapped_column(Integer, default=1)
    oper_period: Mapped[int] = mapped_column(Integer, default=1)
    data_time_curr: Mapped[int] = mapped_column(Integer, default=0)
    oper_time_curr: Mapped[int] = mapped_column(Integer, default=0)
    # Identity/readiness of the authoritative simulator task currently mirrored
    # into this database.  A changed sequence is a task boundary even when the
    # new simulation time moves forward or stays equal.
    source_run_seq: Mapped[int] = mapped_column(Integer, default=0)
    source_time_start: Mapped[int] = mapped_column(Integer, default=0)
    source_runtime_ready: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYcHis(Base):
    __tablename__ = "scada_yc_his"
    __table_args__ = (Index("ix_scada_yc_his_pnt_time", "pnt_no", "time"),)

    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)


class ScadaYtHis(Base):
    __tablename__ = "scada_yt_his"
    __table_args__ = (Index("ix_scada_yt_his_pnt_time", "pnt_no", "time"),)

    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[float] = mapped_column(Float, default=0.0)


class ScadaYxHis(Base):
    __tablename__ = "scada_yx_his"
    __table_args__ = (Index("ix_scada_yx_his_pnt_time", "pnt_no", "time"),)

    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class ScadaYkHis(Base):
    __tablename__ = "scada_yk_his"
    __table_args__ = (Index("ix_scada_yk_his_pnt_time", "pnt_no", "time"),)

    time: Mapped[int] = mapped_column(Integer, primary_key=True)
    pnt_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


SCADA_CURRENT_MODELS = (ScadaYc, ScadaYx, ScadaYk, ScadaYt)
DEVICE_MODELS = (DevDiesalGen, DevWindGen, DevSolarGen, DevEstore, DevLoad)
