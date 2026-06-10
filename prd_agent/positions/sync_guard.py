"""Алерты при рассинхроне позиций бота и биржи (батч, без спама)."""
from __future__ import annotations

import time
from typing import Dict, List, Set


class PositionSyncGuard:
    def __init__(
        self,
        *,
        cooldown_sec: float = 3600.0,
        enabled: bool = True,
        max_alerts_per_cycle: int = 1,
        max_symbols_in_message: int = 8,
    ):
        self.enabled = bool(enabled)
        self.cooldown_sec = max(300.0, float(cooldown_sec))
        self.max_alerts_per_cycle = max(1, int(max_alerts_per_cycle))
        self.max_symbols_in_message = max(3, int(max_symbols_in_message))
        self._last_alert_at: Dict[str, float] = {}

    def _due(self, key: str, now: float) -> bool:
        last = self._last_alert_at.get(key, 0.0)
        if now - last < self.cooldown_sec:
            return False
        self._last_alert_at[key] = now
        return True

    @staticmethod
    def _format_symbol_list(symbols: List[str], limit: int) -> str:
        if len(symbols) <= limit:
            return ", ".join(symbols)
        shown = symbols[:limit]
        rest = len(symbols) - limit
        return f"{', '.join(shown)} (+ещё {rest})"

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

        registry_missing = sorted(
            sym for sym in bot_symbols if sym and sym not in live_symbols
        )
        if registry_missing and self._due("registry_batch", now):
            alerts.append(
                "⚠️ Рассинхрон позиций: в журнале бота, на Bybit нет — "
                f"{self._format_symbol_list(registry_missing, self.max_symbols_in_message)}. "
                "Проверьте биржу; устаревшие записи реестра очищаются автоматически."
            )

        tracked_gone = []
        for sym, pos in tracked.items():
            if sym in live_symbols:
                continue
            origin = str(getattr(pos, "origin", "manual") or "manual")
            if origin != "bot" and sym not in bot_symbols:
                continue
            tracked_gone.append(sym)
        if tracked_gone and self._due("tracked_batch", now):
            alerts.append(
                "⚠️ Рассинхрон: бот снимал сопровождение, на бирже позиции уже нет — "
                f"{self._format_symbol_list(sorted(tracked_gone), self.max_symbols_in_message)}."
            )

        return alerts[: self.max_alerts_per_cycle]
