"""Unit tests for Trend-Continuation Hedge Pair strategy math."""
from __future__ import annotations

from prd_agent.strategies.hedge_pair import (
    HedgePairConfig,
    compute_ema,
    expected_net_on_continuation,
    expected_net_on_immediate_flatten_at_sl,
    hedge_fallback_allowed,
    infer_bias,
    simulate_pair_path,
    trend_allows_entry,
)


def _cfg(**kwargs) -> HedgePairConfig:
    base = dict(
        sl_price_pct=0.8,
        tp_to_sl_ratio=1.8,
        tp_price_pct=1.44,
        be_after_profit_pct=0.5,
        trail_distance_pct=0.4,
        max_pair_minutes=120.0,
        fee_pct_roundtrip_per_leg=0.12,
    )
    base.update(kwargs)
    return HedgePairConfig(**base)


def test_symmetric_tp_equals_sl_is_fee_negative():
    """TP==SL: one SL + other TP at same distance -> net approx -fees."""
    cfg = _cfg(sl_price_pct=1.0, tp_price_pct=1.0, tp_to_sl_ratio=1.0)
    entry = 100.0
    # Price rises 1% -> short SL and long TP both at 101
    prices = [entry, entry * 1.01]
    fees_bps = 12.0  # 0.12% RT per leg
    res = simulate_pair_path(prices, cfg, fees_bps, bias="long")
    assert res.net_pct < 0.0
    assert abs(res.net_pct - expected_net_on_immediate_flatten_at_sl(cfg, 0.12)) < 1e-9


def test_asymmetric_continuation_is_profitable():
    """Bias long: short SL first, then long continues to TP -> net > 0."""
    cfg = _cfg()
    entry = 100.0
    sl = cfg.sl_price_pct / 100.0
    tp = cfg.tp_price_pct / 100.0
    prices = [
        entry,
        entry * (1.0 + sl),  # short SL
        entry * (1.0 + sl + (tp - sl) * 0.5),
        entry * (1.0 + tp),  # long TP
    ]
    fees_bps = 12.0
    res = simulate_pair_path(prices, cfg, fees_bps, bias="long")
    assert res.first_sl_leg == "short"
    assert res.runner_leg == "long"
    assert res.net_pct > 0.0
    expected = expected_net_on_continuation(cfg, 0.12)
    assert abs(res.net_pct - expected) < 1e-6


def test_reversal_after_first_sl_is_loss():
    """After short SL, price reverses to long SL -> both legs lose."""
    cfg = _cfg(be_after_profit_pct=99.0)  # no BE before reverse
    entry = 100.0
    sl = cfg.sl_price_pct / 100.0
    prices = [
        entry,
        entry * (1.0 + sl),  # short SL -> long runner
        entry * (1.0 - sl),  # long original SL
    ]
    res = simulate_pair_path(prices, cfg, 12.0, bias="long")
    assert res.first_sl_leg == "short"
    assert res.net_pct < 0.0


def test_closed_form_continuation_matches_sim():
    cfg = _cfg(sl_price_pct=0.8, tp_price_pct=1.44, tp_to_sl_ratio=1.8)
    fee = 0.10
    closed = expected_net_on_continuation(cfg, fee)
    entry = 50_000.0
    sl = cfg.sl_price_pct / 100.0
    tp = cfg.tp_price_pct / 100.0
    # Bias short: dip to long SL, then continue down to short TP
    prices = [entry, entry * (1.0 - sl), entry * (1.0 - tp)]
    res = simulate_pair_path(prices, cfg, fee * 100.0, bias="short")
    assert res.first_sl_leg == "long"
    assert res.runner_leg == "short"
    assert abs(res.net_pct - closed) < 1e-6


def test_config_from_yaml_defaults():
    cfg = HedgePairConfig.from_cfg({})
    assert cfg.enabled is False
    assert cfg.execute is False
    assert cfg.sl_price_pct == 0.8
    assert cfg.tp_to_sl_ratio == 1.8
    assert abs(cfg.tp_price_pct - 0.8 * 1.8) < 1e-9
    assert cfg.max_pairs == 1
    assert cfg.require_trend_bias is True
    assert "BTCUSDT" in cfg.symbols

    cfg2 = HedgePairConfig.from_cfg(
        {"hedge_pair": {"sl_price_pct": 1.0, "tp_to_sl_ratio": 2.0, "tp_price_pct": 1.5}}
    )
    # tp raised to sl*ratio floor
    assert cfg2.tp_price_pct == 2.0
    assert trend_allows_entry("long", ema=100.0, price=101.0)
    assert not trend_allows_entry("long", ema=100.0, price=99.0)
    assert trend_allows_entry("short", ema=100.0, price=99.0)


def test_infer_bias_long_short():
    assert infer_bias(101.0, 100.0) == "long"
    assert infer_bias(99.0, 100.0) == "short"
    assert infer_bias(100.0, 100.0) == "short"


def test_compute_ema_basic():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    ema = compute_ema(closes, period=3)
    assert ema > 0
    # last close pulls EMA up vs first
    assert ema > closes[0]


def test_hedge_fallback_allowed_gate():
    assert hedge_fallback_allowed(signals_empty=False, open_positions=0, max_pairs=1) is False
    assert hedge_fallback_allowed(signals_empty=True, open_positions=0, max_pairs=1) is True
    assert hedge_fallback_allowed(signals_empty=True, open_positions=1, max_pairs=1) is False
    assert hedge_fallback_allowed(signals_empty=True, open_positions=1, max_pairs=2) is True


def test_config_only_when_no_other_signals_default():
    cfg = HedgePairConfig.from_cfg({})
    assert cfg.only_when_no_other_signals is True
    cfg2 = HedgePairConfig.from_cfg({"hedge_pair": {"only_when_no_other_signals": False}})
    assert cfg2.only_when_no_other_signals is False

