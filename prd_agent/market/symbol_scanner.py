"""
Сканер USDT-linear пар Bybit по обороту за 24ч (turnover24h).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Sequence

logger = logging.getLogger("prd_agent.market")


class SymbolScanner:
    def __init__(self, cfg: Dict[str, Any]):
        t = cfg.get("trading", {})
        self.min_24h_volume_usdt = float(t.get("min_24h_volume_usdt", 0))
        self.max_scan_symbols = int(t.get("max_scan_symbols", 12))
        self.whitelist: List[str] = [str(s).upper() for s in t.get("symbols", []) if s]
        self.blacklist: set[str] = {str(s).upper() for s in t.get("symbol_blacklist", []) if s}
        subs = t.get("symbol_blacklist_substrings", ["1000", "USDC", "USDE"])
        self.blacklist_substrings: tuple[str, ...] = tuple(str(x) for x in subs if x)

    def enabled(self) -> bool:
        return self.min_24h_volume_usdt > 0

    async def scan(self, exchange) -> List[str]:
        """Топ пар по ликвидности; whitelist из config — в начале списка."""
        if not self.enabled():
            return list(self.whitelist)

        if not hasattr(exchange, "get_tickers"):
            logger.warning("get_tickers недоступен — используем symbols из config")
            return list(self.whitelist) or ["BTCUSDT", "ETHUSDT"]

        try:
            tickers = await exchange.get_tickers()
        except Exception as exc:
            logger.error("Symbol scan failed: %s", exc)
            return list(self.whitelist) or ["BTCUSDT", "ETHUSDT"]

        ranked: List[tuple[str, float, float]] = []
        for ticker in tickers:
            symbol = str(ticker.get("symbol", "")).upper()
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.blacklist:
                continue
            if any(part in symbol for part in self.blacklist_substrings):
                continue
            turnover = float(ticker.get("turnover24h", 0) or 0)
            if turnover < self.min_24h_volume_usdt:
                continue
            price_chg = abs(float(ticker.get("price24hPcnt", 0) or 0)) * 100
            score = turnover * (1 + price_chg / 10)
            ranked.append((symbol, turnover, score))

        ranked.sort(key=lambda x: x[2], reverse=True)
        picked = [s for s, _, _ in ranked[: self.max_scan_symbols]]

        for wl in reversed([s for s in self.whitelist if s not in self.blacklist]):
            if wl in picked:
                picked.remove(wl)
            picked.insert(0, wl)

        out: List[str] = []
        seen: set[str] = set()
        for s in picked:
            if s not in seen:
                out.append(s)
                seen.add(s)

        logger.info(
            "Symbol scan: min_vol=%.0f → %d eligible, watch=%d (%s)",
            self.min_24h_volume_usdt,
            len(ranked),
            len(out),
            ", ".join(out[:8]) + ("…" if len(out) > 8 else ""),
        )
        return out or list(self.whitelist) or ["BTCUSDT", "ETHUSDT"]
