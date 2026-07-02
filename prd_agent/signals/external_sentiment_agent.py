"""
Внешний сентимент для scanner/signals:
- Coinugget-style: RSI экстремумы + всплеск объёма по свечам Bybit
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from prd_agent.analysis.technical_indicators import klines_to_df, rsi
from prd_agent.signals.types import UnifiedSignal

logger = logging.getLogger("prd_agent.external_sentiment")


@dataclass
class _CacheEntry:
    ts: float
    signals: List[UnifiedSignal]


class ExternalSentimentAgent:
    def __init__(self, cfg: Dict[str, Any]):
        block = cfg.get("external_sentiment", {})
        if not isinstance(block, dict):
            block = {}
        self.enabled = bool(block.get("enabled", True))
        cg = block.get("coinugget_style", {}) if isinstance(block.get("coinugget_style"), dict) else {}
        self.cg_enabled = bool(cg.get("enabled", True))
        self.rsi_period = int(cg.get("rsi_period", 14))
        self.rsi_oversold = float(cg.get("rsi_oversold", 30))
        self.rsi_overbought = float(cg.get("rsi_overbought", 70))
        self.volume_spike_mult = float(cg.get("volume_spike_mult", 2.0))
        self.cg_min_conf = float(cg.get("min_confidence", 0.68))
        self.cg_max_symbols = int(cg.get("max_symbols", 20))
        self.cg_interval = str(cg.get("kline_interval", "15"))
        self.cg_klines_limit = int(cg.get("klines_limit", 60))
        self.cache_sec = float(block.get("cache_sec", 900))
        self._cache: Optional[_CacheEntry] = None

    async def _coinugget_style_signals(
        self, exchange, symbols: List[str]
    ) -> List[UnifiedSignal]:
        out: List[UnifiedSignal] = []
        scan = symbols[: self.cg_max_symbols]
        for sym in scan:
            klines = await exchange.get_klines(sym, interval=self.cg_interval, limit=self.cg_klines_limit)
            if not klines:
                continue
            df = klines_to_df(klines)
            if df.empty or "volume" not in df.columns:
                continue
            r = rsi(df["close"], period=self.rsi_period)
            vol = df["volume"].astype(float)
            last_vol = float(vol.iloc[-1])
            avg_vol = float(vol.tail(20).mean()) if len(vol) >= 5 else last_vol
            vol_spike = last_vol >= avg_vol * self.volume_spike_mult if avg_vol > 0 else False
            side = ""
            tag = ""
            if r <= self.rsi_oversold:
                side = "Buy"
                tag = f"RSI={r:.1f}≤{self.rsi_oversold:.0f}"
            elif r >= self.rsi_overbought:
                side = "Sell"
                tag = f"RSI={r:.1f}≥{self.rsi_overbought:.0f}"
            if not side:
                continue
            conf = self.cg_min_conf
            if vol_spike:
                conf = min(0.9, conf + 0.08)
            price = await exchange.get_price(sym)
            atr_pct = 0.005
            if side == "Buy":
                sl, tp = price * (1 - atr_pct), price * (1 + atr_pct * 2)
            else:
                sl, tp = price * (1 + atr_pct), price * (1 - atr_pct * 2)
            vol_note = " vol_spike" if vol_spike else ""
            out.append(
                UnifiedSignal(
                    symbol=sym,
                    side=side,
                    confidence=conf,
                    source="coinugget_style",
                    entry=price,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=f"Coinugget-style: {tag}{vol_note}",
                    raw={"rsi": r, "volume_spike": vol_spike},
                )
            )
        return out

    async def collect(self, exchange, symbols: List[str]) -> List[UnifiedSignal]:
        if not self.enabled:
            return []
        now = time.time()
        if self._cache and (now - self._cache.ts) < self.cache_sec:
            return list(self._cache.signals)

        out: List[UnifiedSignal] = []
        if self.cg_enabled:
            try:
                out.extend(await self._coinugget_style_signals(exchange, symbols))
            except Exception as exc:
                logger.warning("coinugget_style: %s", exc)

        self._cache = _CacheEntry(ts=now, signals=out)
        if out:
            logger.info(
                "External sentiment: coinugget_style=%d total=%d",
                sum(1 for s in out if s.source == "coinugget_style"),
                len(out),
            )
        return out
