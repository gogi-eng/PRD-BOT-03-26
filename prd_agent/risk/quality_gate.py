"""
Quality gate v2: фильтр перед отправкой ордера на Bybit.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.signals.types import UnifiedSignal

logger = logging.getLogger("prd_agent.quality")


class QualityGate:
    def __init__(self, cfg: Dict[str, Any]):
        self._cfg = cfg
        q = cfg.get("quality_gate", {})
        t = cfg.get("trading", {})
        ta = cfg.get("ta_scanner", {}) if isinstance(cfg.get("ta_scanner"), dict) else {}
        self.enabled = bool(q.get("enabled", True))
        self.min_confidence = float(
            q.get("min_confidence", t.get("min_signal_confidence", 0.68))
        )
        # По умолчанию 2.0 — как ta_scanner.min_rr и own_multi_agent (atr_tp/atr_sl ≈ 2.08)
        self.min_rr = float(q.get("min_rr_ratio", ta.get("min_rr", 2.0)))
        self.require_sl_tp = bool(q.get("require_sl_tp", True))
        self.min_volume = float(
            q.get("min_24h_volume_usdt", t.get("min_24h_volume_usdt", 10_000_000))
        )
        subs = list(q.get("block_meme_symbol_substrings", [])) or list(
            t.get("symbol_blacklist_substrings", [])
        )
        self.meme_subs = tuple(str(x).upper() for x in subs if x)
        self._volume_cache: Dict[str, float] = {}

    async def _symbol_volume(self, exchange, symbol: str) -> float:
        sym = symbol.upper()
        if sym in self._volume_cache:
            return self._volume_cache[sym]
        vol = 0.0
        if hasattr(exchange, "get_tickers"):
            try:
                for t in await exchange.get_tickers():
                    if str(t.get("symbol", "")).upper() == sym:
                        vol = float(t.get("turnover24h", 0) or 0)
                        break
            except Exception as exc:
                logger.warning("quality_gate volume lookup %s: %s", sym, exc)
        self._volume_cache[sym] = vol
        return vol

    @staticmethod
    def _rr_ratio(entry: float, sl: float, tp: float, side: str) -> float:
        if entry <= 0 or sl <= 0 or tp <= 0:
            return 0.0
        is_buy = side.lower() == "buy"
        risk = abs(entry - sl)
        reward = abs(tp - entry) if is_buy else abs(entry - tp)
        if risk <= 0:
            return 0.0
        return reward / risk

    def _min_rr_for_signal(self, sig: UnifiedSignal) -> float:
        """Не ослаблять RR ниже quality_gate — только ужесточать для TA/own."""
        base = self.min_rr
        src = (sig.source or "").lower()
        if src in ("own_multi_agent", "ta_volatility"):
            ta = self._cfg.get("ta_scanner", {})
            if isinstance(ta, dict) and ta.get("min_rr") is not None:
                return max(base, float(ta["min_rr"]))
        return base

    async def check(
        self,
        sig: UnifiedSignal,
        exchange,
        *,
        entry: float,
        sl: float,
        tp: float,
    ) -> Tuple[bool, str]:
        if not self.enabled:
            return True, ""
        sym = sig.symbol.upper()
        if any(part in sym for part in self.meme_subs):
            return False, f"quality_gate: символ {sym} в blacklist (мем/низколиквид)"
        if float(sig.confidence) < self.min_confidence:
            return False, (
                f"quality_gate: confidence {sig.confidence:.2f} < {self.min_confidence:.2f}"
            )
        if self.require_sl_tp and (sl <= 0 or tp <= 0):
            return False, "quality_gate: нет SL/TP"
        side_u = sig.side.lower()
        if entry > 0 and sl > 0 and tp > 0:
            if side_u == "buy":
                if sl >= entry or tp <= entry:
                    return False, "quality_gate: SL/TP не согласованы с LONG"
            elif side_u == "sell":
                if sl <= entry or tp >= entry:
                    return False, "quality_gate: SL/TP не согласованы с SHORT"
            sl_pct = abs(entry - sl) / entry
            if sl_pct > 0.5:
                return False, f"quality_gate: SL далеко от entry ({sl_pct:.0%})"
        rr = self._rr_ratio(entry, sl, tp, sig.side)
        min_rr = self._min_rr_for_signal(sig)
        if rr < min_rr:
            return False, f"quality_gate: RR {rr:.2f} < {min_rr:.2f}"
        vol = await self._symbol_volume(exchange, sym)
        if self.min_volume > 0 and vol > 0 and vol < self.min_volume:
            return False, (
                f"quality_gate: объём 24h {vol:,.0f} < {self.min_volume:,.0f} USDT"
            )
        return True, ""
