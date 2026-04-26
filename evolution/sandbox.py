#!/usr/bin/env python3
"""Paper / micro-capital tracking with min-trades gate and kill-switch."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SandboxStats:
    pnl: float = 0.0
    trades: int = 0
    equity: float = 0.0
    peak_equity: float = 0.0
    max_dd_frac: float = 0.0
    consecutive_losses: int = 0
    retired: bool = False
    retire_reason: str = ""


class Sandbox:
    def __init__(
        self,
        min_trades_for_score: int = 20,
        max_sandbox_dd: float = 0.15,
        max_consecutive_losses: int = 6,
    ):
        self.min_trades_for_score = max(1, int(min_trades_for_score))
        self.max_sandbox_dd = float(max_sandbox_dd)
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.live: Dict[int, SandboxStats] = {}

    def deploy(self, genome) -> None:
        gid = int(genome.id)
        if gid not in self.live:
            self.live[gid] = SandboxStats()

    def update(self, genome_id: int, pnl: float) -> None:
        s = self.live.get(int(genome_id))
        if not s or s.retired:
            return
        s.trades += 1
        s.pnl += float(pnl)
        s.equity += float(pnl)
        s.peak_equity = max(s.peak_equity, s.equity)
        dd_amt = s.peak_equity - s.equity
        if s.peak_equity > 1e-9:
            s.max_dd_frac = max(s.max_dd_frac, dd_amt / s.peak_equity)

        if pnl < 0:
            s.consecutive_losses += 1
        else:
            s.consecutive_losses = 0

        if s.max_dd_frac >= self.max_sandbox_dd:
            s.retired = True
            s.retire_reason = "sandbox_max_dd"
        if s.consecutive_losses >= self.max_consecutive_losses:
            s.retired = True
            s.retire_reason = "sandbox_loss_streak"

    def score(self, genome_id: int) -> Optional[float]:
        s = self.live.get(int(genome_id))
        if not s or s.retired:
            return None
        if s.trades < self.min_trades_for_score:
            return None
        return float(s.pnl)

    def is_retired(self, genome_id: int) -> bool:
        s = self.live.get(int(genome_id))
        return bool(s and s.retired)

    def snapshot(self, genome_id: int) -> Optional[Dict[str, Any]]:
        s = self.live.get(int(genome_id))
        if not s:
            return None
        return {
            "pnl": s.pnl,
            "trades": s.trades,
            "max_dd_frac": s.max_dd_frac,
            "retired": s.retired,
            "retire_reason": s.retire_reason,
        }
