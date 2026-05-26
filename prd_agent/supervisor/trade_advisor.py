"""
Советник супервизора: оценка качества сигнала и выбор плеча 20–50x.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from prd_agent.risk.dynamic_leverage import (
    DynamicLeverageSettings,
    load_dynamic_leverage_settings,
    resolve_trade_leverage,
)
from prd_agent.signals.types import UnifiedSignal


@dataclass
class LeverageAdvice:
    leverage: int
    score: float
    reason: str
    checks: Dict[str, Any] = field(default_factory=dict)


class TradeAdvisor:
    """
    Локальный советник (аналог analysis/advisor.py), решает плечо перед ордером.
    Использует confidence, RR, проверки качества и статистику виртуальных сделок.
    """

    def __init__(self, cfg: Dict[str, Any]):
        sup = cfg.get("trade_supervisor", {})
        if not isinstance(sup, dict):
            sup = {}
        adv = sup.get("leverage_advisor", {})
        if not isinstance(adv, dict):
            adv = {}
        legacy = cfg.get("advisor", {})
        if isinstance(legacy, dict):
            for k, v in legacy.items():
                adv.setdefault(k, v)

        self.enabled = bool(adv.get("enabled", True))
        self._lev = load_dynamic_leverage_settings(cfg)
        if not self._lev.enabled:
            self._lev = DynamicLeverageSettings(
                enabled=True,
                min_leverage=self._lev.min_leverage,
                max_leverage=self._lev.max_leverage,
                min_confidence=self._lev.min_confidence,
                max_confidence=self._lev.max_confidence,
                fallback_leverage=self._lev.fallback_leverage,
            )

        self.min_rr = float(adv.get("min_rr", 1.8))
        self.min_confidence = float(adv.get("min_confidence", self._lev.min_confidence))
        self.min_edge = float(adv.get("min_edge", 0.35))
        self.allow_countertrend = bool(adv.get("allow_countertrend", False))
        self.allow_chop = bool(adv.get("allow_chop", True))
        self.confidence_weight = float(adv.get("confidence_weight", 0.45))
        self.checks_weight = float(adv.get("checks_weight", 0.35))
        self.rr_weight = float(adv.get("rr_weight", 0.20))
        self.virtual_wr_soft_cap = float(adv.get("virtual_wr_soft_cap", 40.0))
        self.virtual_wr_boost_cap = float(adv.get("virtual_wr_boost_cap", 55.0))

    @staticmethod
    def _f(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _i(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _rr_ratio(entry: float, sl: float, tp: float, side: str) -> float:
        if entry <= 0 or sl <= 0 or tp <= 0:
            return 0.0
        risk = abs(entry - sl)
        if risk <= 0:
            return 0.0
        reward = abs(tp - entry) if str(side).upper() == "BUY" else abs(entry - tp)
        return reward / risk if reward > 0 else 0.0

    def _entry_checks(
        self,
        sig: UnifiedSignal,
        *,
        rr_ratio: float,
        md: Dict[str, Any],
    ) -> Dict[str, bool]:
        confidence = float(sig.confidence)
        side = str(sig.side or "").upper()
        spread_pct = self._f(md.get("spread_pct", 0.0))
        atr_pct = self._f(md.get("atr_pct", 0.0))
        abs_imb = abs(self._f(md.get("normalized_imbalance", 0.0)))
        regime = str(md.get("regime", "unknown")).lower()
        htf_4h_trend = self._i(md.get("htf_4h_trend", 0))
        entry_zone = str(md.get("entry_zone", "no_zone")).lower()
        side_u = side.upper()
        countertrend = (side_u == "BUY" and htf_4h_trend < 0) or (
            side_u == "SELL" and htf_4h_trend > 0
        )
        trained_prob = md.get("trained_model_prob")
        base_prob = self._f(trained_prob, confidence) if trained_prob is not None else confidence
        expected_edge = base_prob * (rr_ratio + 1.0) - 1.0

        return {
            "min_confidence": confidence >= self.min_confidence,
            "min_rr": rr_ratio >= self.min_rr,
            "min_edge": expected_edge >= self.min_edge,
            "spread_ok": spread_pct <= 0.15 or spread_pct <= 0,
            "atr_ok": atr_pct >= 0.02 or atr_pct <= 0,
            "imbalance_ok": abs_imb >= 0.04 or abs_imb <= 0,
            "regime_ok": self.allow_chop or regime != "chop",
            "countertrend_ok": self.allow_countertrend or not countertrend,
            "has_zone_or_high_conf": entry_zone != "no_zone" or confidence >= 0.85,
        }

    def recommend_leverage(
        self,
        sig: UnifiedSignal,
        *,
        entry: float,
        stop_loss: float,
        take_profit: float,
        virtual_stats: Optional[Dict[str, Any]] = None,
    ) -> LeverageAdvice:
        if not self.enabled:
            lev = self._lev.fallback_leverage
            return LeverageAdvice(lev, 1.0, "advisor_disabled", {"mode": "disabled"})

        md = dict(sig.raw or {})
        rr = self._rr_ratio(entry, stop_loss, take_profit, sig.side)
        if rr <= 0 and sig.stop_loss and sig.take_profit and sig.entry:
            rr = self._rr_ratio(sig.entry, sig.stop_loss, sig.take_profit, sig.side)

        checks = self._entry_checks(sig, rr_ratio=rr, md=md)
        passed = sum(1 for ok in checks.values() if ok)
        check_score = passed / max(len(checks), 1)

        conf_score = max(
            0.0,
            min(
                1.0,
                (float(sig.confidence) - self._lev.min_confidence)
                / max(self._lev.max_confidence - self._lev.min_confidence, 1e-6),
            ),
        )
        rr_score = max(0.0, min(1.0, rr / 3.0))

        w_sum = self.confidence_weight + self.checks_weight + self.rr_weight
        if w_sum <= 0:
            w_sum = 1.0
        combined = (
            self.confidence_weight * conf_score
            + self.checks_weight * check_score
            + self.rr_weight * rr_score
        ) / w_sum

        vstats = virtual_stats or {}
        v_wr = float(vstats.get("win_rate_pct", 0) or 0)
        v_n = int(vstats.get("closed", 0) or 0)
        if v_n >= 8 and v_wr < self.virtual_wr_soft_cap:
            combined *= max(0.65, v_wr / self.virtual_wr_soft_cap)
        elif v_n >= 8 and v_wr >= self.virtual_wr_boost_cap:
            combined = min(1.0, combined * 1.05)

        leverage = resolve_trade_leverage(combined, self._lev)
        failed = [name for name, ok in checks.items() if not ok]
        reason = (
            f"advisor score={combined:.2f} conf={sig.confidence:.0%} "
            f"RR={rr:.2f} checks={passed}/{len(checks)}"
        )
        if failed:
            reason += f" | слабые: {', '.join(failed[:3])}"

        return LeverageAdvice(
            leverage=leverage,
            score=round(combined, 4),
            reason=reason,
            checks={**checks, "rr_ratio": round(rr, 3), "virtual_wr_24h": v_wr},
        )
