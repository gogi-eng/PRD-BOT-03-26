"""Smoke tests for +Gemma meta / RL / hybrid engine modules."""
from __future__ import annotations

from engine.hybrid_voter import HybridVoter
from engine.meta_controller import MetaController
from engine.rl_meta_controller import (
    ACTION_RISK_MULT,
    RuleRLMetaController,
    state_from_meta_ohlcv,
)


def test_meta_chaos_blocks_trade():
    m = MetaController()
    m.detect_regime([100.0, 200.0, 50.0, 80.0, 120.0, 20.0])  # high vol
    assert m.market_regime == "CHAOS"
    assert m.allow_trade() is False


def test_hybrid_decide():
    h = HybridVoter(threshold=0.2, weights={"xgb": 0.4, "gemma": 0.3, "ta": 0.3})
    v = h.vote({"xgb": 0.5, "ta": 0.4, "gemma": 0.3})
    assert h.decide(v) == "LONG"
    st = h.decide(h.vote({"xgb": 0.0, "ta": 0.0, "gemma": 0.0}))
    assert st == "NO_TRADE"


def test_rl_state_and_action_risk_map():
    st = state_from_meta_ohlcv(0.01, 0.0, 60.0, [100.0, 100.1, 100.0, 100.2, 100.1] * 5, "TREND", 0.55)
    assert len(st) == 7
    r = RuleRLMetaController()
    a = r.act(st)
    assert 0 <= a <= 3
    assert isinstance(ACTION_RISK_MULT[a], float)
