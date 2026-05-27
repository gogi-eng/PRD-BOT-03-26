"""Расчёт PnL% и разбор позиции Bybit."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def position_size(p: Dict[str, Any]) -> float:
    for key in ("size", "qty", "positionQty"):
        val = float(p.get(key, 0) or 0)
        if val > 0:
            return val
    avg = float(p.get("avgPrice") or p.get("entryPrice") or 0)
    pval = float(p.get("positionValue", 0) or 0)
    if avg > 0 and pval > 0:
        return pval / avg
    return 0.0


def normalize_side(raw: str) -> str:
    s = str(raw or "").lower()
    return "Buy" if s in ("buy", "long") else "Sell"


def unrealized_profit_pct(side: str, entry: float, mark: float) -> float:
    if entry <= 0 or mark <= 0:
        return 0.0
    if normalize_side(side) == "Buy":
        return (mark - entry) / entry * 100.0
    return (entry - mark) / entry * 100.0


def parse_position(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    sym = str(row.get("symbol", "")).upper()
    size = position_size(row)
    if not sym or size <= 0:
        return None
    entry = float(row.get("avgPrice") or row.get("entryPrice") or 0)
    if entry <= 0:
        return None
    mark = float(row.get("markPrice") or entry)
    side = normalize_side(row.get("side", ""))
    return {
        "symbol": sym,
        "side": side,
        "size": size,
        "entry": entry,
        "mark": mark,
        "stop_loss": float(row.get("stopLoss") or 0),
        "take_profit": float(row.get("takeProfit") or 0),
        "leverage": int(float(row.get("leverage") or 1)),
        "position_idx": int(row.get("positionIdx", 0) or 0),
        "profit_pct": unrealized_profit_pct(side, entry, mark),
    }


def position_key(symbol: str, side: str) -> str:
    return f"{symbol.upper()}:{normalize_side(side)}"
