#!/usr/bin/env python3
"""
BACKTESTING ENGINE — Historical strategy evaluation.

Fetches historical klines from Bybit, runs the SMC v5 entry engine,
simulates trades, and calculates performance metrics.

Usage:
    python -m bot.backtester --symbol BTCUSDT --days 30
    python -m bot.backtester --all-whitelist --days 14
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

BOT_DIR = Path(__file__).parent.resolve()
if str(BOT_DIR) not in sys.path:
    sys.path.insert(0, str(BOT_DIR))

from analysis.market_analyzer import MarketAnalyzer, MarketAnalysis
from analysis.market_regime_ai import MarketRegimeAI
from analysis.market_structure import MarketStructureEngine
from analysis.orderflow_analyzer import OrderflowAnalyzer, OrderflowSnapshot
from analysis.structure_zones import StructureZoneAnalyzer
from analysis.transformer_model import TransformerPriceModel
from analysis.feature_engineering import FeatureEngineer
from analysis.liquidation_clusters import LiquidationClusterDetector, LiquidationAnalysis
from core.config import BotConfig
from engine.entry_engine import EntryEngine
from exchange.bybit_client import BybitClient
from utils import ATRCalculator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("BACKTEST")


@dataclass
class BacktestTrade:
    symbol: str
    side: str  # BUY or SELL
    entry_price: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    entry_time: str
    exit_price: float = 0.0
    exit_time: str = ""
    pnl_pct: float = 0.0
    result: str = ""  # "win", "loss", "open"
    exit_reason: str = ""
    reasons: list = field(default_factory=list)
    htf_4h_trend: int = 0
    entry_zone: str = ""


@dataclass
class BacktestResult:
    symbol: str
    period_days: int
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_rr: float
    profit_factor: float
    total_pnl_pct: float
    max_drawdown_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    trades: List[BacktestTrade] = field(default_factory=list)
    rejected_reasons: Dict[str, int] = field(default_factory=dict)


class Backtester:
    """Runs the SMC v5 strategy against historical data."""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = str(BOT_DIR / "config.yaml")
        self.cfg = BotConfig.load(config_path)

        # Analysis modules (same as live bot)
        self.market_analyzer = MarketAnalyzer(atr_period=self.cfg.get("atr", "period", default=14))
        self.regime_ai = MarketRegimeAI()
        self.structure_zone_analyzer = StructureZoneAnalyzer()
        self.market_structure_engine = MarketStructureEngine(
            swing_lookback=self.cfg.get("market_structure", "swing_lookback", default=2),
            volume_spike_mult=self.cfg.get("market_structure", "volume_spike_mult", default=2.0),
            bos_volume_mult=self.cfg.get("market_structure", "bos_volume_mult", default=1.5),
            spread_expansion_mult=self.cfg.get("market_structure", "spread_expansion_mult", default=1.5),
        )
        self.orderflow_analyzer = OrderflowAnalyzer()
        self.feature_engineer = FeatureEngineer(sequence_length=self.cfg.get("bot", "feature_window", default=128))
        self.transformer_model = TransformerPriceModel(sequence_length=self.cfg.get("bot", "feature_window", default=128))
        self.liq_detector = LiquidationClusterDetector()
        self.atr_calc = ATRCalculator(period=self.cfg.get("atr", "period", default=14))
        self.entry_engine = EntryEngine(self.cfg)

        # Exchange client (public API only — no keys needed for klines)
        self.client = None
        self.htf_4h_interval = self.cfg.get("bot", "htf_4h_interval", default="240")
        self.htf_interval = self.cfg.get("bot", "htf_interval", default="15")
        self.candle_interval = self.cfg.get("bot", "candle_interval", default="1")
        self.klines_limit = max(self.cfg.get("bot", "klines_limit", default=180),
                                self.cfg.get("bot", "feature_window", default=128))

    async def _init_client(self):
        """Initialize Bybit client for public data (no API keys needed)."""
        if self.client is None:
            self.client = BybitClient("", "", testnet=False, category="linear")

    async def run(self, symbol: str, days: int = 30, interval: str = "15") -> BacktestResult:
        """Run backtest for a single symbol over N days.

        Strategy: Walk forward through HTF (15m) candles.
        For each candle:
          1. Use the last 180 candles as context window
          2. Run entry engine
          3. If signal → open virtual trade
          4. Track open trades for SL/TP hits
        """
        await self._init_client()
        logger.info(f"=== BACKTEST {symbol} | {days}d | interval={interval} ===")

        # Fetch historical data
        total_candles = self._candles_needed(days, interval)
        klines = await self._fetch_klines(symbol, interval, total_candles)
        htf_klines = await self._fetch_klines(symbol, self.htf_interval, min(total_candles, 500))
        klines_4h = await self._fetch_klines(symbol, self.htf_4h_interval, 100)

        if len(klines) < self.klines_limit + 50:
            logger.warning(f"{symbol}: Not enough data ({len(klines)} candles)")
            return BacktestResult(symbol=symbol, period_days=days, total_signals=0,
                                  total_trades=0, wins=0, losses=0, win_rate=0,
                                  avg_rr=0, profit_factor=0, total_pnl_pct=0,
                                  max_drawdown_pct=0, avg_win_pct=0, avg_loss_pct=0)

        trades: List[BacktestTrade] = []
        open_trades: List[BacktestTrade] = []
        rejected: Dict[str, int] = {}
        total_signals = 0

        # Determine 4H trend from historical 4H data
        htf_4h_trend = self._determine_4h_trend(klines_4h)

        # Walk forward
        window = self.klines_limit
        step = max(1, window // 20)  # Check every ~5% of window

        for i in range(window, len(klines), step):
            candle_slice = klines[i - window:i]
            htf_slice = [k for k in htf_klines if k["timestamp"] <= candle_slice[-1]["timestamp"]]
            if len(htf_slice) < 60:
                htf_slice = htf_klines[:min(80, len(htf_klines))]

            current_price = float(candle_slice[-1]["close"])
            candle_time = datetime.fromtimestamp(
                candle_slice[-1]["timestamp"] / 1000, tz=timezone.utc
            ).isoformat() if candle_slice[-1]["timestamp"] > 1e9 else str(candle_slice[-1]["timestamp"])

            # Update 4H trend periodically (every ~4h worth of candles)
            if i % max(1, self._candles_per_period(interval, 240)) == 0 and len(klines_4h) >= 20:
                # Find 4H candles up to current time
                cutoff = candle_slice[-1]["timestamp"]
                h4_slice = [k for k in klines_4h if k["timestamp"] <= cutoff]
                if len(h4_slice) >= 20:
                    htf_4h_trend = self._determine_4h_trend(h4_slice)

            # Check open trades for SL/TP
            still_open = []
            for trade in open_trades:
                high = float(candle_slice[-1]["high"])
                low = float(candle_slice[-1]["low"])
                hit = self._check_trade_exit(trade, high, low, current_price, candle_time)
                if hit:
                    trades.append(trade)
                else:
                    still_open.append(trade)
            open_trades = still_open

            # Skip if already have an open trade for this symbol
            if any(t.symbol == symbol for t in open_trades):
                continue

            # Run entry engine
            total_signals += 1
            signal = self._evaluate_signal(
                symbol, candle_slice, htf_slice, current_price,
                htf_4h_trend,
            )

            if signal.should_enter:
                trade = BacktestTrade(
                    symbol=symbol,
                    side=signal.side,
                    entry_price=current_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    rr_ratio=signal.rr_ratio,
                    entry_time=candle_time,
                    result="open",
                    reasons=signal.reasons,
                    htf_4h_trend=htf_4h_trend,
                    entry_zone=signal.metadata.get("entry_zone", ""),
                )
                open_trades.append(trade)
            else:
                reason = signal.metadata.get("reject_reason", "unknown")
                rejected[reason] = rejected.get(reason, 0) + 1

        # Close remaining open trades at last price
        last_price = float(klines[-1]["close"])
        last_time = str(klines[-1]["timestamp"])
        for trade in open_trades:
            trade.exit_price = last_price
            trade.exit_time = last_time
            trade.exit_reason = "backtest_end"
            if trade.side == "BUY":
                trade.pnl_pct = (last_price - trade.entry_price) / trade.entry_price * 100
            else:
                trade.pnl_pct = (trade.entry_price - last_price) / trade.entry_price * 100
            trade.result = "win" if trade.pnl_pct > 0 else "loss"
            trades.append(trade)

        return self._compute_result(symbol, days, total_signals, trades, rejected)

    def _evaluate_signal(self, symbol: str, klines: list, htf_klines: list,
                         current_price: float, htf_4h_trend: int):
        """Run the full entry engine pipeline on a historical candle window."""
        market = self.market_analyzer.analyze(klines, htf_klines)
        if not market.can_trade:
            from engine.entry_engine import EntrySignal
            sig = EntrySignal()
            sig.metadata["reject_reason"] = "market_blocked"
            return sig

        atr_val = self.atr_calc.get_atr(symbol, klines)
        structure = self.market_structure_engine.analyze(klines, atr_val)
        zone_context = self.structure_zone_analyzer.analyze(htf_klines, current_price)
        regime = self.regime_ai.classify(market)

        # Synthetic orderflow from candle bodies (no live orderbook in backtest)
        orderflow = self._synthetic_orderflow(klines)
        features = self.feature_engineer.build(klines, orderflow, Backtester._empty_liq(), atr_val)
        transformer = self.transformer_model.predict(features, regime, orderflow, Backtester._empty_liq())

        # Synthetic liquidation from klines
        events = self._build_synthetic_liq_events(klines, current_price)
        liq = self.liq_detector.analyze(current_price, events)

        return self.entry_engine.generate_signal(
            symbol, klines, current_price, market, regime, transformer,
            orderflow, liq, atr_val,
            zone_context=zone_context, structure=structure,
            funding_rate=0.0, htf_4h_trend=htf_4h_trend,
        )

    def _check_trade_exit(self, trade: BacktestTrade, high: float, low: float,
                          close: float, time_str: str) -> bool:
        """Check if a trade hits SL or TP. Returns True if closed."""
        if trade.side == "BUY":
            if low <= trade.stop_loss:
                trade.exit_price = trade.stop_loss
                trade.exit_time = time_str
                trade.exit_reason = "stop_loss"
                trade.pnl_pct = (trade.stop_loss - trade.entry_price) / trade.entry_price * 100
                trade.result = "loss"
                return True
            if high >= trade.take_profit:
                trade.exit_price = trade.take_profit
                trade.exit_time = time_str
                trade.exit_reason = "take_profit"
                trade.pnl_pct = (trade.take_profit - trade.entry_price) / trade.entry_price * 100
                trade.result = "win"
                return True
        else:  # SELL
            if high >= trade.stop_loss:
                trade.exit_price = trade.stop_loss
                trade.exit_time = time_str
                trade.exit_reason = "stop_loss"
                trade.pnl_pct = (trade.entry_price - trade.stop_loss) / trade.entry_price * 100
                trade.result = "loss"
                return True
            if low <= trade.take_profit:
                trade.exit_price = trade.take_profit
                trade.exit_time = time_str
                trade.exit_reason = "take_profit"
                trade.pnl_pct = (trade.entry_price - trade.take_profit) / trade.entry_price * 100
                trade.result = "win"
                return True
        return False

    def _compute_result(self, symbol: str, days: int, total_signals: int,
                        trades: List[BacktestTrade],
                        rejected: Dict[str, int]) -> BacktestResult:
        """Compute aggregate statistics."""
        wins = [t for t in trades if t.result == "win"]
        losses = [t for t in trades if t.result == "loss"]
        n_wins = len(wins)
        n_losses = len(losses)
        n_total = len(trades)
        win_rate = n_wins / n_total * 100 if n_total > 0 else 0.0
        avg_rr = sum(t.rr_ratio for t in trades) / n_total if n_total > 0 else 0.0
        avg_win = sum(t.pnl_pct for t in wins) / n_wins if n_wins > 0 else 0.0
        avg_loss = sum(t.pnl_pct for t in losses) / n_losses if n_losses > 0 else 0.0
        total_win_pnl = sum(t.pnl_pct for t in wins)
        total_loss_pnl = abs(sum(t.pnl_pct for t in losses))
        profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float('inf') if total_win_pnl > 0 else 0.0
        total_pnl = sum(t.pnl_pct for t in trades)

        # Max drawdown
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            equity += t.pnl_pct
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            symbol=symbol,
            period_days=days,
            total_signals=total_signals,
            total_trades=n_total,
            wins=n_wins,
            losses=n_losses,
            win_rate=round(win_rate, 1),
            avg_rr=round(avg_rr, 2),
            profit_factor=round(profit_factor, 2) if profit_factor != float('inf') else 999.0,
            total_pnl_pct=round(total_pnl, 2),
            max_drawdown_pct=round(max_dd, 2),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            trades=trades,
            rejected_reasons=rejected,
        )

    # --- Data helpers ---

    async def _fetch_klines(self, symbol: str, interval: str, limit: int) -> list:
        """Fetch historical klines, paginating if needed (Bybit max 200 per call)."""
        all_klines = []
        remaining = limit
        end_time = None

        while remaining > 0:
            batch_size = min(remaining, 200)
            params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": batch_size}
            if end_time:
                params["end"] = end_time

            result = await self.client._request("GET", "/v5/market/kline", params)
            if not result or not result.get("list"):
                break

            batch = []
            for k in reversed(result["list"]):
                batch.append({
                    "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                })

            if not batch:
                break

            all_klines = batch + all_klines
            remaining -= len(batch)

            # Set end_time to the earliest timestamp for next page
            end_time = int(result["list"][-1][0]) - 1
            if len(result["list"]) < batch_size:
                break

            # Rate limit: Bybit allows ~10 req/sec
            await asyncio.sleep(0.15)

        return all_klines

    def _determine_4h_trend(self, klines_4h: list) -> int:
        """Same as main bot: EMA20 vs EMA50 + last 3 candles."""
        if len(klines_4h) < 20:
            return 0
        closes = [float(k["close"]) for k in klines_4h]
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, min(50, len(closes)))
        recent = closes[-3:]
        rising = recent[-1] > recent[0]
        falling = recent[-1] < recent[0]
        if ema20 > ema50 and rising:
            return 1
        elif ema20 < ema50 and falling:
            return -1
        return 0

    @staticmethod
    def _ema(data: list, period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data) if data else 0
        mult = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * mult + ema
        return ema

    @staticmethod
    def _candles_needed(days: int, interval: str) -> int:
        minutes_per_candle = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
                              "60": 60, "120": 120, "240": 240, "D": 1440}
        mins = minutes_per_candle.get(interval, 15)
        return int(days * 24 * 60 / mins)

    @staticmethod
    def _candles_per_period(interval: str, target_minutes: int) -> int:
        minutes_per_candle = {"1": 1, "3": 3, "5": 5, "15": 15, "30": 30,
                              "60": 60, "120": 120, "240": 240}
        mins = minutes_per_candle.get(interval, 15)
        return max(1, target_minutes // mins)

    @staticmethod

    @staticmethod
    def _synthetic_orderflow(klines: list) -> OrderflowSnapshot:
        """Build synthetic orderflow from candlestick data.

        Estimates buy/sell pressure from candle bodies:
        - Bullish candle (close > open): body = buy volume
        - Bearish candle (close < open): body = sell volume
        - Wick ratio shows rejection strength
        """
        recent = klines[-20:] if len(klines) >= 20 else klines
        buy_vol = 0.0
        sell_vol = 0.0

        for k in recent:
            o, h, l, c = float(k["open"]), float(k["high"]), float(k["low"]), float(k["close"])
            vol = float(k.get("volume", 1.0))
            rng = h - l if h > l else 0.0001
            body = abs(c - o)
            body_ratio = body / rng  # 0 = doji, 1 = full body

            if c > o:
                # Bullish candle
                buy_vol += vol * body_ratio
                sell_vol += vol * (1 - body_ratio) * 0.5
            elif c < o:
                # Bearish candle
                sell_vol += vol * body_ratio
                buy_vol += vol * (1 - body_ratio) * 0.5
            else:
                # Doji
                buy_vol += vol * 0.5
                sell_vol += vol * 0.5

        total = buy_vol + sell_vol
        norm_imb = (buy_vol - sell_vol) / total if total > 0 else 0.0
        trade_ratio = buy_vol / sell_vol if sell_vol > 0 else 2.0
        bearish_ratio = sell_vol / buy_vol if buy_vol > 0 else 2.0

        dominant = "neutral"
        if norm_imb > 0.15:
            dominant = "bullish"
        elif norm_imb < -0.15:
            dominant = "bearish"

        return OrderflowSnapshot(
            orderbook_ratio=1.0,
            trade_ratio=round(trade_ratio, 4),
            bullish_ratio=round(max(trade_ratio, 1.0), 4),
            bearish_ratio=round(max(bearish_ratio, 1.0), 4),
            imbalance_score=round(norm_imb, 4),
            bid_volume=round(buy_vol, 2),
            ask_volume=round(sell_vol, 2),
            buy_volume=round(buy_vol, 2),
            sell_volume=round(sell_vol, 2),
            trade_delta=round(buy_vol - sell_vol, 2),
            volume_spike=1.0,
            spread_pct=0.01,
            dominant_side=dominant,
            normalized_imbalance=round(norm_imb, 4),
        )

    def _empty_liq():
        return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

    @staticmethod
    def _build_synthetic_liq_events(klines: list, current_price: float) -> list:
        events = []
        for candle in klines[-36:]:
            high = float(candle.get("high", 0.0))
            low = float(candle.get("low", 0.0))
            close = float(candle.get("close", current_price))
            volume = float(candle.get("volume", 0.0))
            weight = max(volume * close, 1.0)
            if high > current_price:
                events.append({"price": high, "size": weight, "side": "Sell"})
            if low < current_price:
                events.append({"price": low, "size": weight, "side": "Buy"})
        return events

    async def close(self):
        if self.client:
            await self.client.close()


def format_report(results: List[BacktestResult]) -> str:
    """Format backtest results as a readable report."""
    lines = ["=" * 60, "BACKTEST REPORT — SMC v5 Strategy", "=" * 60, ""]

    total_trades = 0
    total_wins = 0
    total_pnl = 0.0

    for r in results:
        lines.append(f"{'─' * 50}")
        lines.append(f"  {r.symbol} | {r.period_days} days")
        lines.append(f"{'─' * 50}")
        lines.append(f"  Signals scanned:  {r.total_signals}")
        lines.append(f"  Trades executed:  {r.total_trades}")
        lines.append(f"  Wins / Losses:    {r.wins} / {r.losses}")
        lines.append(f"  Win Rate:         {r.win_rate:.1f}%")
        lines.append(f"  Avg RR:           {r.avg_rr:.2f}")
        lines.append(f"  Profit Factor:    {r.profit_factor:.2f}")
        lines.append(f"  Total PnL:        {r.total_pnl_pct:+.2f}%")
        lines.append(f"  Max Drawdown:     {r.max_drawdown_pct:.2f}%")
        lines.append(f"  Avg Win:          {r.avg_win_pct:+.2f}%")
        lines.append(f"  Avg Loss:         {r.avg_loss_pct:+.2f}%")

        if r.rejected_reasons:
            lines.append(f"  Top rejections:")
            for reason, count in sorted(r.rejected_reasons.items(), key=lambda x: -x[1])[:5]:
                lines.append(f"    {reason}: {count}")
        lines.append("")

        total_trades += r.total_trades
        total_wins += r.wins
        total_pnl += r.total_pnl_pct

    if len(results) > 1:
        lines.append(f"{'=' * 50}")
        lines.append(f"  TOTAL ACROSS ALL SYMBOLS")
        lines.append(f"{'=' * 50}")
        lines.append(f"  Total Trades:  {total_trades}")
        lines.append(f"  Total Wins:    {total_wins}")
        lines.append(f"  Win Rate:      {total_wins / total_trades * 100:.1f}%" if total_trades > 0 else "  Win Rate:      N/A")
        lines.append(f"  Total PnL:     {total_pnl:+.2f}%")

    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="SMC v5 Backtester")
    parser.add_argument("--symbol", type=str, help="Single symbol (e.g. BTCUSDT)")
    parser.add_argument("--all-whitelist", action="store_true", help="Test all whitelist symbols")
    parser.add_argument("--days", type=int, default=14, help="Number of days to backtest")
    parser.add_argument("--interval", type=str, default="15", help="Candle interval (1, 5, 15, 60)")
    parser.add_argument("--output", type=str, help="Save JSON results to file")
    args = parser.parse_args()

    bt = Backtester()

    symbols = []
    if args.all_whitelist:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "LINKUSDT", "BNBUSDT"]
    elif args.symbol:
        symbols = [args.symbol]
    else:
        symbols = ["BTCUSDT"]

    results = []
    for symbol in symbols:
        try:
            result = await bt.run(symbol, days=args.days, interval=args.interval)
            results.append(result)
            await asyncio.sleep(2)  # Rate limit between symbols
        except Exception as e:
            logger.error(f"Error backtesting {symbol}: {e}")

    await bt.close()

    # Print report
    report = format_report(results)
    print(report)

    # Save JSON
    output_path = args.output or str(BOT_DIR / "backtest_results.json")
    json_data = []
    for r in results:
        d = asdict(r)
        json_data.append(d)
    with open(output_path, "w") as f:
        json.dump(json_data, f, indent=2, default=str)
    logger.info(f"Results saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
