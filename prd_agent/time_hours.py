"""Часы входа: config-списки в местном времени (timezone_offset), не в UTC."""
from __future__ import annotations

from typing import Any, Dict


def read_timezone_offset(cfg: Dict[str, Any]) -> int:
    raw = cfg.get("timezone_offset")
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def entry_check_hour(utc_hour: int, tz_offset: int) -> int:
    """Час для сравнения с block_entry_* / seed_blocked_*."""
    hour = int(utc_hour) % 24
    if tz_offset:
        return (hour + int(tz_offset)) % 24
    return hour


def format_blocked_hour_label(utc_hour: int, check_hour: int, tz_offset: int) -> str:
    if tz_offset:
        sign = f"+{tz_offset}" if tz_offset > 0 else str(tz_offset)
        return f"местный UTC{sign} час {check_hour} (сейчас UTC {utc_hour % 24})"
    return f"UTC час {utc_hour % 24}"
