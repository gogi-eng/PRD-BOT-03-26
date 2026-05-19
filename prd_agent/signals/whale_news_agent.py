"""
Агент «киты + новости»: RSS + ликвидации Bybit + скачок OI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from telegram_agent.world_feed import fetch_rss_items


@dataclass
class MacroSignal:
    symbol: str
    side: str
    confidence: float
    source: str
    reason: str
    raw: Dict[str, Any]


class WhaleNewsAgent:
    def __init__(self, cfg: Dict[str, Any]):
        wn = cfg.get("whale_news", {})
        self.rss_urls: List[str] = list(wn.get("rss_urls", []))
        self.liq_usd_threshold = float(wn.get("liquidation_usd_threshold", 500_000))
        self.oi_change_pct = float(wn.get("oi_change_pct_threshold", 3.0))
        self.bull_kw = [k.lower() for k in wn.get("news_keywords_bull", [])]
        self.bear_kw = [k.lower() for k in wn.get("news_keywords_bear", [])]
        self._seen_news: set[str] = set()

    def _news_score(self, text: str) -> float:
        t = (text or "").lower()
        bull = sum(1 for k in self.bull_kw if k in t)
        bear = sum(1 for k in self.bear_kw if k in t)
        if bull == bear == 0:
            return 0.0
        return max(-1.0, min(1.0, (bull - bear) / max(bull + bear, 1)))

    async def scan_news(self, symbols: List[str]) -> List[MacroSignal]:
        out: List[MacroSignal] = []
        macro_score = 0.0
        headlines: List[str] = []
        for url in self.rss_urls[:5]:
            for item in fetch_rss_items(url, max_items=8):
                uid = item.get("id", "")
                if uid in self._seen_news:
                    continue
                self._seen_news.add(uid)
                title = item.get("title", "")
                summary = item.get("summary", "")
                text = f"{title} {summary}"
                headlines.append(title[:120])
                macro_score += self._news_score(text)
        if not headlines:
            return out
        avg = macro_score / max(len(headlines), 1)
        if abs(avg) < 0.15:
            return out
        side = "Buy" if avg > 0 else "Sell"
        conf = min(0.85, 0.45 + abs(avg) * 0.4)
        for sym in symbols[:2]:
            out.append(
                MacroSignal(
                    symbol=sym,
                    side=side,
                    confidence=conf,
                    source="macro_news",
                    reason=f"Новости: {headlines[0][:80]}…",
                    raw={"headlines": headlines[:5], "score": avg},
                )
            )
        return out

    async def scan_whales(self, exchange, symbols: List[str]) -> List[MacroSignal]:
        out: List[MacroSignal] = []
        if hasattr(exchange, "set_liquidation_symbols"):
            await exchange.set_liquidation_symbols(symbols)
        for sym in symbols:
            liqs = await exchange.get_recent_liquidations(sym, limit=30)
            buy_liq = 0.0
            sell_liq = 0.0
            for ev in liqs:
                px = float(ev.get("price", 0) or 0)
                sz = float(ev.get("size", 0) or ev.get("qty", 0) or 0)
                usd = px * sz
                side = str(ev.get("side", "")).upper()
                if side in ("BUY", "LONG"):
                    buy_liq += usd
                else:
                    sell_liq += usd
            dominant = max(buy_liq, sell_liq)
            if dominant < self.liq_usd_threshold:
                continue
            # Крупная ликвидация лонгов → давление вниз (Sell), шортов → (Buy)
            if sell_liq > buy_liq:
                side = "Buy"
                conf = min(0.9, 0.5 + sell_liq / self.liq_usd_threshold * 0.05)
            else:
                side = "Sell"
                conf = min(0.9, 0.5 + buy_liq / self.liq_usd_threshold * 0.05)
            out.append(
                MacroSignal(
                    symbol=sym,
                    side=side,
                    confidence=conf,
                    source="whale_liquidation",
                    reason=f"Ликвидации ~${dominant:,.0f} (buy={buy_liq:,.0f} sell={sell_liq:,.0f})",
                    raw={"buy_liq": buy_liq, "sell_liq": sell_liq},
                )
            )

            oi_hist = await exchange.get_open_interest_history(sym, interval="1h", limit=5)
            if len(oi_hist) >= 2:
                try:
                    o0 = float(oi_hist[0].get("openInterest", 0))
                    o1 = float(oi_hist[-1].get("openInterest", 0))
                    if o1 > 0:
                        chg = (o0 - o1) / o1 * 100
                        if abs(chg) >= self.oi_change_pct:
                            oi_side = "Buy" if chg > 0 else "Sell"
                            out.append(
                                MacroSignal(
                                    symbol=sym,
                                    side=oi_side,
                                    confidence=min(0.8, 0.55 + abs(chg) / 20),
                                    source="whale_oi",
                                    reason=f"OI Δ {chg:+.1f}% за период",
                                    raw={"oi_change_pct": chg},
                                )
                            )
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
        return out

    async def collect(self, exchange, symbols: List[str]) -> List[MacroSignal]:
        news = await self.scan_news(symbols)
        whales = await self.scan_whales(exchange, symbols)
        return news + whales
