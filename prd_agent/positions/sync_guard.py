"""Алерты при рассинхроне позиций бота и биржи."""
from __future__ import annotations

import time
from typing import Dict, List, Set


class PositionSyncGuard:
    def __init__(self, *, cooldown_sec: float = 600.0, enabled: bool = True):
        self.enabled = bool(enabled)
        self.cooldown_sec = max(60.0, float(cooldown_sec))
        self._last_alert_at: Dict[str, float] = {}

    def _due(self, key: str, now: float) -> bool:
        last = self._last_alert_at.get(key, 0.0)
        if now - last < self.cooldown_sec:
            return False
        self._last_alert_at[key] = now
        return True

    def check(
        self,
        *,
        bot_symbols: Set[str],
        live_symbols: Set[str],
        tracked: Dict[str, object],
    ) -> List[str]:
        if not self.enabled:
            return []
        now = time.time()
        alerts: List[str] = []

        for sym in sorted(bot_symbols):
            if sym in live_symbols:
                continue
            key = f"registry_missing:{sym}"
            if self._due(key, now):
                alerts.append(
                    f"⚠️ Рассинхрон: {sym} в журнале бота, на Bybit позиции нет. "
                    "Проверьте биржу вручную."
                )

        for sym, pos in tracked.items():
            if sym in live_symbols:
                continue
            origin = str(getattr(pos, "origin", "manual") or "manual")
            if origin != "bot" and sym not in bot_symbols:
                continue
            key = f"tracked_gone:{sym}"
            if self._due(key, now):
                alerts.append(
                    f"⚠️ Рассинхрон: {sym} сопровождалась ботом ({origin}), "
                    "на бирже закрыта — контроль снят, сделку не удаляем из памяти биржи."
                )
        return alerts
