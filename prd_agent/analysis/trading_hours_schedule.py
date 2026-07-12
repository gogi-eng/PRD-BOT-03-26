"""Расписание stop/start ботов по block_entry_utc_hours (местное время)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[misc, assignment]

from prd_agent.time_hours import read_timezone_offset


@dataclass(frozen=True)
class TradingWindow:
    stop_at: str
    resume_at: str
    start_hour: int
    end_hour: int

    @property
    def stop_cron(self) -> str:
        h, m = _parse_hhmm(self.stop_at)
        return f"{m} {h}"

    @property
    def resume_cron(self) -> str:
        h, m = _parse_hhmm(self.resume_at)
        return f"{m} {h}"


def _parse_hhmm(value: str) -> Tuple[int, int]:
    parts = str(value).strip().split(":", 1)
    return int(parts[0]) % 24, int(parts[1]) % 60


def _parse_hour_list(raw: Any) -> Set[int]:
    hours: Set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            try:
                hours.add(int(item) % 24)
            except (TypeError, ValueError):
                continue
    return hours


def read_ny_open_block_settings(cfg: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    sched = trading.get("non_trading_systemd") if isinstance(trading.get("non_trading_systemd"), dict) else {}
    raw = sched.get("ny_open_block")
    if not isinstance(raw, dict) or not bool(raw.get("enabled", False)):
        return None
    return raw


def ny_open_block_hours_msk(
    cfg: Mapping[str, Any],
    *,
    when: Optional[datetime] = None,
) -> Set[int]:
    """МСК-часы вокруг открытия NYSE; летом 16–18, зимой 17–19 (авто DST)."""
    raw = read_ny_open_block_settings(cfg)
    if not raw:
        return set()
    if ZoneInfo is None:
        return set()
    tz_offset = read_timezone_offset(dict(cfg))
    msk_zone = timezone(timedelta(hours=int(tz_offset)))
    market_tz = str(raw.get("market_tz", "America/New_York") or "America/New_York")
    market_open = str(raw.get("market_open_local", "09:30") or "09:30")
    stop_before = max(0, int(raw.get("stop_before_open_minutes", 30) or 30))
    block_hours = max(1, min(6, int(raw.get("block_hours", 3) or 3)))
    when = when or datetime.now(msk_zone)
    ny_zone = ZoneInfo(market_tz)
    ny_now = when.astimezone(ny_zone)
    open_h, open_m = _parse_hhmm(market_open)
    ny_open = ny_now.replace(hour=open_h, minute=open_m, second=0, microsecond=0)
    ny_stop = ny_open - timedelta(minutes=stop_before)
    out: Set[int] = set()
    for i in range(block_hours):
        msk_hour = (ny_stop + timedelta(hours=i)).astimezone(msk_zone).hour % 24
        out.add(msk_hour)
    return out


def static_blocked_local_hours(cfg: Mapping[str, Any]) -> Set[int]:
    """Часы из yaml без динамического блока NY."""
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    hours = _parse_hour_list(trading.get("block_entry_utc_hours") or [])
    sup = cfg.get("supervisor_v4") if isinstance(cfg.get("supervisor_v4"), dict) else {}
    hours |= _parse_hour_list(sup.get("seed_blocked_utc_hours") or [])
    return hours


def effective_blocked_local_hours(
    cfg: Mapping[str, Any],
    *,
    when: Optional[datetime] = None,
) -> Set[int]:
    """Статические часы + авто-блок открытия NY (DST)."""
    hours = static_blocked_local_hours(cfg)
    hours |= ny_open_block_hours_msk(cfg, when=when)
    return hours


def read_blocked_local_hours(
    cfg: Mapping[str, Any],
    *,
    when: Optional[datetime] = None,
) -> Set[int]:
    return effective_blocked_local_hours(cfg, when=when)


def merge_consecutive_hours(hours: Iterable[int]) -> List[Tuple[int, int]]:
    sorted_hours = sorted({int(h) % 24 for h in hours})
    if not sorted_hours:
        return []
    ranges: List[Tuple[int, int]] = []
    start = prev = sorted_hours[0]
    for hour in sorted_hours[1:]:
        if hour == prev + 1:
            prev = hour
        else:
            ranges.append((start, prev))
            start = prev = hour
    ranges.append((start, prev))
    return ranges


def windows_from_blocked_hours(
    blocked: Set[int],
    *,
    resume_before_minutes: int = 5,
) -> List[TradingWindow]:
    """stop в начале блока; start за N минут до конца блока (начала следующего часа)."""
    if not blocked:
        return []
    resume_before_minutes = max(1, min(30, int(resume_before_minutes)))
    resume_minute = 60 - resume_before_minutes
    out: List[TradingWindow] = []
    for start_h, end_h in merge_consecutive_hours(blocked):
        stop_at = f"{start_h:02d}:00"
        resume_at = f"{end_h:02d}:{resume_minute:02d}"
        out.append(
            TradingWindow(
                stop_at=stop_at,
                resume_at=resume_at,
                start_hour=start_h,
                end_hour=end_h,
            )
        )
    return out


def read_trading_windows(cfg: Mapping[str, Any]) -> List[TradingWindow]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    sched = trading.get("non_trading_systemd") if isinstance(trading.get("non_trading_systemd"), dict) else {}
    if not bool(sched.get("enabled", True)):
        return []
    resume_before = int(sched.get("resume_before_minutes", 5) or 5)
    explicit = sched.get("windows")
    if isinstance(explicit, list) and explicit:
        windows: List[TradingWindow] = []
        for row in explicit:
            if not isinstance(row, dict):
                continue
            stop_at = str(row.get("stop_at", "") or "").strip()
            resume_at = str(row.get("resume_at", "") or "").strip()
            if not stop_at or not resume_at:
                continue
            sh, _ = _parse_hhmm(stop_at)
            eh, _ = _parse_hhmm(resume_at)
            windows.append(
                TradingWindow(stop_at=stop_at, resume_at=resume_at, start_hour=sh, end_hour=eh)
            )
        return windows
    blocked = effective_blocked_local_hours(cfg)
    return windows_from_blocked_hours(blocked, resume_before_minutes=resume_before)


def format_windows_md(windows: Sequence[TradingWindow], *, tz_offset: int) -> List[str]:
    if not windows:
        return ["_Нет неторговых окон._"]
    lines = [
        f"Часовой пояс: UTC{tz_offset:+d}",
        "",
        "| стоп | старт (−5 мин) | часы блока |",
        "|------|----------------|------------|",
    ]
    for w in windows:
        lines.append(
            f"| **{w.stop_at}** | **{w.resume_at}** | {w.start_hour:02d}:00–{w.end_hour:02d}:59 |"
        )
    return lines
