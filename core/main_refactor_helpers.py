"""Small pure helpers used by TradingBot (intervals, kline time, ISO parsing, closed-PnL filters)."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def interval_to_seconds(interval: str | int | float) -> int:
    if isinstance(interval, (int, float)):
        return max(1, int(interval))
    s = str(interval).strip().lower()
    if not s:
        return 60
    mult = 1
    if s.endswith("m"):
        mult = 60
        s = s[:-1]
    elif s.endswith("h"):
        mult = 3600
        s = s[:-1]
    elif s.endswith("d"):
        mult = 86400
        s = s[:-1]
    try:
        return max(1, int(float(s) * mult))
    except ValueError:
        return 60


def last_closed_kline_ts(klines: list) -> int:
    if not klines:
        return 0
    last = klines[-1]
    ts = last.get("start") or last.get("timestamp") or 0
    try:
        return int(ts)
    except (TypeError, ValueError):
        return 0


def parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})", s)
        if not m:
            return None
        try:
            dt = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}")
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None


def filter_recent_closed_pnl(closed_records: list | None, max_age_sec: int = 300) -> list:
    if not closed_records:
        return []
    now = datetime.now(timezone.utc).timestamp()
    out: list[Any] = []
    for rec in closed_records:
        ts = rec.get("createdTime") or rec.get("updatedTime") or rec.get("time")
        if not ts:
            continue
        try:
            t = float(ts) / (1000.0 if float(ts) > 1e12 else 1.0)
        except (TypeError, ValueError):
            continue
        if now - t <= max(1, int(max_age_sec)):
            out.append(rec)
    return out


def classify_exchange_closed_reason(closed_records: list | None) -> str:
    if not closed_records:
        return "exchange_closed"
    return "exchange_closed"
