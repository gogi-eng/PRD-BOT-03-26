#!/usr/bin/env python3
"""Local always-on advisor for entry sanity checks."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class AdvisorDecision:
    allow: bool
    confidence: float
    reason: str
    score: float
    checks: Dict[str, Any]


class LocalTradingAdvisor:
    """
    Deterministic advisor that continuously reviews trade candidates.

    Modes:
      - advisory: logs only, does not block entries
      - enforce: blocks entries that fail advisor checks
      - disabled: bypasses advisor
    """

    def __init__(self, cfg: Dict[str, Any] | None = None):
        c = cfg or {}
        self.enabled = bool(c.get("enabled", True))
        self.mode = str(c.get("mode", "enforce")).lower()
        if self.mode not in {"enforce", "advisory", "disabled"}:
            self.mode = "enforce"
        if self.mode == "disabled":
            self.enabled = False

        self.min_rr = float(c.get("min_rr", 1.8))
        self.min_confidence = float(c.get("min_confidence", 0.62))
        self.min_edge = float(c.get("min_edge", 0.45))
        self.max_spread_pct = float(c.get("max_spread_pct", 0.12))
        self.min_atr_pct = float(c.get("min_atr_pct", 0.03))
        self.min_abs_imbalance = float(c.get("min_abs_imbalance", 0.06))
        self.allow_countertrend = bool(c.get("allow_countertrend", False))
        self.allow_chop = bool(c.get("allow_chop", True))

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def evaluate(self, symbol: str, signal, market=None) -> AdvisorDecision:
        if not self.enabled:
            return AdvisorDecision(True, 1.0, "advisor_disabled", 1.0, {"mode": "disabled"})

        confidence = self._to_float(getattr(signal, "confidence", 0.0))
        rr_ratio = self._to_float(getattr(signal, "rr_ratio", 0.0))
        side = str(getattr(signal, "side", "") or "").upper()
        md = dict(getattr(signal, "metadata", {}) or {})

        trained_prob = md.get("trained_model_prob")
        base_prob = self._to_float(trained_prob, confidence) if trained_prob is not None else confidence
        expected_edge = base_prob * (rr_ratio + 1.0) - 1.0

        spread_pct = self._to_float(md.get("spread_pct", 0.0))
        atr_pct = self._to_float(md.get("atr_pct", 0.0))
        abs_imb = abs(self._to_float(md.get("normalized_imbalance", 0.0)))
        regime = str(md.get("regime", "unknown")).lower()
        htf_4h_trend = self._to_int(md.get("htf_4h_trend", 0))
        entry_zone = str(md.get("entry_zone", "no_zone")).lower()

        countertrend = (
            (side == "BUY" and htf_4h_trend < 0)
            or (side == "SELL" and htf_4h_trend > 0)
        )

        checks = {
            "min_confidence": confidence >= self.min_confidence,
            "min_rr": rr_ratio >= self.min_rr,
            "min_edge": expected_edge >= self.min_edge,
            "spread_ok": spread_pct <= self.max_spread_pct,
            "atr_ok": atr_pct >= self.min_atr_pct,
            "imbalance_ok": abs_imb >= self.min_abs_imbalance,
            "regime_ok": (self.allow_chop or regime != "chop"),
            "countertrend_ok": (self.allow_countertrend or not countertrend),
            "has_zone_or_high_conf": (entry_zone != "no_zone" or confidence >= 0.85),
        }

        passed = sum(1 for ok in checks.values() if ok)
        score = passed / max(len(checks), 1)

        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            reason = f"advisor_blocked: {', '.join(failed[:3])}"
            allow = False
        else:
            reason = "advisor_ok"
            allow = True

        return AdvisorDecision(
            allow=allow,
            confidence=confidence,
            reason=reason,
            score=round(score, 4),
            checks=checks,
        )
