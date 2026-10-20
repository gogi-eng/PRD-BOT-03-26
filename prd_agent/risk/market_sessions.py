"""Границы торговых сессий по московскому времени (таблица в README).

Убыток считается не до полуночи, а до конца той сессии, в которой он случился.
Сессии пересекаются, поэтому окно — отрезок до ближайшего конца:

- 01:00–09:00 Сидней
- 09:00–12:00 Азия (Токио)
- 12:00–19:00 Европа (Франкфурт)
- 19:00–20:00 Лондон
- 20:00–01:00 Нью-Йорк
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Минуты от полуночи, когда сессия заканчивается и начинается следующая.
_ENDS = (60, 9 * 60, 12 * 60, 19 * 60, 20 * 60)
_NAME_BY_END = {
    60: "Нью-Йорк",
    9 * 60: "Сидней",
    12 * 60: "Азия",
    19 * 60: "Европа",
    20 * 60: "Лондон",
}


@dataclass(frozen=True)
class SessionWindow:
    start_utc: datetime
    end_utc: datetime
    name: str
    end_hhmm: str


def _as_utc(now_utc: datetime) -> datetime:
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def _wall_to_utc(wall: datetime, tz_offset: int) -> datetime:
    """wall — часы местного времени, но с меткой UTC (так считает остальной бот)."""
    return (wall - timedelta(hours=int(tz_offset))).replace(tzinfo=timezone.utc)


def session_window(now_utc: datetime, tz_offset: int) -> SessionWindow:
    """Текущее окно сессии. В момент ровной границы уже идёт следующая сессия."""
    off = int(tz_offset)
    now = _as_utc(now_utc)
    local = now + timedelta(hours=off)
    minutes = local.hour * 60 + local.minute

    next_end = None
    prev_end = 20 * 60
    for end in _ENDS:
        if minutes < end:
            next_end = end
            break
        prev_end = end

    if next_end is None:
        next_end = 60
        prev_end = 20 * 60
        end_local = local.replace(hour=1, minute=0, second=0, microsecond=0) + timedelta(days=1)
        start_local = local.replace(hour=20, minute=0, second=0, microsecond=0)
    else:
        end_local = local.replace(
            hour=next_end // 60,
            minute=next_end % 60,
            second=0,
            microsecond=0,
        )
        if prev_end == 20 * 60 and next_end == 60:
            start_local = (local - timedelta(days=1)).replace(
                hour=20, minute=0, second=0, microsecond=0
            )
        else:
            start_local = local.replace(
                hour=prev_end // 60,
                minute=prev_end % 60,
                second=0,
                microsecond=0,
            )

    hh = next_end // 60
    mm = next_end % 60
    return SessionWindow(
        start_utc=_wall_to_utc(start_local, off),
        end_utc=_wall_to_utc(end_local, off),
        name=_NAME_BY_END[next_end],
        end_hhmm=f"{hh:02d}:{mm:02d}",
    )
