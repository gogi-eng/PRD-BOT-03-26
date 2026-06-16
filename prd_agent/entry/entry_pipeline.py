"""
Guard pipeline → scoring: веса фильтров вместо только бинарного reject.
Режимы: strict / balanced / aggressive (связка с Telegram-пресетами 🛡/⚖️/🚀).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.signals.types import UnifiedSignal

MAX_SCORE = 8.0

MODE_THRESHOLDS = {
    "strict": 6.0,
    "balanced": 5.0,
    "aggressive": 4.0,
}

PRESET_TO_MODE = {
    "conservative": "strict",
    "normal": "balanced",
    "aggressive": "aggressive",
}


@dataclass
class PipelineResult:
    score: float
    max_score: float
    passed: bool
    reason: str
    size_mult: float
    breakdown: Dict[str, float]


def _pipeline_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    block = cfg.get("entry_pipeline", {})
    return block if isinstance(block, dict) else {}


def resolve_pipeline_mode(cfg: Dict[str, Any]) -> str:
    pc = _pipeline_cfg(cfg)
    mode = str(pc.get("mode", "") or "").strip().lower()
    if mode in MODE_THRESHOLDS:
        return mode
    meta = cfg.get("risk_presets_meta", {})
    if isinstance(meta, dict):
        preset = str(meta.get("active", "") or "").strip().lower()
        if preset in PRESET_TO_MODE:
            return PRESET_TO_MODE[preset]
    return "balanced"


def _rr_ratio(entry: float, sl: float, tp: float, side: str) -> float:
    if entry <= 0 or sl <= 0 or tp <= 0:
        return 0.0
    is_buy = side.lower() == "buy"
    risk = abs(entry - sl)
    reward = abs(tp - entry) if is_buy else abs(entry - tp)
    if risk <= 0:
        return 0.0
    return reward / risk


def evaluate_entry_pipeline(
    sig: UnifiedSignal,
    cfg: Dict[str, Any],
    *,
    entry: float = 0.0,
    sl: float = 0.0,
    tp: float = 0.0,
    has_zone: bool = False,
    has_bos: bool = False,
    supervisor_ok: bool = True,
    atr_pct: float = 0.0,
) -> PipelineResult:
    pc = _pipeline_cfg(cfg)
    if not bool(pc.get("enabled", True)):
        return PipelineResult(
            score=MAX_SCORE,
            max_score=MAX_SCORE,
            passed=True,
            reason="",
            size_mult=1.0,
            breakdown={},
        )

    mode = resolve_pipeline_mode(cfg)
    threshold = float(pc.get(f"{mode}_threshold", MODE_THRESHOLDS.get(mode, 5.0)))
    breakdown: Dict[str, float] = {}

    conf = float(sig.confidence)
    if conf >= 0.90:
        breakdown["confidence"] = 2.0
    elif conf >= 0.80:
        breakdown["confidence"] = 1.5
    elif conf >= 0.70:
        breakdown["confidence"] = 1.0
    else:
        breakdown["confidence"] = 0.0

    if has_zone and has_bos:
        breakdown["structure"] = 2.0
    elif has_zone or has_bos:
        breakdown["structure"] = 1.0
    else:
        breakdown["structure"] = 0.0

    rr = _rr_ratio(entry or float(sig.entry or 0), sl, tp, sig.side)
    qg = cfg.get("quality_gate", {}) if isinstance(cfg.get("quality_gate"), dict) else {}
    min_rr = float(qg.get("min_rr_ratio", 2.0))
    if rr >= min_rr + 0.3:
        breakdown["rr"] = 2.0
    elif rr >= min_rr:
        breakdown["rr"] = 1.5
    elif rr >= min_rr * 0.85:
        breakdown["rr"] = 0.5
    else:
        breakdown["rr"] = 0.0

    breakdown["supervisor"] = 1.0 if supervisor_ok else 0.0

    src = str(sig.source or "").lower()
    if src in ("hybrid", "telegram", "ta_volatility", "mirror_pump_dump_agent", "agent_world"):
        breakdown["source"] = 0.5
    elif src == "own_multi_agent":
        breakdown["source"] = 0.25
    else:
        breakdown["source"] = 0.0

    if atr_pct >= 0.003:
        breakdown["volatility"] = 1.0
    elif atr_pct >= 0.0015:
        breakdown["volatility"] = 0.5
    else:
        breakdown["volatility"] = 0.0

    score = sum(breakdown.values())
    passed = score >= threshold
    size_mult = 0.5 if mode == "aggressive" and passed else 1.0

    if passed:
        reason = f"entry_pipeline: {mode} score={score:.1f}/{MAX_SCORE:.0f} OK"
    else:
        weak = sorted(breakdown.items(), key=lambda x: x[1])[:2]
        weak_s = ", ".join(f"{k}={v:.1f}" for k, v in weak)
        reason = (
            f"entry_pipeline: {mode} score={score:.1f} < {threshold:.1f} "
            f"({weak_s})"
        )

    return PipelineResult(
        score=score,
        max_score=MAX_SCORE,
        passed=passed,
        reason=reason,
        size_mult=size_mult,
        breakdown=breakdown,
    )
