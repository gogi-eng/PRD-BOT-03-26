"""
Сканер волатильных USDT-пар и теханализ для сигналов на Bybit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from prd_agent.analysis.technical_indicators import (
    atr,
    ema,
    intraday_range_pct,
    klines_to_df,
    rsi,
)

logger = logging.getLogger("prd_agent.ta")


@dataclass
class VolatileSymbol:
    symbol: str
    change_24h_pct: float
    turnover_24h: float
    last_price: float


@dataclass
class TASignalResult:
    symbol: str
    side: str  # Buy | Sell
    confidence: float
    entry: float
    stop_loss: float
    take_profit: float
    reason: str
    change_24h_pct: float = 0.0
    indicators: Dict[str, Any] = field(default_factory=dict)


class VolatilityTAEngine:
    def __init__(self, cfg: Dict[str, Any]):
        t = cfg.get("trading", {})
        s = cfg.get("ta_scanner", {})
        self.enabled = bool(s.get("enabled", True))
        self.min_24h_change_pct = float(s.get("min_24h_change_pct", 1.0))
        self.min_intraday_range_pct = float(s.get("min_intraday_range_pct", 0.8))
        self.min_turnover_usdt = float(
            s.get("min_24h_volume_usdt", t.get("min_24h_volume_usdt", 5_000_000))
        )
        self.max_symbols = int(s.get("max_symbols", 15))
        self.interval = str(s.get("interval", "15"))
        self.kline_limit = int(s.get("kline_limit", 120))
        self.min_confidence = float(s.get("min_confidence", t.get("min_own_agent_confidence", 0.55)))
        self.atr_sl_mult = float(s.get("atr_sl_mult", 1.2))
        self.atr_tp_mult = float(s.get("atr_tp_mult", 2.5))
        self.min_rr = float(s.get("min_rr", 2.0))
        self.rsi_buy_min = float(s.get("rsi_buy_min", 38))
        self.rsi_buy_max = float(s.get("rsi_buy_max", 68))
        self.rsi_sell_min = float(s.get("rsi_sell_min", 32))
        self.rsi_sell_max = float(s.get("rsi_sell_max", 62))
        subs = list(t.get("symbol_blacklist_substrings", [])) + list(
            s.get("extra_blacklist_substrings", [])
        )
        self.blacklist_substrings = tuple(str(x) for x in subs if x)
        self.blacklist = {str(x).upper() for x in t.get("symbol_blacklist", []) if x}

    def _symbol_ok(self, symbol: str) -> bool:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            return False
        if sym in self.blacklist:
            return False
        return not any(part in sym for part in self.blacklist_substrings)

    async def scan_volatile(self, exchange) -> List[VolatileSymbol]:
        if not hasattr(exchange, "get_tickers"):
            return []
        try:
            tickers = await exchange.get_tickers()
        except Exception as exc:
            logger.error("TA scan tickers: %s", exc)
            return []
        ranked: List[VolatileSymbol] = []
        for row in tickers:
            sym = str(row.get("symbol", "")).upper()
            if not self._symbol_ok(sym):
                continue
            turnover = float(row.get("turnover24h", 0) or 0)
            if turnover < self.min_turnover_usdt:
                continue
            chg_pct = abs(float(row.get("price24hPcnt", 0) or 0)) * 100.0
            if chg_pct < self.min_24h_change_pct:
                continue
            price = float(row.get("lastPrice", 0) or row.get("markPrice", 0) or 0)
            ranked.append(
                VolatileSymbol(
                    symbol=sym,
                    change_24h_pct=chg_pct,
                    turnover_24h=turnover,
                    last_price=price,
                )
            )
        ranked.sort(key=lambda x: (x.change_24h_pct, x.turnover_24h), reverse=True)
        return ranked[: self.max_symbols]

    def analyze_df(
        self, symbol: str, df: pd.DataFrame, *, change_24h_pct: float = 0.0
    ) -> Optional[TASignalResult]:
        if df.empty or len(df) < 50:
            return None
        range_pct = intraday_range_pct(df, 12)
        if range_pct < self.min_intraday_range_pct:
            return None

        close = df["close"]
        price = float(close.iloc[-1])
        if price <= 0:
            return None

        e9 = float(ema(close, 9).iloc[-1])
        e21 = float(ema(close, 21).iloc[-1])
        e50 = float(ema(close, 50).iloc[-1])
        rsi_v = rsi(close, 14)
        atr_v = atr(df, 14)
        if atr_v <= 0:
            return None

        bullish = e9 > e21 > e50 and price > e21
        bearish = e9 < e21 < e50 and price < e21
        momentum = (e9 - e21) / price * 100.0

        side: Optional[str] = None
        score = 0.0
        parts: List[str] = []

        if bullish and self.rsi_buy_min <= rsi_v <= self.rsi_buy_max:
            side = "Buy"
            score = 0.55
            parts.append(f"тренд↑ EMA9>21>50 RSI={rsi_v:.0f}")
            if momentum > 0.05:
                score += 0.08
                parts.append(f"mom={momentum:.2f}%")
            if rsi_v > 45:
                score += 0.05
        elif bearish and self.rsi_sell_min <= rsi_v <= self.rsi_sell_max:
            side = "Sell"
            score = 0.55
            parts.append(f"тренд↓ EMA9<21<50 RSI={rsi_v:.0f}")
            if momentum < -0.05:
                score += 0.08
                parts.append(f"mom={momentum:.2f}%")
            if rsi_v < 55:
                score += 0.05

        if not side:
            return None

        if change_24h_pct >= self.min_24h_change_pct * 1.5:
            score += 0.06
            parts.append(f"vol24h={change_24h_pct:.1f}%")
        if range_pct >= self.min_intraday_range_pct * 1.5:
            score += 0.05
            parts.append(f"range12={range_pct:.1f}%")

        score = min(0.92, score)
        if score < self.min_confidence:
            return None

        if side == "Buy":
            sl = price - self.atr_sl_mult * atr_v
            tp = price + self.atr_tp_mult * atr_v
        else:
            sl = price + self.atr_sl_mult * atr_v
            tp = price - self.atr_tp_mult * atr_v

        risk = abs(price - sl)
        reward = abs(tp - price)
        if risk <= 0 or reward / risk < self.min_rr:
            return None

        return TASignalResult(
            symbol=symbol.upper(),
            side=side,
            confidence=score,
            entry=price,
            stop_loss=sl,
            take_profit=tp,
            reason="; ".join(parts),
            change_24h_pct=change_24h_pct,
            indicators={
                "rsi": round(rsi_v, 1),
                "atr": round(atr_v, 6),
                "ema9": round(e9, 6),
                "ema21": round(e21, 6),
                "ema50": round(e50, 6),
                "range_12_pct": round(range_pct, 2),
                "rr": round(reward / risk, 2),
            },
        )

    async def analyze_symbol(
        self, exchange, vol: VolatileSymbol
    ) -> Optional[TASignalResult]:
        klines = await exchange.get_klines(
            vol.symbol, interval=self.interval, limit=self.kline_limit
        )
        df = klines_to_df(klines)
        return self.analyze_df(vol.symbol, df, change_24h_pct=vol.change_24h_pct)

    async def collect_signals(self, exchange) -> Tuple[List[TASignalResult], List[VolatileSymbol]]:
        if not self.enabled:
            return [], []
        volatile = await self.scan_volatile(exchange)
        signals: List[TASignalResult] = []
        for vol in volatile:
            try:
                sig = await self.analyze_symbol(exchange, vol)
                if sig:
                    signals.append(sig)
            except Exception as exc:
                logger.warning("TA %s: %s", vol.symbol, exc)
        signals.sort(key=lambda x: x.confidence, reverse=True)
        logger.info(
            "TA scan: volatile=%d signals=%d top=%s",
            len(volatile),
            len(signals),
            ", ".join(f"{s.symbol} {s.side}" for s in signals[:5]) or "—",
        )
        return signals, volatile
