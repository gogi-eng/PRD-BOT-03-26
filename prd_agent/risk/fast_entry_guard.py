"""Дневной стоп для охотника за скачками и для входа с каналов.

Обычный цикл уже сверяет закрытые сделки Bybit. Охотник за скачками
открывает сделки отдельно и раньше этот стоп не видел.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from prd_agent.analysis.trade_monitor import TradeMonitor
from prd_agent.risk.guard import RiskGuard

logger = logging.getLogger("prd_agent.risk.fast_entry")


async def fast_entry_block_reason(
    bybit: Any,
    cfg: Dict[str, Any],
    symbol: str,
    *,
    store_dir: Path,
) -> str:
    """Пустая строка — вход разрешён. Иначе текст, почему нельзя.

    Считает закрытые сделки биржи с начала торгового дня — тем же окном,
    что и дневной PnL, а не только текущую сессию.
    """
    if bybit is None:
        return "Дневной стоп: нет связи с биржей, вход пропущен"
    try:
        guard = RiskGuard(cfg if isinstance(cfg, dict) else {})
        monitor = TradeMonitor(store_dir)
        rows = await monitor.fetch_closed_pnl(bybit, hours=30)
        balance = 0.0
        getter = getattr(bybit, "get_balance", None)
        if getter is not None:
            balance = float(await getter() or 0)
        if balance > 0:
            guard.update_balance_reference(balance)
        guard.reconcile_from_closed_rows(rows, balance=balance)
        ok, reason = guard.fast_path_block(symbol)
        return "" if ok else str(reason or "Дневной стоп")
    except Exception as exc:
        logger.error(
            "Day stop check failed for %s: %s",
            symbol,
            exc,
            exc_info=True,
        )
        return "Дневной стоп: не удалось сверить убыток дня, вход пропущен"
