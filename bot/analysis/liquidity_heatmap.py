#!/usr/bin/env python3
"""
Liquidity Heatmap — real orderbook-based cluster detection (Coinglass-style).

Replaces synthetic price-action fallback with actual bid/ask wall analysis.
Finds where stops and liquidations cluster by analyzing orderbook depth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class LiquidityWall:
    price: float
    volume: float
    side: str  # "bid" or "ask"
    relative_strength: float = 0.0  # how much bigger than average


@dataclass
class HeatmapResult:
    bid_walls: List[LiquidityWall]
    ask_walls: List[LiquidityWall]
    strongest_bid: Optional[LiquidityWall]
    strongest_ask: Optional[LiquidityWall]
    bid_total_volume: float
    ask_total_volume: float
    imbalance: float  # positive = more bids (bullish), negative = more asks


class LiquidityHeatmap:
    """Builds a liquidity heatmap from real orderbook data."""

    def __init__(self, depth_levels: int = 200, wall_threshold_mult: float = 2.0):
        self.depth_levels = depth_levels
        self.wall_threshold_mult = wall_threshold_mult

    def build_heatmap(self, orderbook: Dict) -> HeatmapResult:
        raw_bids = orderbook.get("bids", [])[:self.depth_levels]
        raw_asks = orderbook.get("asks", [])[:self.depth_levels]

        bid_prices = [float(b[0]) for b in raw_bids if len(b) >= 2]
        bid_volumes = [float(b[1]) for b in raw_bids if len(b) >= 2]
        ask_prices = [float(a[0]) for a in raw_asks if len(a) >= 2]
        ask_volumes = [float(a[1]) for a in raw_asks if len(a) >= 2]

        bid_walls = self._find_walls(bid_prices, bid_volumes, "bid")
        ask_walls = self._find_walls(ask_prices, ask_volumes, "ask")

        bid_total = sum(bid_volumes)
        ask_total = sum(ask_volumes)
        total = bid_total + ask_total
        imbalance = (bid_total - ask_total) / total if total > 0 else 0.0

        strongest_bid = max(bid_walls, key=lambda w: w.volume, default=None)
        strongest_ask = max(ask_walls, key=lambda w: w.volume, default=None)

        return HeatmapResult(
            bid_walls=bid_walls,
            ask_walls=ask_walls,
            strongest_bid=strongest_bid,
            strongest_ask=strongest_ask,
            bid_total_volume=bid_total,
            ask_total_volume=ask_total,
            imbalance=round(imbalance, 4),
        )

    def _find_walls(self, prices: List[float], volumes: List[float], side: str) -> List[LiquidityWall]:
        if not volumes:
            return []
        avg_vol = sum(volumes) / len(volumes)
        threshold = avg_vol * self.wall_threshold_mult
        walls = []
        for price, vol in zip(prices, volumes):
            if vol > threshold:
                strength = vol / avg_vol if avg_vol > 0 else 0.0
                walls.append(LiquidityWall(price=price, volume=vol, side=side, relative_strength=round(strength, 2)))
        return walls

    def get_liquidity_magnet(self, current_price: float, heatmap: HeatmapResult) -> Tuple[str, float]:
        """Determine which direction the liquidity magnet pulls.

        Returns (direction, target_price):
            'up' = large ask walls above (shorts' stops = magnet)
            'down' = large bid walls below (longs' stops = magnet)
            'neutral' = balanced
        """
        if heatmap.strongest_ask and heatmap.strongest_bid:
            ask_pull = heatmap.strongest_ask.volume
            bid_pull = heatmap.strongest_bid.volume
            if ask_pull > bid_pull * 1.5:
                return "up", heatmap.strongest_ask.price
            elif bid_pull > ask_pull * 1.5:
                return "down", heatmap.strongest_bid.price
        elif heatmap.strongest_ask:
            return "up", heatmap.strongest_ask.price
        elif heatmap.strongest_bid:
            return "down", heatmap.strongest_bid.price
        return "neutral", current_price
