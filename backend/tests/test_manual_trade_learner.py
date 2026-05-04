import json
from datetime import datetime, timezone

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
