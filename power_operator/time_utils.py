from __future__ import annotations

from datetime import datetime


def format_float(value: int | float) -> str:
    """Format a displayed real value with exactly three decimal places."""

    return f"{float(value):.3f}"


def format_simu_time(value: int | float) -> str:
    """Format elapsed running seconds as HH:mm:ss without 24-hour wrapping."""

    total_seconds = max(0, int(value))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_simu_time(value: str) -> int:
    """Parse either elapsed integer seconds or an HH:mm:ss string."""

    text = value.strip()
    if ":" not in text:
        seconds = int(text)
        if seconds < 0:
            raise ValueError("运行时刻不能为负数")
        return seconds
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("运行时刻必须是 HH:mm:ss")
    hours, minutes, seconds = (int(part) for part in parts)
    if hours < 0 or not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError("运行时刻必须是有效的 HH:mm:ss")
    return hours * 3600 + minutes * 60 + seconds


def format_wall_time(value: int | float) -> str:
    """Format a Unix wall-clock timestamp in the machine's local timezone."""

    timestamp = int(value)
    if timestamp <= 0:
        return "--"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def parse_wall_time(value: str) -> int:
    """Parse either Unix seconds or a local yyyy-MM-dd HH:mm:ss value."""

    text = value.strip()
    if text in ("", "--"):
        return 0
    if text.lstrip("+").isdigit():
        timestamp = int(text)
        if timestamp < 0:
            raise ValueError("墙钟时刻不能为负数")
        return timestamp
    try:
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp())
    except ValueError as exc:
        raise ValueError("墙钟时刻必须是 Unix 秒或 yyyy-MM-dd HH:mm:ss") from exc
