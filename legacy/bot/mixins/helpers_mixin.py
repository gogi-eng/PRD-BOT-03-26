"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotHelpersMixin:
    @staticmethod
    def _interval_to_seconds(interval: str | int | float) -> int:
        return interval_to_seconds(interval)


    @staticmethod
    def _last_closed_kline_ts(klines: list) -> int:
        return last_closed_kline_ts(klines)


    @staticmethod
    def _parse_iso_dt(value: str):
        return parse_iso_dt(value)


    def _unique_symbols(self, symbols: list[str]) -> list[str]:
        unique = []
        seen = set()
        for symbol in symbols:
            if symbol and symbol not in seen:
                unique.append(symbol)
                seen.add(symbol)
        return unique


    @staticmethod
    def _candle_dir(candle: dict) -> int:
        c_open = float(candle.get("open", 0.0) or 0.0)
        c_close = float(candle.get("close", 0.0) or 0.0)
        if c_close > c_open:
            return 1
        if c_close < c_open:
            return -1
        return 0


    @staticmethod
    def _candle_body(candle: dict) -> float:
        c_open = float(candle.get("open", 0.0) or 0.0)
        c_close = float(candle.get("close", 0.0) or 0.0)
        return abs(c_close - c_open)


    @staticmethod
    def _ema(data: list, period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        mult = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * mult + ema
        return ema


    def _determine_4h_trend(self, klines_4h: list) -> int:
        """Determine 4H trend: 1=bullish, -1=bearish, 0=neutral.

        Uses EMA20 vs EMA50 on 4H candles + last 3 candle direction.
        """
        if len(klines_4h) < 20:
            return 0
        closes = [float(k["close"]) for k in klines_4h]

        # EMA20 vs EMA50
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, min(50, len(closes)))

        # Last 3 candles direction
        recent = closes[-3:]
        rising = recent[-1] > recent[0]
        falling = recent[-1] < recent[0]

        if ema20 > ema50 and rising:
            return 1
        elif ema20 < ema50 and falling:
            return -1
        return 0
