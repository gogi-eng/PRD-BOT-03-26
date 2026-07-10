"""Расписание stop/start ботов по block_entry_utc_hours (местное время)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Sequence, Set, Tuple


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


def read_blocked_local_hours(cfg: Mapping[str, Any]) -> Set[int]:
    trading = cfg.get("trading") if isinstance(cfg.get("trading"), dict) else {}
    raw = trading.get("block_entry_utc_hours") or []
    hours: Set[int] = set()
    if isinstance(raw, list):
        for item in raw:
            try:
                hours.add(int(item) % 24)
            except (TypeError, ValueError):
                continue
    sup = cfg.get("supervisor_v4") if isinstance(cfg.get("supervisor_v4"), dict) else {}
    seed = sup.get("seed_blocked_utc_hours") or []
    if isinstance(seed, list):
        for item in seed:
            try:
                hours.add(int(item) % 24)
            except (TypeError, ValueError):
                continue
    return hours


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
    blocked = read_blocked_local_hours(cfg)
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
