"""Тесты Hermes briefing и классификации рекомендаций."""
from __future__ import annotations

import json
from pathlib import Path

from prd_agent.analysis.hermes_briefing import (
    build_hermes_telegram_briefing,
    classify_skip_bucket,
    classify_weight_recommendation,
    load_winning_entry_rules,
)
from prd_agent.entry.entry_soft_rules import RULE_POINTS, compute_soft_score


def test_classify_safety_limits():
    assert classify_skip_bucket("на бирже уже открыта позиция zecusdt") == "safety"
    assert classify_skip_bucket("дневной лимит убытка $35.89 (лимит $15).") == "safety"
    assert classify_skip_bucket("лимит сделок на сегодня. сброс через 1ч") == "safety"
    assert classify_skip_bucket("supervisor") == "safety"


def test_classify_tunable_filters():
    assert classify_skip_bucket("entry_guard") == "tunable"
    assert classify_skip_bucket("impulse_retest") == "tunable"
    assert classify_weight_recommendation("skip:entry_guard") == "tunable"


def test_soft_rules_penalties_updated():
    assert RULE_POINTS["regime_chop"] == -15.0
    assert RULE_POINTS["spread_wide"] == -10.0
    assert RULE_POINTS["atr_sweet"] == 5.0


def test_soft_score_chop_penalty():
    ctx = {"regime": "chop", "adx": 20, "atr_pct": 0.5, "local_hour": 12}
    res_chop = compute_soft_score(ctx, side="BUY", cfg={"timezone_offset": 3})
    ctx_trend = dict(ctx)
    ctx_trend["regime"] = "trend"
    res_trend = compute_soft_score(ctx_trend, side="BUY", cfg={"timezone_offset": 3})
    assert "regime_chop" in res_chop.active_rules
    assert res_chop.score < res_trend.score


def test_soft_weight_overrides_weaken_negative_lift_rules():
    """Hermes: ослабить regime_trend/adx/imb через weight_overrides (< 1)."""
    ctx = {
        "regime": "trend",
        "adx": 30,
        "atr_pct": 0.5,
        "normalized_imbalance": 0.35,
        "local_hour": 12,
    }
    base = compute_soft_score(ctx, side="BUY", cfg={"timezone_offset": 3})
    weak_cfg = {
        "timezone_offset": 3,
        "rule_weight_learning": {
            "weight_overrides": {
                "regime_trend": 0.55,
                "adx_ok": 0.55,
                "adx_strong": 0.55,
                "imb_strong": 0.55,
            }
        },
    }
    weak = compute_soft_score(ctx, side="BUY", cfg=weak_cfg)
    assert weak.score < base.score
    assert weak.breakdown["regime_trend"] < base.breakdown["regime_trend"]
    assert abs(weak.breakdown["regime_trend"] - RULE_POINTS["regime_trend"] * 0.55) < 0.01


def test_build_briefing_from_fixture(tmp_path: Path):
    rules = {
        "hours": 72,
        "generated_at": "2026-07-02T12:00:00+00:00",
        "outcome_counts": {"profit": 10, "loss": 5, "neutral": 20},
        "tp_winners": 10,
        "tp_skipped_virtual": 6,
        "tp_opened_real": 4,
        "weight_recommendations": [
            {
                "filter_id": "skip:entry_guard",
                "action": "consider_remove",
                "confidence": "high",
                "n_samples": 21,
                "reason_ru": "тест",
            },
            {
                "filter_id": "skip:дневной лимит убытка $15",
                "action": "consider_remove",
                "confidence": "high",
                "n_samples": 25,
                "reason_ru": "тест",
            },
        ],
        "skip_filter_reviews": [
            {
                "skip_bucket": "supervisor",
                "recommendation": "keep_strict",
                "n_virtual_loss": 100,
            }
        ],
        "rules": [{"description_ru": "local_hour >= 4"}],
        "filter_impacts": [{"filter_id": "regime_chop", "win_rate_pct": 33, "lift_pct": -12}],
    }
    hermes_dir = tmp_path / "data" / "hermes"
    hermes_dir.mkdir(parents=True)
    (hermes_dir / "winning_entry_rules.json").write_text(
        json.dumps(rules), encoding="utf-8"
    )
    text = build_hermes_telegram_briefing(tmp_path)
    assert "Hermes" in text
    assert "entry_guard" in text
    assert "не снимать" in text or "Не трогать" in text
    data, path = load_winning_entry_rules(tmp_path)
    assert data is not None
    assert path is not None
