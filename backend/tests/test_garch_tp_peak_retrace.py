"""Tests for GARCH TP peak retrace exit."""
from prd_agent.positions.garch_tp_peak_retrace import (
    GarchTpPeakRetraceConfig,
    evaluate_garch_tp_peak_retrace,
    retrace_threshold_pct,
)


def _cfg(**kw) -> GarchTpPeakRetraceConfig:
    base = GarchTpPeakRetraceConfig(
        enabled=True,
        min_tp_progress_pct=90.0,
        retrace_from_peak_pct=25.0,
        retrace_by_regime={"calm": 20.0, "normal": 25.0, "storm": 35.0},
        min_peak_profit_pct=0.5,
        apply_to_manual=True,
        apply_to_bot=True,
    )
    for k, v in kw.items():
        setattr(base, k, v)
    return base


def test_arms_at_90pct_tp_progress_long():
    cfg = _cfg()
    armed, peak, action, _ = evaluate_garch_tp_peak_retrace(
        side="Buy",
        entry=100.0,
        mark=109.0,
        take_profit=110.0,
        current_profit_pct=9.0,
        tp_zone_armed=False,
        tp_zone_peak_profit_pct=0.0,
        regime="normal",
        origin="manual",
        cfg=cfg,
        symbol="TESTUSDT",
    )
    assert armed is True
    assert peak >= 9.0
    assert action is None


def test_no_close_before_tp_zone():
    cfg = _cfg()
    _, _, action, _ = evaluate_garch_tp_peak_retrace(
        side="Buy",
        entry=100.0,
        mark=105.0,
        take_profit=110.0,
        current_profit_pct=5.0,
        tp_zone_armed=False,
        tp_zone_peak_profit_pct=0.0,
        regime="normal",
        origin="manual",
        cfg=cfg,
    )
    assert action is None


def test_closes_on_25pct_retrace_from_peak_long():
    cfg = _cfg()
    armed, peak, action, note = evaluate_garch_tp_peak_retrace(
        side="Buy",
        entry=100.0,
        mark=107.5,
        take_profit=110.0,
        current_profit_pct=7.5,
        tp_zone_armed=True,
        tp_zone_peak_profit_pct=10.0,
        regime="normal",
        origin="manual",
        cfg=cfg,
        symbol="LONGUSDT",
    )
    assert armed is True
    assert peak == 10.0
    assert action == "close_garch_tp_retrace"
    assert "retrace=" in note


def test_storm_uses_35pct_threshold():
    cfg = _cfg()
    _, _, action, _ = evaluate_garch_tp_peak_retrace(
        side="Buy",
        entry=100.0,
        mark=108.0,
        take_profit=110.0,
        current_profit_pct=8.0,
        tp_zone_armed=True,
        tp_zone_peak_profit_pct=10.0,
        regime="storm",
        origin="bot",
        cfg=cfg,
    )
    assert action is None
    assert retrace_threshold_pct("storm", cfg) == 35.0


def test_short_close_on_retrace():
    cfg = _cfg()
    _, _, action, _ = evaluate_garch_tp_peak_retrace(
        side="Sell",
        entry=100.0,
        mark=91.0,
        take_profit=90.0,
        current_profit_pct=7.5,
        tp_zone_armed=True,
        tp_zone_peak_profit_pct=10.0,
        regime="normal",
        origin="manual",
        cfg=cfg,
    )
    assert action == "close_garch_tp_retrace"


def test_manual_skip_when_disabled_for_manual():
    cfg = _cfg(apply_to_manual=False)
    _, _, action, _ = evaluate_garch_tp_peak_retrace(
        side="Buy",
        entry=100.0,
        mark=107.0,
        take_profit=110.0,
        current_profit_pct=7.0,
        tp_zone_armed=True,
        tp_zone_peak_profit_pct=10.0,
        regime="normal",
        origin="manual",
        cfg=cfg,
    )
    assert action is None


def test_from_cfg_reads_nested_block():
    root = {
        "manual_trailing_garch_learning": {
            "enabled": True,
            "tp_peak_retrace_exit": {
                "enabled": True,
                "min_tp_progress_pct": 92.0,
                "retrace_by_regime": {"calm": 18.0, "normal": 24.0, "storm": 33.0},
            },
        }
    }
    cfg = GarchTpPeakRetraceConfig.from_cfg(root)
    assert cfg.enabled is True
    assert cfg.min_tp_progress_pct == 92.0
    assert cfg.retrace_by_regime["calm"] == 18.0
