"""Zone fallback не должен обходить volume_guard (CBRSUSDT vol=0 → ENTERED)."""
from __future__ import annotations

from prd_agent.entry.entry_engine_bridge import should_block_zone_entry_fallback
from prd_agent.entry.entry_soft_rules import compute_soft_score


def test_volume_guard_blocks_zone_fallback():
    reason = "volume_guard (vol=0 < avg20=274 * 0.50)"
    assert should_block_zone_entry_fallback(reason) is True


def test_volume_guard_partial_vol_still_blocks():
    reason = "volume_guard (vol=50 < avg20=274 * 0.50)"
    assert should_block_zone_entry_fallback(reason) is True


def test_other_rejects_do_not_hard_block_fallback():
    # Остальные отказы по-прежнему могут идти в мягкий fallback (как раньше).
    assert should_block_zone_entry_fallback("score_below_threshold") is False
    assert should_block_zone_entry_fallback("") is False
    assert should_block_zone_entry_fallback("momentum_guard (SELL ...)") is False


def test_soft_caution_reduces_size_mult():
    """caution раньше оставлял size_mult≈1.0 — полный размер при слабом скоре."""
    ctx = {
        "regime": "chop",
        "adx": 18,
        "atr_pct": 0.5,
        "normalized_imbalance": 0.30,
        "volume_24h_usdt": 25_000_000,
        "spread_pct": 0.01,
        "local_hour": 12,
    }
    cfg = {
        "timezone_offset": 3,
        "rule_weight_learning": {
            "caution_threshold": 40,
            "neutral_threshold": 50,
            "favorable_threshold": 65,
            "weight_overrides": {
                "adx_ok": 0.55,
                "imb_strong": 0.55,
            },
        },
    }
    soft = compute_soft_score(ctx, side="BUY", cfg=cfg)
    assert soft.label == "caution"
    assert "spread_wide" in soft.active_rules
    assert soft.size_mult <= 0.35 + 1e-9


def test_soft_neutral_keeps_fullish_size():
    ctx = {
        "regime": "trend",
        "adx": 22,
        "atr_pct": 0.5,
        "local_hour": 12,
    }
    soft = compute_soft_score(ctx, side="BUY", cfg={"timezone_offset": 3})
    assert soft.label in ("neutral", "favorable")
    assert soft.size_mult >= 1.0 - 1e-9
