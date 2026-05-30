"""Фильтр сигналов ai4trade: только BTC / ETH."""
from __future__ import annotations

import re
from typing import Any, Iterable

DEFAULT_BASES = frozenset({"BTC", "ETH", "XBT"})

_RE_BTC = re.compile(r"\b(BTC|BITCOIN|XBT)\b", re.I)
_RE_ETH = re.compile(r"\b(ETH|ETHEREUM)\b", re.I)


def _normalize_base(symbol: str) -> str:
    s = (symbol or "").upper().strip()
    for suffix in ("USDT", "USDC", "USD", "-PERP", "PERP"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s.strip("-_/ ")


def bases_from_signal(signal: dict[str, Any]) -> set[str]:
    """Извлечь базовые монеты из полей сигнала."""
    out: set[str] = set()
    sym = _normalize_base(str(signal.get("symbol") or ""))
    if sym in DEFAULT_BASES:
        out.add("ETH" if sym == "XBT" else sym)
    for item in signal.get("symbols") or []:
        base = _normalize_base(str(item))
        if base in DEFAULT_BASES:
            out.add("ETH" if base == "XBT" else base)
    text = " ".join(
        str(signal.get(k) or "")
        for k in ("title", "content", "latest_strategy_title", "message")
    )
    if _RE_BTC.search(text):
        out.add("BTC")
    if _RE_ETH.search(text):
        out.add("ETH")
    return out


def matches_btc_eth(
    signal: dict[str, Any],
    *,
    allowed_bases: Iterable[str] | None = None,
) -> bool:
    allowed = {b.upper() for b in (allowed_bases or DEFAULT_BASES)}
    allowed.discard("XBT")
    allowed.add("BTC")
    bases = bases_from_signal(signal)
    if not bases:
        return False
    return bool(bases & allowed)


def format_bases_label(bases: set[str]) -> str:
    order = [b for b in ("BTC", "ETH") if b in bases]
    return "/".join(order) if order else "?"
