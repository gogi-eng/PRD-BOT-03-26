"""Auto-split from main.TradingBot — see package bot.trading_bot."""
from __future__ import annotations

from bot.trading_bot_imports import *  # noqa: F401,F403

class TradingBotLiquidationMixin:
    def _resolve_liquidation_context(self, symbol: str, current_price: float, klines: list[dict]):
        liq = self.liq_detector.analyze(current_price, self.client.get_liquidation_events(symbol))
        if liq.target_level > 0:
            return liq
        # Quasi-liquidation model: estimate where leveraged positions get liquidated
        # based on ATR, typical leverage levels, and price structure
        quasi_liq = self._build_quasi_liquidation_model(klines, current_price)
        if quasi_liq.target_level > 0:
            logger.info(f"[HEATMAP] {symbol}: quasi-liquidation model (ATR+leverage zones)")
            return quasi_liq
        return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)


    def _build_quasi_liquidation_model(self, klines: list[dict], current_price: float) -> LiquidationAnalysis:
        """Build quasi-liquidation heatmap from ATR + leverage zones.

        Logic (Coinglass-inspired):
        1. High leverage (50x-125x) traders get liquidated at 0.8-2% from entry
        2. Medium leverage (10x-25x) at 4-10% from entry
        3. Recent swing highs/lows act as entry clusters
        4. ATR defines the "heat zone" width
        """
        if len(klines) < 20 or current_price <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        highs = [float(k["high"]) for k in klines[-50:]]
        lows = [float(k["low"]) for k in klines[-50:]]

        # ATR approximation
        ranges = [high_val - low_val for high_val, low_val in zip(highs, lows)]
        atr = sum(ranges[-14:]) / min(14, len(ranges)) if ranges else current_price * 0.01

        # Leverage liquidation zones (% from current price)
        # 50x-125x leverage → liquidated at 0.8-2.0% move
        # 10x-25x leverage → liquidated at 4-10% move
        high_lev_dist = current_price * 0.012  # ~1.2% (50x zone)
        # Find recent swing highs/lows as entry clusters
        recent_swing_highs = sorted(highs[-20:], reverse=True)[:3]
        recent_swing_lows = sorted(lows[-20:])[:3]

        above_clusters = []  # Shorts' stop-losses above price (liquidity magnets for longs)
        below_clusters = []  # Longs' stop-losses below price (liquidity magnets for shorts)

        # High-leverage liquidation zone above (shorts getting squeezed)
        liq_above_50x = current_price + high_lev_dist
        above_clusters.append(LiquidationCluster(
            round(liq_above_50x, 8), round(atr * 2, 4), 1,
            round(high_lev_dist / current_price * 100, 4), "shorts_50x"
        ))

        # Swing high clusters (where shorts entered → their stops are above)
        for sh in recent_swing_highs:
            if sh > current_price:
                dist = sh - current_price
                dist_pct = dist / current_price * 100
                above_clusters.append(LiquidationCluster(
                    round(sh + atr * 0.3, 8), round(atr, 4), 1,
                    round(dist_pct, 4), "shorts_swing"
                ))

        # High-leverage liquidation zone below (longs getting liquidated)
        liq_below_50x = current_price - high_lev_dist
        below_clusters.append(LiquidationCluster(
            round(liq_below_50x, 8), round(atr * 2, 4), 1,
            round(high_lev_dist / current_price * 100, 4), "longs_50x"
        ))

        # Swing low clusters (where longs entered → their stops are below)
        for sl in recent_swing_lows:
            if sl < current_price:
                dist = current_price - sl
                dist_pct = dist / current_price * 100
                below_clusters.append(LiquidationCluster(
                    round(sl - atr * 0.3, 8), round(atr, 4), 1,
                    round(dist_pct, 4), "longs_swing"
                ))

        # Determine magnet direction: larger cluster = more liquidity = magnet
        above_total = sum(c.size for c in above_clusters)
        below_total = sum(c.size for c in below_clusters)

        if above_total > below_total * 1.3:
            # More liquidity above → price likely sweeps up
            target = max(above_clusters, key=lambda c: c.size)
            return LiquidationAnalysis(
                above_clusters, below_clusters, target, None,
                target.level, target.size, "up", 1, target.distance_pct
            )
        elif below_total > above_total * 1.3:
            # More liquidity below → price likely sweeps down
            target = max(below_clusters, key=lambda c: c.size)
            return LiquidationAnalysis(
                above_clusters, below_clusters, None, target,
                target.level, target.size, "down", -1, target.distance_pct
            )
        else:
            # Balanced
            return LiquidationAnalysis(
                above_clusters, below_clusters, None, None,
                0.0, 0.0, "neutral", 0, 0.0
            )


    def _build_synthetic_liquidation_events(self, klines: list[dict], current_price: float) -> list[dict]:
        events = []
        window = klines[-36:]
        for candle in window:
            high = float(candle.get("high", 0.0))
            low = float(candle.get("low", 0.0))
            close = float(candle.get("close", current_price) or current_price)
            volume = float(candle.get("volume", 0.0))
            weight = max(volume * close, 1.0)
            if high > current_price:
                events.append({"price": high, "size": weight, "side": "Sell"})
            if low < current_price:
                events.append({"price": low, "size": weight, "side": "Buy"})
        return events


    def _build_directional_liq_fallback(self, current_price: float, market, orderflow, atr_val: float) -> LiquidationAnalysis:
        bullish_votes = 0
        bearish_votes = 0
        if market.trend.value > 0:
            bullish_votes += 1
        elif market.trend.value < 0:
            bearish_votes += 1
        if market.htf_trend.value > 0:
            bullish_votes += 1
        elif market.htf_trend.value < 0:
            bearish_votes += 1
        if orderflow.bullish_ratio >= 1.03 and orderflow.bullish_ratio >= orderflow.bearish_ratio:
            bullish_votes += 1
        if orderflow.bearish_ratio >= 1.03 and orderflow.bearish_ratio > orderflow.bullish_ratio:
            bearish_votes += 1

        if bullish_votes == bearish_votes or current_price <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        atr = atr_val if atr_val > 0 else current_price * 0.008
        distance = max(atr * 1.8, current_price * 0.004)
        if bullish_votes > bearish_votes:
            target_level = current_price + distance
            distance_pct = distance / current_price * 100
            cluster = LiquidationCluster(round(target_level, 8), 1.0, 1, round(distance_pct, 4), "shorts")
            logger.info("[HEATMAP] directional fallback: bullish target created")
            return LiquidationAnalysis([cluster], [], cluster, None, cluster.level, cluster.size, "up", 1, cluster.distance_pct)

        target_level = max(current_price - distance, 0.0)
        distance_pct = distance / current_price * 100
        cluster = LiquidationCluster(round(target_level, 8), 1.0, 1, round(distance_pct, 4), "longs")
        logger.info("[HEATMAP] directional fallback: bearish target created")
        return LiquidationAnalysis([], [cluster], None, cluster, cluster.level, cluster.size, "down", -1, cluster.distance_pct)


    def _heatmap_to_liq_analysis(self, current_price: float, heatmap, magnet_dir: str, magnet_target: float) -> LiquidationAnalysis:
        """Convert real orderbook heatmap into LiquidationAnalysis format."""
        if magnet_dir == "neutral" or magnet_target <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        distance_pct = abs(magnet_target - current_price) / current_price * 100 if current_price > 0 else 0.0
        density = heatmap.strongest_ask.volume if magnet_dir == "up" and heatmap.strongest_ask else (
            heatmap.strongest_bid.volume if magnet_dir == "down" and heatmap.strongest_bid else 1.0
        )

        if magnet_dir == "up":
            cluster = LiquidationCluster(round(magnet_target, 8), round(density, 4), 1, round(distance_pct, 4), "shorts")
            logger.info(f"[HEATMAP] real orderbook: bullish magnet @ {magnet_target:.2f} (vol={density:.2f})")
            return LiquidationAnalysis([cluster], [], cluster, None, cluster.level, cluster.size, "up", 1, cluster.distance_pct)
        else:
            cluster = LiquidationCluster(round(magnet_target, 8), round(density, 4), 1, round(distance_pct, 4), "longs")
            logger.info(f"[HEATMAP] real orderbook: bearish magnet @ {magnet_target:.2f} (vol={density:.2f})")
            return LiquidationAnalysis([], [cluster], None, cluster, cluster.level, cluster.size, "down", -1, cluster.distance_pct)
