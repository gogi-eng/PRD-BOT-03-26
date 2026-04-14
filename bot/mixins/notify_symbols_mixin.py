"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotNotifySymbolsMixin:
    async def _notify_tg(self, message: str):
        if self.tg:
            await self.tg.send_alert(message)


    async def get_trade_symbols(self) -> list:
        """Scan top symbols by momentum. Whitelist symbols always at front (priority).
        If whitelist_only=True, ONLY whitelist symbols are traded."""
        # Whitelist-only mode: skip scanning, return whitelist directly
        if self.whitelist_only and self.whitelist:
            result = [s for s in self.whitelist if s not in self.blacklist]
            logger.info(f"Symbol scanner: WHITELIST-ONLY mode → {len(result)} symbols: {result}")
            return result

        try:
            tickers = await self.client.get_tickers()
        except Exception as exc:
            logger.error(f"Failed to get tickers: {exc}")
            limit = max(1, int(getattr(self, "trade_symbols", 25) or 25))
            return self.whitelist[:limit] if self.whitelist else []

        ranked = []
        for ticker in tickers:
            symbol = ticker.get("symbol", "")
            if not symbol.endswith("USDT"):
                continue
            if symbol in self.blacklist:
                continue
            if any(part in symbol for part in self.blacklist_substrings):
                continue
            turnover = float(ticker.get("turnover24h", 0) or 0)
            if turnover < self.min_volume:
                continue
            price_change_pct = abs(float(ticker.get("price24hPcnt", 0) or 0)) * 100
            momentum_score = turnover * (1 + price_change_pct / 10)
            ranked.append((symbol, turnover, momentum_score))

        ranked.sort(key=lambda item: item[2], reverse=True)
        symbols = [item[0] for item in ranked[: self.max_symbols]]

        # Whitelist symbols always at front (priority, not exclusive)
        if self.whitelist_enabled:
            ordered = [s for s in self.whitelist if s not in self.blacklist]
            for s in reversed(ordered):
                if s in symbols:
                    symbols.remove(s)
                symbols.insert(0, s)

        unique = []
        seen = set()
        for s in symbols:
            if s not in seen:
                unique.append(s)
                seen.add(s)

        limit = max(1, int(getattr(self, "trade_symbols", 25) or 25))
        result = unique[:limit]
        wl_in = [s for s in self.whitelist if s in result]
        logger.info(f"Symbol scanner: {len(ranked)} eligible → top {len(result)} (whitelist: {wl_in})")
        return result
