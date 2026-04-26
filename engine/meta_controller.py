"""
Meta-Controller: режим рынка (TREND/RANGE/CHAOS), просадка по PnL-истории, множитель риска.
Источник: +Gemma.txt (без жёсткой привязки к pandas).
"""
from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, List, Optional


def _as_cfg_dict(cfg: Any) -> dict:
    if cfg is None:
        return {}
    if isinstance(cfg, dict):
        return cfg
    raw = getattr(cfg, "raw", None)
    if isinstance(raw, dict):
        return raw
    return {}


@dataclass
class MetaController:
    """Управляющий слой: режим + эвристическая просадка по последним сделкам (USDT PnL)."""

    pnl_history: Deque[float] = field(default_factory=lambda: deque(maxlen=200))
    winrate_window: Deque[float] = field(default_factory=lambda: deque(maxlen=50))
    chaos_volatility: float = 0.02
    trend_strength: float = 0.001
    max_entry_drawdown_abs: float = 0.10
    safe_drawdown_abs: float = 0.05
    market_regime: str = "UNKNOWN"
    mode: str = "NORMAL"
    # Накопленный PnL (USDT) и пик — для allow_trade
    _cum_pnl: float = 0.0
    _peak_cum: float = 0.0
    drawdown_abs: float = 0.0

    @classmethod
    def from_config(cls, cfg: Any) -> "MetaController":
        d = _as_cfg_dict(cfg).get("meta_controller") or {}
        return cls(
            chaos_volatility=float(d.get("chaos_volatility", 0.02)),
            trend_strength=float(d.get("trend_strength", 0.001)),
            max_entry_drawdown_abs=float(d.get("max_entry_drawdown_abs", 0.10)),
            safe_drawdown_abs=float(d.get("safe_drawdown_abs", 0.05)),
        )

    def detect_regime(self, closes: List[float]) -> str:
        """По ряду цен закрытия (старые → новые)."""
        if len(closes) < 5:
            self.market_regime = "RANGE"
            return self.market_regime
        rets: List[float] = []
        for i in range(1, len(closes)):
            a, b = closes[i - 1], closes[i]
            if a and a > 0:
                rets.append((b - a) / a)
        if not rets:
            self.market_regime = "RANGE"
            return self.market_regime
        vol = float(statistics.pstdev(rets)) if len(rets) > 1 else 0.0
        trend = abs(float(statistics.mean(rets)))
        if vol > self.chaos_volatility:
            self.market_regime = "CHAOS"
        elif trend > self.trend_strength:
            self.market_regime = "TREND"
        else:
            self.market_regime = "RANGE"
        return self.market_regime

    def update_performance(self, pnl_usdt: float) -> None:
        """Вызывать после каждой сделки (USDT, может быть отрицательным)."""
        self.pnl_history.append(float(pnl_usdt))
        wins = [x for x in self.pnl_history if x > 0.0]
        wr = (len(wins) / len(self.pnl_history)) if self.pnl_history else 0.0
        self.winrate_window.append(wr)
        self._cum_pnl += float(pnl_usdt)
        self._peak_cum = max(self._peak_cum, self._cum_pnl)
        # Просадка от пика в «абсолютных» нормализованных единицах (по доллару)
        if self._peak_cum > 0:
            self.drawdown_abs = max(0.0, (self._peak_cum - self._cum_pnl) / (self._peak_cum + 1e-9))
        else:
            self.drawdown_abs = 0.0
        if self._cum_pnl < 0 and self._peak_cum <= 0:
            self.drawdown_abs = min(1.0, abs(self._cum_pnl) / 1000.0)

    def decide_mode(self) -> str:
        avg_wr = float(statistics.mean(self.winrate_window)) if self.winrate_window else 0.0
        if self.drawdown_abs > self.safe_drawdown_abs:
            self.mode = "SAFE"
        elif avg_wr > 0.6 and len(self.winrate_window) >= 3:
            self.mode = "AGGRESSIVE"
        else:
            self.mode = "NORMAL"
        return self.mode

    def get_risk_multiplier(self) -> float:
        if self.mode == "SAFE":
            return 0.5
        if self.mode == "AGGRESSIVE":
            return 1.5
        return 1.0

    def allow_trade(self) -> bool:
        if self.drawdown_abs > self.max_entry_drawdown_abs:
            return False
        if self.market_regime == "CHAOS":
            return False
        return True
