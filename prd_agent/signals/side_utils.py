"""Нормализация стороны сделки (сигнал / позиция Bybit)."""
from __future__ import annotations


def normalize_trade_side(side: str) -> str:
    s = str(side or "").strip()
    if not s:
        return ""
    cap = s.capitalize()
    if cap == "Buy":
        return "BUY"
    if cap == "Sell":
        return "SELL"
    u = s.upper()
    if u in ("BUY", "LONG", "B"):
        return "BUY"
    if u in ("SELL", "SHORT", "S"):
        return "SELL"
    return u


def trade_sides_opposite(side_a: str, side_b: str) -> bool:
    a = normalize_trade_side(side_a)
    b = normalize_trade_side(side_b)
    return bool(a and b and a != b)
