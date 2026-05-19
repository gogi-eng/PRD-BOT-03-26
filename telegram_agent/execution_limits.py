"""Rate limits for auto-execution (daily cap + cooldown), persisted in agent state JSON."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class ExecutionLimitsConfig:
    enabled: bool = True
    # 0 = unlimited
    max_auto_executes_per_day: int = 0
    cooldown_sec_after_execute: float = 0.0


class ExecutionLimiter:
    """Stores counters under state['telegram_agent_execution_limits']."""

    KEY = "telegram_agent_execution_limits"

    def __init__(self, state: dict[str, Any], cfg: ExecutionLimitsConfig):
        self.state = state
        self.cfg = cfg

    @classmethod
    def from_agent_cfg(cls, state: dict[str, Any], node: dict[str, Any] | None) -> ExecutionLimiter:
        raw = node if isinstance(node, dict) else {}
        cfg = ExecutionLimitsConfig(
            enabled=bool(raw.get("enabled", True)),
            max_auto_executes_per_day=int(raw.get("max_auto_executes_per_day", 0) or 0),
            cooldown_sec_after_execute=float(raw.get("cooldown_sec_after_execute", 0.0) or 0.0),
        )
        return cls(state, cfg)

    def _node(self) -> dict[str, Any]:
        n = self.state.setdefault(self.KEY, {})
        if not isinstance(n, dict):
            n = {}
            self.state[self.KEY] = n
        return n

    @staticmethod
    def _utc_day(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")

    def can_execute(self, now: datetime | None = None) -> tuple[bool, str]:
        if not self.cfg.enabled:
            return True, ""
        now = now or datetime.now(timezone.utc)
        node = self._node()
        day = self._utc_day(now)
        if node.get("day_utc") != day:
            node["day_utc"] = day
            node["executes"] = 0
        cap = int(self.cfg.max_auto_executes_per_day or 0)
        if cap > 0:
            cnt = int(node.get("executes", 0) or 0)
            if cnt >= cap:
                return False, f"дневной лимит автоисполнений {cap} исчерпан"
        cool = float(self.cfg.cooldown_sec_after_execute or 0.0)
        if cool > 0:
            raw_last = str(node.get("last_execute_at", "") or "")
            if raw_last:
                try:
                    last = datetime.fromisoformat(raw_left_fix(raw_last))
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    elapsed = (now - last.astimezone(timezone.utc)).total_seconds()
                    if elapsed < cool:
                        left = cool - elapsed
                        return False, f"кулдаун {cool:.0f}s, осталось {left:.0f}s"
                except Exception:
                    pass
        return True, ""

    def record_successful_execute(self, now: datetime | None = None) -> None:
        if not self.cfg.enabled:
            return
        now = now or datetime.now(timezone.utc)
        node = self._node()
        day = self._utc_day(now)
        if node.get("day_utc") != day:
            node["day_utc"] = day
            node["executes"] = 0
        node["executes"] = int(node.get("executes", 0) or 0) + 1
        node["last_execute_at"] = now.astimezone(timezone.utc).isoformat()


def raw_left_fix(raw: str) -> str:
    """Accept timestamps with 'Z' suffix."""
    s = raw.strip()
    if s.endswith("Z"):
        return s[:-1] + "+00:00"
    return s
