"""Shared bot runtime state dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BasketProfitState:
    peak_profit_usdt: float = 0.0
    armed: bool = False
    last_reason: str = ""
    total_history: dict = None
    symbol_pnl_history: dict = None
    drawdown_detected_at: float = 0.0
    # Фиксировано при старте таймера (сек) — граница fast vs base-часа не сокращает/не растягивает уже запущенное ожидание.
    drawdown_confirm_lock_sec: float = 0.0

    def __post_init__(self):
        if self.total_history is None:
            self.total_history = []
        if self.symbol_pnl_history is None:
            self.symbol_pnl_history = {}
