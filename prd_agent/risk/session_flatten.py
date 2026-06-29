"""Закрытие всех позиций бота перед риск-окном (местные часы UTC+offset)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from prd_agent.time_hours import read_timezone_offset

logger = logging.getLogger("prd_agent.session_flatten")


@dataclass
class SessionFlattenConfig:
    enabled: bool = False
    local_hours: List[int] = field(default_factory=list)
    lead_minutes: int = 30
    skip_manual: bool = True
    telegram_notify: bool = True

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> "SessionFlattenConfig":
        block = cfg.get("session_flatten")
        if not isinstance(block, dict):
            block = {}
        hours_raw = block.get("local_hours")
        if hours_raw is None:
            hours_raw = block.get("utc_hours") or []
        hours = sorted({int(h) % 24 for h in (hours_raw or [])})
        return cls(
            enabled=bool(block.get("enabled", False)),
            local_hours=hours,
            lead_minutes=max(1, int(block.get("lead_minutes", 30) or 30)),
            skip_manual=bool(block.get("skip_manual", True)),
            telegram_notify=bool(block.get("telegram_notify", True)),
        )


class SessionFlattenGuard:
    """Срабатывает за lead_minutes до каждого local_hour из config (местное время)."""

    def __init__(self, cfg: Dict[str, Any]):
        self.apply_config(cfg)

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = SessionFlattenConfig.from_cfg(cfg)
        self._tz_offset = read_timezone_offset(cfg)

    def _local_now(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(hours=self._tz_offset)

    def due_trigger(self, *, _last_keys: Optional[Set[str]] = None) -> Optional[Tuple[int, str]]:
        """Возвращает (целевой_час, ключ_дня) если пора flatten, иначе None."""
        if not self.cfg.enabled or not self.cfg.local_hours:
            return None
        last = _last_keys if _last_keys is not None else set()
        local_now = self._local_now()
        lead = max(1, int(self.cfg.lead_minutes))
        for hour in self.cfg.local_hours:
            target = local_now.replace(hour=int(hour), minute=0, second=0, microsecond=0)
            if target <= local_now:
                target = target + timedelta(days=1)
            minutes_to_target = (target - local_now).total_seconds() / 60.0
            if 0 < minutes_to_target <= lead:
                key = f"{target.date().isoformat()}-{int(hour):02d}"
                if key in last:
                    continue
                return int(hour), key
        return None

    def format_telegram(self, target_hour: int, closed: int, notes: List[str]) -> str:
        flat_at = self._local_now()
        lead = self.cfg.lead_minutes
        tz = self._tz_offset
        sign = f"+{tz}" if tz >= 0 else str(tz)
        lines = [
            "<b>SESSION FLATTEN</b>",
            "",
            f"Закрываю позиции бота за {lead} мин до <code>{target_hour:02d}:00</code> "
            f"(местное UTC{sign}).",
            f"Закрыто: <code>{closed}</code>",
        ]
        if notes:
            lines.append("")
            lines.extend(notes[:8])
        return "\n".join(lines)
