"""BPR linear ranker smoke tests."""
from __future__ import annotations

from pathlib import Path

from engine.bpr_ranker import BPRLinearRanker, feature_vector_from_signal
from engine.entry_engine import EntrySignal


def _sig(conf: float, soft: bool = False) -> EntrySignal:
    s = EntrySignal()
    s.should_enter = True
    s.side = "BUY"
    s.confidence = conf
    s.rr_ratio = 3.0
    s.grade = "B"
    s.metadata = {
        "composite_score": conf,
        "trend_score": 0.5,
        "orderflow_score": 0.6,
        "ai_score": 0.55,
        "normalized_imbalance": 0.1,
        "spread_pct": 0.02,
        "atr_pct": 1.0,
        "signal_grade": "B",
        "entry_soft_pass": soft,
    }
    return s


def test_feature_vector_dim():
    v = feature_vector_from_signal(_sig(0.7))
    assert len(v) == 10


def test_annotate_candidates_changes_strength():
    root = Path(__file__).resolve().parents[2]
    bpr = BPRLinearRanker(enabled=True, weights_path="bpr_weights.json", bot_dir=root, telegram_top_n=0)
    cands = [
        {"symbol": "A", "signal": _sig(0.71), "signal_strength": 0.71, "liquidity": 1e6, "volatility": 0.01, "spread": 0.0001},
        {"symbol": "B", "signal": _sig(0.69), "signal_strength": 0.69, "liquidity": 1e6, "volatility": 0.01, "spread": 0.0001},
    ]
    before = [c["signal_strength"] for c in cands]
    bpr.annotate_candidates(cands)
    after = [c["signal_strength"] for c in cands]
    assert after != before
    assert "bpr_score" in cands[0]


def test_maybe_take_top1():
    root = Path(__file__).resolve().parents[2]
    bpr = BPRLinearRanker(enabled=True, top1_when_multiple=True, weights_path="bpr_weights.json", bot_dir=root)
    ranked = [
        {"symbol": "X", "signal": _sig(0.8), "bpr_score": 0.2, "signal_strength": 0.9, "liquidity": 1.0, "volatility": 0.01, "spread": 0.0},
        {"symbol": "Y", "signal": _sig(0.75), "bpr_score": 0.9, "signal_strength": 0.8, "liquidity": 1.0, "volatility": 0.01, "spread": 0.0},
    ]
    out = bpr.maybe_take_top1(ranked)
    assert len(out) == 1
    assert out[0]["symbol"] == "Y"
