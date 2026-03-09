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
            }
        return result
