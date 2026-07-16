"""Проверка открытых позиций на бирже перед новым сигналом."""
from __future__ import annotations

from typing import Any, Mapping, Sequence, Set


def position_size(row: Mapping[str, Any]) -> float:
    try:
        return float(row.get("size", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def symbols_with_open_positions(positions: Sequence[Mapping[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for row in positions:
        sym = str(row.get("symbol", "")).upper()
        if sym and position_size(row) > 0:
            out.add(sym)
    return out


def has_open_position_for_symbol(
    positions: Sequence[Mapping[str, Any]],
    symbol: str,
) -> bool:
    sym = str(symbol or "").upper()
    if not sym:
        return False
    for row in positions:
        if str(row.get("symbol", "")).upper() != sym:
            continue
        if position_size(row) > 0:
            return True
    return False


def open_position_skip_reason(symbol: str) -> str:
    sym = str(symbol or "").upper()
    return f"на бирже уже открыта позиция {sym} — новый сигнал отклонён"
