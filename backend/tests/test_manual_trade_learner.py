import json
from datetime import datetime, timedelta, timezone

from engine.entry_engine import EntrySignal
from engine.manual_trade_learner import ManualTradeLearner


class DummyConfig:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, *keys, default=None):
        node = self.values
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


def test_manual_trade_learner_boosts_matching_bot_signal(tmp_path):
    history = [
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry": 100.0,
            "exit": 103.0,
            "qty": 1.0,
            "pnl": 3.0,
            "pnl_pct": 3.0,
            "origin": "manual",
            "reason": "manual_take_profit",
        },
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": "BTCUSDT",
            "side": "BUY",
            "entry": 100.0,
            "exit": 102.0,
            "qty": 1.0,
            "pnl": 2.0,
            "pnl_pct": 2.0,
            "origin": "manual",
            "reason": "manual_take_profit",
        },
    ]
    (tmp_path / "trade_history.json").write_text(json.dumps(history), encoding="utf-8")
    cfg = DummyConfig(
        {
            "timezone_offset": 3,
            "manual_trade_learning": {
                "enabled": True,
                "min_manual_winners": 2,
                "min_match_score": 0.55,
                "max_confidence_boost": 0.05,
            },
        }
    )
    learner = ManualTradeLearner(tmp_path, cfg)
    signal = EntrySignal(should_enter=True, side="BUY", confidence=0.80, rr_ratio=2.0, capital_score=1.60)

    match = learner.apply_to_signal("BTCUSDT", signal)

    assert match is not None
    assert signal.confidence > 0.80
    assert signal.capital_score > 1.60
    assert "manual_learning_match" in signal.metadata


def test_manual_trade_learner_ignores_non_matching_side(tmp_path):
    history = [
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": "ETHUSDT",
            "side": "BUY",
            "entry": 100.0,
            "exit": 103.0,
            "qty": 1.0,
            "pnl": 3.0,
            "pnl_pct": 3.0,
            "origin": "manual",
            "reason": "manual_take_profit",
        },
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": "ETHUSDT",
            "side": "BUY",
            "entry": 100.0,
            "exit": 102.0,
            "qty": 1.0,
            "pnl": 2.0,
            "pnl_pct": 2.0,
            "origin": "manual",
            "reason": "manual_take_profit",
        },
    ]
    (tmp_path / "trade_history.json").write_text(json.dumps(history), encoding="utf-8")
    learner = ManualTradeLearner(tmp_path, DummyConfig({"manual_trade_learning": {"enabled": True}}))
    signal = EntrySignal(should_enter=True, side="SELL", confidence=0.80, rr_ratio=2.0, capital_score=1.60)

    match = learner.apply_to_signal("ETHUSDT", signal)

    assert match is None
    assert signal.confidence == 0.80
    assert "manual_learning_match" not in signal.metadata


def test_manual_trade_learner_profiles_entry_context_and_ignores_stale_entry_time(tmp_path):
    close_time = datetime.now(timezone.utc)
    stale_entry_time = close_time - timedelta(days=90)
    history = [
        {
            "time": close_time.isoformat(),
            "entry_time": stale_entry_time.isoformat(),
            "symbol": "OPUSDT",
            "side": "SELL",
            "entry": 1.0,
            "exit": 0.97,
            "qty": 10.0,
            "pnl": 3.0,
            "pnl_pct": 3.0,
            "origin": "manual",
            "reason": "manual_take_profit",
            "entry_context": {
                "regime": "trend",
                "trend": "down",
                "htf_trend": "down",
                "entry_zone": "fvg",
                "atr_pct": 0.31,
                "adx": 31,
                "normalized_imbalance": -0.41,
            },
        },
        {
            "time": close_time.isoformat(),
            "symbol": "XNYUSDT",
            "side": "SELL",
            "entry": 1.0,
            "exit": 0.98,
            "qty": 10.0,
            "pnl": 2.0,
            "pnl_pct": 2.0,
            "origin": "manual",
            "reason": "manual_take_profit",
            "entry_context": {
                "regime": "trend",
                "trend": "down",
                "htf_trend": "down",
                "entry_zone": "fvg",
                "atr_pct": 0.28,
                "adx": 29,
                "normalized_imbalance": -0.35,
            },
        },
    ]
    (tmp_path / "trade_history.json").write_text(json.dumps(history), encoding="utf-8")
    learner = ManualTradeLearner(
        tmp_path,
        DummyConfig(
            {
                "timezone_offset": 3,
                "manual_trade_learning": {
                    "enabled": True,
                    "min_manual_winners": 2,
                    "min_match_score": 0.20,
                    "max_entry_age_days": 7,
                    "context_weight": 0.20,
                },
            }
        ),
    )
    signal = EntrySignal(should_enter=True, side="SELL", confidence=0.80, rr_ratio=2.0, capital_score=1.60)
    signal.metadata.update(
        {
            "regime": "trend",
            "trend": "down",
            "htf_trend": "down",
            "entry_zone": "fvg",
            "atr_pct": 0.30,
            "adx": 30,
            "normalized_imbalance": -0.40,
        }
    )

    match = learner.apply_to_signal("OPUSDT", signal)

    assert match is not None
    state = json.loads((tmp_path / "manual_trade_learning_state.json").read_text(encoding="utf-8"))
    assert state["stale_entry_times_ignored"] == 1
    assert "regime:trend" in state["by_context"]
    assert "atr_pct:atr_normal" in state["by_context"]
    assert "adx:adx_strong" in state["by_context"]
    assert "imbalance:imb_strong" in state["by_context"]
    assert signal.metadata["manual_learning_match"]["profile"]["context_hits"]
