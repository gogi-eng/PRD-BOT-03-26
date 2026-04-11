#!/usr/bin/env python3
"""Liquidation heatmap builder following the new technical specification."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class LiquidationCluster:
    level: float
    size: float
    hits: int
    distance_pct: float
    side_bias: str


@dataclass
class LiquidationAnalysis:
    clusters_above: List[LiquidationCluster]
    clusters_below: List[LiquidationCluster]
    max_liq_cluster_above: Optional[LiquidationCluster]
    max_liq_cluster_below: Optional[LiquidationCluster]
    target_level: float
    target_density: float
    magnet_direction: str
    signal: int
    distance_to_target_pct: float


class LiquidationClusterDetector:
    """Aggregates liquidation events into Coinglass-style density clusters."""

    def __init__(self, cluster_step: int = 20, max_levels: int = 10):
        self.cluster_step = cluster_step
        self.max_levels = max_levels

    def analyze(self, current_price: float, liquidation_events: Optional[List[Dict]] = None) -> LiquidationAnalysis:
        if current_price <= 0:
            return LiquidationAnalysis([], [], None, None, 0.0, 0.0, "neutral", 0, 0.0)

        cluster_step = self._resolve_cluster_step(current_price)
        clusters: Dict[float, Dict[str, float]] = {}
        for event in liquidation_events or []:
            price = float(event.get("price", 0.0))
            size = float(event.get("size", 0.0))
            side = str(event.get("side", "")).lower()
            if price <= 0 or size <= 0:
                continue
            level = round(price / cluster_step) * cluster_step
            bucket = clusters.setdefault(level, {"size": 0.0, "hits": 0, "buy": 0.0, "sell": 0.0})
            bucket["size"] += size
            bucket["hits"] += 1
            if side == "buy":
                bucket["buy"] += size
            elif side == "sell":
                bucket["sell"] += size

        above: List[LiquidationCluster] = []
        below: List[LiquidationCluster] = []
        for level, info in clusters.items():
            distance_pct = abs(level - current_price) / current_price * 100
            side_bias = "shorts" if info["sell"] >= info["buy"] else "longs"
            cluster = LiquidationCluster(
                level=float(level),
                size=round(info["size"], 4),
                hits=int(info["hits"]),
                distance_pct=round(distance_pct, 4),
                side_bias=side_bias,
            )
            if level > current_price:
                above.append(cluster)
            elif level < current_price:
                below.append(cluster)

        above.sort(key=lambda item: item.distance_pct)
        below.sort(key=lambda item: item.distance_pct)
        strongest_above = max(above, key=lambda item: item.size) if above else None
        strongest_below = max(below, key=lambda item: item.size) if below else None

        signal = 0
        magnet = "neutral"
        target_level = 0.0
        target_density = 0.0
        distance_to_target_pct = 0.0
        if strongest_above and (not strongest_below or strongest_above.size >= strongest_below.size * 1.1):
            signal = 1
            magnet = "up"
            target_level = strongest_above.level
            target_density = strongest_above.size
            distance_to_target_pct = strongest_above.distance_pct
        elif strongest_below:
            signal = -1
            magnet = "down"
            target_level = strongest_below.level
            target_density = strongest_below.size
            distance_to_target_pct = strongest_below.distance_pct

        return LiquidationAnalysis(
            clusters_above=above[: self.max_levels],
            clusters_below=below[: self.max_levels],
            max_liq_cluster_above=strongest_above,
            max_liq_cluster_below=strongest_below,
            target_level=target_level,
            target_density=round(target_density, 4),
            magnet_direction=magnet,
            signal=signal,
            distance_to_target_pct=round(distance_to_target_pct, 4),
        )

    def _resolve_cluster_step(self, current_price: float) -> float:
        if current_price >= 1000:
            return max(float(self.cluster_step), 100.0)
        if current_price >= 100:
            return max(float(self.cluster_step), 20.0)
        if current_price >= 10:
            return 0.1
        if current_price >= 1:
            return 0.01
        if current_price >= 0.1:
            return 0.001
        return 0.0001

