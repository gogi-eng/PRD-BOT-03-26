"""
Фильтр деривативов перед входом: funding, OI spike, long/short ratio (Bybit v5).
Apodex Priority-1 / AGENT-WORLD — без смены базовой SMC-стратегии.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from analysis.funding_filter import FundingFilter, FundingSignal

logger = logging.getLogger("prd_agent.derivatives_guard")


class DerivativesEntryGuard:
    def __init__(self, cfg: Dict[str, Any]):
        block = cfg.get("derivatives_entry_guard", {})
        if not isinstance(block, dict):
            block = {}
        self.enabled = bool(block.get("enabled", False))
        self.advisory_only = bool(block.get("advisory_only", False))
        self.block_on_extreme_funding = bool(block.get("block_on_extreme_funding", True))
        self.block_on_crowded_funding = bool(block.get("block_on_crowded_funding", False))
        self.oi_spike_pct = float(block.get("oi_spike_pct_threshold", 5.0)) / 100.0
        self.oi_spike_block_with_crowd = bool(block.get("oi_spike_block_with_crowd", True))
        lsr = block.get("long_short_ratio", {})
        if not isinstance(lsr, dict):
            lsr = {}
        self.lsr_enabled = bool(lsr.get("enabled", True))
        self.lsr_block = bool(lsr.get("block_on_crowd", True))
        self.lsr_long_crowded = float(lsr.get("long_crowded_buy_ratio", 0.72))
        self.lsr_short_crowded = float(lsr.get("short_crowded_buy_ratio", 0.28))
        self.lsr_period = str(lsr.get("period", "1h"))
        self._funding = FundingFilter(
            high_threshold=float(block.get("high_funding_threshold", 0.0005)),
            extreme_threshold=float(block.get("extreme_funding_threshold", 0.001)),
            oi_significant=float(block.get("oi_significant_pct", 5.0)) / 100.0,
        )

    @staticmethod
    def _is_long(side: str) -> bool:
        return str(side or "").upper() in ("BUY", "LONG")

    async def _fetch_lsr(self, exchange: Any, symbol: str) -> float:
        if not self.lsr_enabled or not hasattr(exchange, "get_long_short_ratio"):
            return 0.5
        try:
            rows = await exchange.get_long_short_ratio(
                symbol, period=self.lsr_period, limit=3
            )
            if not rows:
                return 0.5
            return float(rows[0].get("buyRatio", 0.5) or 0.5)
        except Exception as exc:
            logger.warning("LSR %s: %s", symbol, exc)
            return 0.5

    def _funding_blocks(self, fs: FundingSignal, is_long: bool) -> Tuple[bool, str]:
        if fs.sentiment in ("extreme_long", "extreme_short"):
            if fs.strength >= 0.8 and self.block_on_extreme_funding:
                blocked, reason = self._funding.should_filter_entry(
                    fs, "Buy" if is_long else "Sell"
                )
                if blocked:
                    return True, reason
        if fs.sentiment in ("crowded_long", "crowded_short") and self.block_on_crowded_funding:
            blocked, reason = self._funding.should_filter_entry(
                fs, "Buy" if is_long else "Sell"
            )
            if blocked and fs.strength >= 0.5:
                return True, reason
        return False, ""

    def _oi_crowd_blocks(self, fs: FundingSignal, is_long: bool) -> Tuple[bool, str]:
        if not self.oi_spike_block_with_crowd:
            return False, ""
        if fs.oi_change_pct < self.oi_spike_pct:
            return False, ""
        if is_long and fs.sentiment in ("extreme_long", "crowded_long"):
            return True, (
                f"derivatives_oi_crowd: OI +{fs.oi_change_pct * 100:.1f}% "
                f"with {fs.sentiment}"
            )
        if not is_long and fs.sentiment in ("extreme_short", "crowded_short"):
            return True, (
                f"derivatives_oi_crowd: OI +{fs.oi_change_pct * 100:.1f}% "
                f"with {fs.sentiment}"
            )
        return False, ""

    def _lsr_blocks(self, buy_ratio: float, is_long: bool) -> Tuple[bool, str]:
        if not self.lsr_enabled or not self.lsr_block:
            return False, ""
        if is_long and buy_ratio >= self.lsr_long_crowded:
            return True, f"derivatives_lsr: buyRatio={buy_ratio:.3f} crowded long"
        if not is_long and buy_ratio <= self.lsr_short_crowded:
            return True, f"derivatives_lsr: buyRatio={buy_ratio:.3f} crowded short"
        return False, ""

    async def check(self, exchange: Any, symbol: str, side: str) -> Tuple[bool, str]:
        """Returns (allowed, reason). allowed=False → пропуск входа."""
        if not self.enabled:
            return True, ""
        sym = str(symbol or "").upper()
        is_long = self._is_long(side)
        try:
            fs = await self._funding.analyze(exchange, sym)
        except Exception as exc:
            logger.warning("derivatives funding %s: %s", sym, exc)
            return True, ""

        blocked, reason = self._funding_blocks(fs, is_long)
        if blocked:
            if self.advisory_only:
                logger.info("derivatives advisory %s: %s", sym, reason)
                return True, ""
            return False, reason or fs.reason

        blocked, reason = self._oi_crowd_blocks(fs, is_long)
        if blocked:
            if self.advisory_only:
                logger.info("derivatives advisory %s: %s", sym, reason)
                return True, ""
            return False, reason

        buy_ratio = await self._fetch_lsr(exchange, sym)
        blocked, reason = self._lsr_blocks(buy_ratio, is_long)
        if blocked:
            if self.advisory_only:
                logger.info("derivatives advisory %s: %s", sym, reason)
                return True, ""
            return False, reason

        return True, ""
