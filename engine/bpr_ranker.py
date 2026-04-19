#!/usr/bin/env python3
"""
BPR-style linear ranker for intra-cycle candidate ordering.

Uses a fixed feature vector derived from EntrySignal + metadata. Weights can be
fit offline from trade_history pairwise preferences (see scripts/train_bpr_ranker.py).
At runtime: score(feature) = bias + dot(weights, features) → softmax optional in caller.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

FEATURE_DIM = 10


def _grade_to_float(grade: str) -> float:
    g = (grade or "C").upper()
    if g == "A":
        return 1.0
    if g == "B":
        return 0.72
    return 0.45


def feature_vector_from_signal(signal: Any) -> List[float]:
    """Build normalized-ish features in [0,1] where possible."""
    m: Dict[str, Any] = dict(signal.metadata or {})
    comp = float(m.get("composite_score", signal.confidence or 0.0) or 0.0)
    tr = float(m.get("trend_score", 0.0) or 0.0)
    of = float(m.get("orderflow_score", 0.0) or 0.0)
    ai = float(m.get("ai_score", 0.0) or 0.0)
    conf = float(signal.confidence or 0.0)
    rr = min(max(float(signal.rr_ratio or 0.0), 0.0) / 6.0, 1.0)
    imb = float(m.get("normalized_imbalance", 0.0) or 0.0)
    imb_n = (imb + 1.0) * 0.5  # [-1,1] -> [0,1]
    sp = min(max(float(m.get("spread_pct", 0.0) or 0.0), 0.0), 0.5) / 0.5
    atr = min(max(float(m.get("atr_pct", 0.0) or 0.0) / 5.0, 0.0), 1.0)
    soft = 1.0 if bool(m.get("entry_soft_pass")) else 0.0
    grade = _grade_to_float(str(m.get("signal_grade", signal.grade or "C")))
    vec = [comp, tr, of, ai, conf, rr, imb_n, sp, atr, soft * 0.15 + grade * 0.85]
    if len(vec) != FEATURE_DIM:
        raise ValueError(f"BPR feature dim mismatch: {len(vec)} != {FEATURE_DIM}")
    return vec


class BPRLinearRanker:
    """Linear scorer w·x + b; weights from JSON (offline BPR-style training)."""

    def __init__(
        self,
        enabled: bool = False,
        weights_path: str = "bpr_weights.json",
        blend_weight: float = 0.35,
        top1_when_multiple: bool = True,
        telegram_top_n: int = 0,
        bot_dir: Optional[Path] = None,
    ):
        self.enabled = bool(enabled)
        self.weights_path = weights_path
        self.blend_weight = max(0.0, min(1.0, float(blend_weight)))
        self.top1_when_multiple = bool(top1_when_multiple)
        self.telegram_top_n = max(0, int(telegram_top_n))
        self.bot_dir = Path(bot_dir) if bot_dir else Path(".")
        self.weights: List[float] = [0.0] * FEATURE_DIM
        self.bias: float = 0.0
        self._load_weights()

    def _resolve_path(self) -> Path:
        p = Path(self.weights_path)
        if p.is_absolute():
            return p
        return (self.bot_dir / p).resolve()

    def _load_weights(self) -> None:
        path = self._resolve_path()
        if not path.exists():
            # Sensible prior: emphasize composite + flow, slight soft-pass bonus channel.
            self.weights = [0.42, 0.12, 0.18, 0.10, 0.12, 0.04, 0.02, 0.0, 0.0, 0.02]
            self.bias = 0.0
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            w = data.get("weights") or []
            if len(w) == FEATURE_DIM:
                self.weights = [float(x) for x in w]
            else:
                self.weights = [0.42, 0.12, 0.18, 0.10, 0.12, 0.04, 0.02, 0.0, 0.0, 0.02]
            self.bias = float(data.get("bias", 0.0))
        except Exception:
            self.weights = [0.42, 0.12, 0.18, 0.10, 0.12, 0.04, 0.02, 0.0, 0.0, 0.02]
            self.bias = 0.0

    def score(self, features: Sequence[float]) -> float:
        if len(features) != FEATURE_DIM:
            return 0.0
        s = self.bias
        for wi, xi in zip(self.weights, features):
            s += wi * float(xi)
        return float(1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, s)))))  # squash to (0,1)

    def features_from_signal(self, signal: EntrySignal) -> List[float]:
        return feature_vector_from_signal(signal)

    def annotate_candidates(self, candidates: List[Dict]) -> None:
        """Mutates each candidate dict with bpr_score and blended signal_strength."""
        if not self.enabled or not candidates:
            return
        for c in candidates:
            sig = c["signal"]
            vec = self.features_from_signal(sig)
            bpr = self.score(vec)
            c["bpr_score"] = round(bpr, 6)
            sig.metadata["bpr_score"] = c["bpr_score"]
            sig.metadata["bpr_features"] = [round(x, 4) for x in vec]
            base = float(c.get("signal_strength", 0.0) or 0.0)
            blend = self.blend_weight
            c["signal_strength"] = round((1.0 - blend) * base + blend * bpr, 6)

    def maybe_take_top1(self, ranked: List[Dict]) -> List[Dict]:
        if not self.enabled or not self.top1_when_multiple or len(ranked) < 2:
            return ranked
        sorted_rank = sorted(
            ranked,
            key=lambda it: float(it.get("bpr_score", it["signal"].metadata.get("bpr_score", 0.0)) or 0.0),
            reverse=True,
        )
        return sorted_rank[:1]
