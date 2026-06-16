"""
Внешний сентимент для scanner/signals:
- Coinugget-style: RSI экстремумы + всплеск объёма по свечам Bybit
- Adanos: Reddit Crypto trending (API key в .env: ADANOS_API_KEY)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from prd_agent.analysis.technical_indicators import klines_to_df, rsi
from prd_agent.signals.types import UnifiedSignal

logger = logging.getLogger("prd_agent.external_sentiment")


@dataclass
class _CacheEntry:
    ts: float
    signals: List[UnifiedSignal]


def _normalize_bybit_symbol(token: str) -> str:
    raw = str(token or "").upper().strip().replace("/", "").replace("-", "")
    if not raw:
        return ""
    if raw.endswith("USDT"):
        return raw
    if raw.endswith("USD"):
        return raw + "T"
    return f"{raw}USDT"


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

        ad = block.get("adanos", {}) if isinstance(block.get("adanos"), dict) else {}
        self.ad_enabled = bool(ad.get("enabled", True))
        self.ad_base = str(ad.get("base_url", "https://api.adanos.org")).rstrip("/")
        self.ad_key_env = str(ad.get("api_key_env", "ADANOS_API_KEY"))
        self.ad_limit = int(ad.get("trending_limit", 10))
        self.ad_min_buzz = float(ad.get("min_buzz_score", 55))
        self.ad_min_sent = float(ad.get("min_sentiment_abs", 0.15))
        self.cache_sec = float(block.get("cache_sec", 900))
        self._cache: Optional[_CacheEntry] = None

    def _api_key(self) -> str:
        return os.getenv(self.ad_key_env, "").strip()

    async def _fetch_adanos_trending(self, session: aiohttp.ClientSession) -> List[Dict[str, Any]]:
        key = self._api_key()
        if not key:
            return []
        url = f"{self.ad_base}/reddit/crypto/v1/trending"
        headers = {"X-API-Key": key, "Accept": "application/json"}
        try:
            async with session.get(
                url,
                headers=headers,
                params={"limit": self.ad_limit},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 401:
                    logger.warning("Adanos: неверный API key (%s)", self.ad_key_env)
                    return []
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Adanos trending HTTP %s: %s", resp.status, body[:200])
                    return []
                data = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning("Adanos trending: %s", exc)
            return []
        items = data.get("results") or data.get("tokens") or data.get("data") or []
        if isinstance(items, dict):
            items = list(items.values())
        return [x for x in items if isinstance(x, dict)]

    def _adanos_to_signal(self, row: Dict[str, Any], price: float) -> Optional[UnifiedSignal]:
        symbol_raw = str(row.get("symbol") or row.get("token") or row.get("ticker") or "")
        sym = _normalize_bybit_symbol(symbol_raw)
        if not sym:
            return None
        buzz = float(row.get("buzz_score", 0) or 0)
        if buzz < self.ad_min_buzz:
            return None
        sent = row.get("sentiment_score")
        if sent is None:
            bull = float(row.get("bullish_pct", 0) or 0)
            bear = float(row.get("bearish_pct", 0) or 0)
            if bull + bear <= 0:
                return None
            sent = (bull - bear) / 100.0
        sent_f = float(sent)
        if abs(sent_f) < self.ad_min_sent:
            return None
        side = "Buy" if sent_f > 0 else "Sell"
        conf = min(0.92, 0.55 + abs(sent_f) * 0.25 + buzz / 500.0)
        trend = str(row.get("trend") or "")
        reason = f"Adanos Reddit: buzz={buzz:.0f} sent={sent_f:+.2f} trend={trend}"
        atr_pct = 0.006
        if side == "Buy":
            sl, tp = price * (1 - atr_pct), price * (1 + atr_pct * 2)
        else:
            sl, tp = price * (1 + atr_pct), price * (1 - atr_pct * 2)
        return UnifiedSignal(
            symbol=sym,
            side=side,
            confidence=conf,
            source="adanos_reddit",
            entry=price,
            stop_loss=sl,
            take_profit=tp,
            reason=reason,
            raw={"adanos": row},
        )

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

        if self.ad_enabled and self._api_key():
            async with aiohttp.ClientSession() as session:
                trending = await self._fetch_adanos_trending(session)
            for row in trending:
                sym = _normalize_bybit_symbol(
                    str(row.get("symbol") or row.get("token") or row.get("ticker") or "")
                )
                if not sym:
                    continue
                try:
                    price = await exchange.get_price(sym)
                except Exception:
                    continue
                sig = self._adanos_to_signal(row, price)
                if sig:
                    out.append(sig)
        elif self.ad_enabled and not self._api_key():
            logger.debug("Adanos: пропуск — нет %s в .env", self.ad_key_env)

        self._cache = _CacheEntry(ts=now, signals=out)
        if out:
            logger.info(
                "External sentiment: coinugget_style=%d adanos=%d total=%d",
                sum(1 for s in out if s.source == "coinugget_style"),
                sum(1 for s in out if s.source == "adanos_reddit"),
                len(out),
            )
        return out
