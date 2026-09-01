"""Ручной трейлинг → правила GARCH по режимам."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.positions.manual_trailing_garch_learner import ManualTrailingGarchLearner


def test_manual_sl_move_recorded(tmp_path: Path) -> None:
    cfg = {
        "_root": str(tmp_path),
        "volatility_regime_sizing": {"enabled": True, "min_bars": 80, "lookback_bars": 200},
        "positions": {
            "trailing_volatility_regime": {
                "enabled": True,
                "distance_mult": {"calm": 0.75, "normal": 1.0, "storm": 1.35},
                "clamp_min": 0.5,
                "clamp_max": 2.0,
            }
        },
        "manual_trailing_garch_learning": {
            "enabled": True,
            "min_sl_move_pct": 0.01,
            "min_profit_pct": 0.01,
            "min_samples_per_regime": 2,
            "state_path": "data/garch/manual_trailing_rules.json",
        },
    }
    learner = ManualTrailingGarchLearner(cfg, tmp_path / "data")
    klines = [{"close": 100.0 + i * 0.01, "open": 100.0, "high": 100.1, "low": 99.9} for i in range(120)]

    msg1 = learner.observe_exchange_sl(
        symbol="BTCUSDT",
        side="Buy",
        origin="manual",
        mark=101.0,
        exchange_sl=99.5,
        entry=100.0,
        klines=klines,
        bot_sent_sl=0.0,
        trailing_bot_enabled=False,
    )
    assert msg1 is None

    msg2 = learner.observe_exchange_sl(
        symbol="BTCUSDT",
        side="Buy",
        origin="manual",
        mark=102.0,
        exchange_sl=100.5,
        entry=100.0,
        klines=klines,
        bot_sent_sl=0.0,
        trailing_bot_enabled=False,
    )
    assert msg2 is not None
    assert "Trailing GARCH learn" in msg2

    state_path = tmp_path / "data" / "garch" / "manual_trailing_rules.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text(encoding="utf-8"))
    total_samples = sum(int(data["regimes"][k]["samples"]) for k in ("calm", "normal", "storm"))
    assert total_samples >= 1

    summary = learner.telegram_rules_summary()
    assert "GARCH" in summary
    assert "calm" in summary
