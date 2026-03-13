#!/usr/bin/env python3
"""
POSITION MANAGER — трекинг открытых позиций.
"""
from __future__ import annotations
from typing import Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Position:
    """Открытая позиция."""
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    qty: float
    stop_loss: float
    take_profit: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    best_price: float = 0.0
    trailing_active: bool = False
    trailing_stop: float = 0.0
    trailing_distance: float = 0.0
    trailing_activation_price: float = 0.0
    bars_since_entry: int = 0
    unrealized_pnl: float = 0.0
    capital_weight: float = 1.0
    heatmap_target: float = 0.0
    protective_liq_level: float = 0.0
    model_confidence: float = 0.0
    last_rl_action: str = "hold"
    add_count: int = 0
    origin: str = "bot"
    partial_tp_price: float = 0.0
    partial_tp_done: bool = False
    partial_close_fraction: float = 0.5
    total_tp_price: float = 0.0
    position_idx: int = 0
    external_tp_locked: bool = False
    last_notified_stop_loss: float = 0.0

    @property
    def is_long(self) -> bool:
        return self.side.upper() in ["BUY", "LONG"]


class PositionManager:
    """Управляет открытыми позициями."""

    def __init__(self):
        self.positions: Dict[str, Position] = {}

    def add(self, pos: Position):
        self.positions[pos.symbol] = pos
        print(f"[POS] Added: {pos.side} {pos.symbol} entry=${pos.entry_price:.4f}")

    def remove(self, symbol: str) -> Optional[Position]:
        pos = self.positions.pop(symbol, None)
        if pos:
            print(f"[POS] Removed: {symbol}")
        return pos

    def get(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def increase(self, symbol: str, qty: float, price: float):
        pos = self.positions.get(symbol)
        if not pos or qty <= 0 or price <= 0:
            return
        total_cost = pos.entry_price * pos.qty + price * qty
        pos.qty += qty
        pos.entry_price = total_cost / pos.qty
        pos.add_count += 1

    def reduce(self, symbol: str, qty: float):
        pos = self.positions.get(symbol)
        if not pos or qty <= 0:
            return
        pos.qty = max(0.0, pos.qty - qty)
        if pos.qty <= 0:
            self.remove(symbol)

    def has(self, symbol: str) -> bool:
        return symbol in self.positions

    def count(self) -> int:
        return len(self.positions)

    def symbols(self) -> list:
        return list(self.positions.keys())

    def all_positions(self) -> Dict[str, Position]:
        return self.positions.copy()

    def to_controls_dict(self) -> dict:
        """Для LiveControls.set_positions()."""
        result = {}
        for sym, pos in self.positions.items():
            result[sym] = {
                "side": pos.side,
                "qty": pos.qty,
                "entry": pos.entry_price,
                "unrealized_pnl": pos.unrealized_pnl,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "heatmap_target": pos.heatmap_target,
                "rl_action": pos.last_rl_action,
                "origin": pos.origin,
                "partial_tp_price": pos.partial_tp_price,
                "partial_tp_done": pos.partial_tp_done,
                "external_tp_locked": pos.external_tp_locked,
            }
        return result
