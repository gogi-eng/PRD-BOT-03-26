"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotCorrelationMixin:
    async def _update_correlation_cache(self, symbol: str):
        lookback = max(int(self.correlation_filter.lookback), 20)
        klines = await self.client.get_klines(symbol, self.candle_interval, lookback + 5)
        closes = [float(item.get("close", 0.0) or 0.0) for item in klines if float(item.get("close", 0.0) or 0.0) > 0]
        if len(closes) >= 10:
            self.correlation_filter.update_prices(symbol, closes)


    async def _passes_correlation_filter(self, symbol: str, same_side_symbols: list[str]) -> tuple[bool, str]:
        if not self.correlation_filter_enabled or not same_side_symbols:
            return True, ""
        try:
            await self._update_correlation_cache(symbol)
            for peer in same_side_symbols:
                await self._update_correlation_cache(peer)
            should_filter, reason = self.correlation_filter.should_filter(symbol, same_side_symbols)
            return (not should_filter), reason
        except Exception as exc:
            logger.warning(f"Correlation filter error for {symbol}: {exc}")
            return True, ""


    def _same_side_peer_symbols(self, side: str, candidates: list[dict]) -> list[str]:
        side_up = str(side).upper()
        peers = []
        for symbol in self.position_manager.symbols():
            pos = self.position_manager.get(symbol)
            if pos and str(pos.side).upper() == side_up:
                peers.append(symbol)
        for item in candidates:
            sig = item.get("signal")
            if sig and str(sig.side).upper() == side_up:
                peers.append(item.get("symbol", ""))
        return [s for s in self._unique_symbols(peers) if s]
