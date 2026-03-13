#!/usr/bin/env python3
"""Orderflow analysis for orderbook and recent trades."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class OrderflowSnapshot:
    orderbook_ratio: float = 1.0
    trade_ratio: float = 1.0
    bullish_ratio: float = 1.0
    bearish_ratio: float = 1.0
    imbalance_score: float = 0.0
    bid_volume: float = 0.0
    ask_volume: float = 0.0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    trade_delta: float = 0.0
    volume_spike: float = 1.0
    spread_pct: float = 0.0
    dominant_side: str = "neutral"


class OrderflowAnalyzer:
    """Builds a compact orderflow snapshot from Bybit orderbook and trades."""

    def __init__(self, depth_levels: int = 10):
        self.depth_levels = depth_levels

    def analyze(self, orderbook: Dict, trades: List[Dict]) -> OrderflowSnapshot:
        bids = orderbook.get("bids", [])[: self.depth_levels]
        asks = orderbook.get("asks", [])[: self.depth_levels]

        bid_volume = sum(float(level[1]) for level in bids if len(level) >= 2)
        ask_volume = sum(float(level[1]) for level in asks if len(level) >= 2)
        orderbook_ratio = bid_volume / ask_volume if ask_volume > 0 else 1.0

        buy_volume = 0.0
        sell_volume = 0.0
        sizes: List[float] = []
        for trade in trades:
            size = float(trade.get("size", 0.0))
            side = str(trade.get("side", "")).lower()
            sizes.append(size)
            if side == "buy":
                buy_volume += size
            elif side == "sell":
                sell_volume += size

        trade_ratio = buy_volume / sell_volume if sell_volume > 0 else (2.0 if buy_volume > 0 else 1.0)
        bearish_trade_ratio = sell_volume / buy_volume if buy_volume > 0 else (2.0 if sell_volume > 0 else 1.0)
        trade_delta = buy_volume - sell_volume

        recent = sizes[:10] if sizes else []
        baseline = sizes[10:50] if len(sizes) > 10 else sizes
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        baseline_avg = sum(baseline) / len(baseline) if baseline else recent_avg
        volume_spike = recent_avg / baseline_avg if baseline_avg > 0 else 1.0

        best_bid = float(bids[0][0]) if bids and len(bids[0]) >= 2 else 0.0
        best_ask = float(asks[0][0]) if asks and len(asks[0]) >= 2 else 0.0
        mid = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0
        spread_pct = ((best_ask - best_bid) / mid * 100) if mid > 0 else 0.0

        orderbook_edge = orderbook_ratio - 1.0
        trade_edge = trade_ratio - 1.0
        imbalance_score = orderbook_edge * 0.55 + trade_edge * 0.45

        dominant_side = "neutral"
        if imbalance_score >= 0.18:
            dominant_side = "bullish"
        elif imbalance_score <= -0.18:
            dominant_side = "bearish"

        return OrderflowSnapshot(
            orderbook_ratio=round(orderbook_ratio, 4),
            trade_ratio=round(trade_ratio, 4),
            bullish_ratio=round(max(orderbook_ratio, trade_ratio), 4),
            bearish_ratio=round(max((1 / orderbook_ratio) if orderbook_ratio > 0 else 0.0, bearish_trade_ratio), 4),
            imbalance_score=round(imbalance_score, 4),
            bid_volume=round(bid_volume, 4),
            ask_volume=round(ask_volume, 4),
            buy_volume=round(buy_volume, 4),
            sell_volume=round(sell_volume, 4),
            trade_delta=round(trade_delta, 4),
            volume_spike=round(volume_spike, 4),
            spread_pct=round(spread_pct, 5),
            dominant_side=dominant_side,
        )