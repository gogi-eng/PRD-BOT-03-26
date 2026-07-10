"""Закрытие всех позиций в заданные местные часы (стыки сессий)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from prd_agent.time_hours import read_timezone_offset


@dataclass(frozen=True)
class SessionBoundaryCloseConfig:
    enabled: bool = False
    timezone_offset: int = 3
    close_at_local: Tuple[str, ...] = ()
    window_minutes: int = 3

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> SessionBoundaryCloseConfig:
        positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
        raw = positions.get("session_boundary_close")
        if not isinstance(raw, dict):
            raw = {}
        slots_raw = raw.get("close_at_local") or raw.get("close_at") or []
        slots: List[str] = []
        if isinstance(slots_raw, list):
            for item in slots_raw:
                text = str(item or "").strip()
                if text:
                    slots.append(text)
        tz = raw.get("timezone_offset")
        if tz is None:
            tz = read_timezone_offset(dict(cfg))
        return cls(
            enabled=bool(raw.get("enabled", False)),
            timezone_offset=int(tz or 0),
            close_at_local=tuple(slots),
            window_minutes=max(1, int(raw.get("window_minutes", 3) or 3)),
        )


def _local_now(tz_offset: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=int(tz_offset))


def _parse_hhmm(value: str) -> Optional[Tuple[int, int]]:
    text = str(value or "").strip()
    if not text or ":" not in text:
        return None
    parts = text.split(":", 1)
    try:
        hour = int(parts[0]) % 24
        minute = int(parts[1]) % 60
    except (TypeError, ValueError):
        return None
    return hour, minute


def active_session_close_slot(
    cfg: SessionBoundaryCloseConfig,
    *,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Возвращает метку слота (например 06:30), если сейчас окно закрытия."""
    if not cfg.enabled or not cfg.close_at_local:
        return None
    now = now or _local_now(cfg.timezone_offset)
    now_min = now.hour * 60 + now.minute
    for slot in cfg.close_at_local:
        parsed = _parse_hhmm(slot)
        if parsed is None:
            continue
        h, m = parsed
        start = h * 60 + m
        end = start + cfg.window_minutes
        if start <= now_min < end:
            return f"{h:02d}:{m:02d}"
    return None


def session_flush_key(local_date: str, slot: str) -> str:
    return f"{local_date}:{slot}"


def should_run_session_flush(
    cfg: SessionBoundaryCloseConfig,
    done_keys: Sequence[str],
    *,
    now: Optional[datetime] = None,
) -> Tuple[bool, str]:
    now = now or _local_now(cfg.timezone_offset)
    slot = active_session_close_slot(cfg, now=now)
    if not slot:
        return False, ""
    key = session_flush_key(now.strftime("%Y-%m-%d"), slot)
    if key in done_keys:
        return False, ""
    return True, slot
