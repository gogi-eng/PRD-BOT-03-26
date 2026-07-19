"""Hermes supervisor bypass level 2."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.signals.types import UnifiedSignal
from prd_agent.supervisor.supervisor_v4 import SupervisorMode, SupervisorV4


def _cfg(tmp_path, **hb_kw) -> dict:
    hermes_bypass = {
        "level": 2,
        "enabled": True,
        "min_confidence": 0.92,
        "min_local_hour": 9,
        "require_soft_label": "favorable",
        "min_atr_pct": 0.288,
        "max_atr_pct": 2.0,
    }
    hermes_bypass.update(hb_kw)
    return {
        "_root": str(tmp_path),
        "timezone_offset": 3,
        "rule_weight_learning": {"min_score_to_enter": 75.5},
        "trading": {
            "block_entry_utc_hours": [4],
            "risk_pct_per_trade": 0.35,
        },
        "supervisor_v4": {
            "enabled": True,
            "seed_blocked_utc_hours": [4],
            "preferred_utc_hours": [10, 11, 12],
            "hermes_link": {"hermes_bypass": hermes_bypass},
        },
    }


def _make(tmp_path) -> SupervisorV4:
    cfg = _cfg(tmp_path)
    return SupervisorV4(cfg, tmp_path / "data", SelfImprover(cfg, tmp_path))


def _hermes_sig(**kw) -> UnifiedSignal:
    base = dict(
        symbol="ETHUSDT",
        side="Sell",
        confidence=0.93,
        source="telegram",
        entry=100.0,
        stop_loss=102.0,
        take_profit=95.6,
        reason="test",
        raw={
            "regime": "trend",
            "adx": 31.6,
            "htf_trend": "bearish",
            "normalized_imbalance": -0.53,
            "volume_24h_usdt": 450_000_000,
        },
    )
    base.update(kw)
    return UnifiedSignal(**base)


def _favorable_ctx(hour: int = 10, atr_pct: float = 0.51) -> dict:
    return {
        "local_hour": hour,
        "side": "SELL",
        "atr_pct": atr_pct,
        "regime": "trend",
        "adx": 31.6,
        "htf_trend": "bearish",
        "normalized_imbalance": -0.53,
        "volume_24h_usdt": 450_000_000,
    }


def test_defensive_preferred_hour_no_bypass_needed(tmp_path) -> None:
    sup = _make(tmp_path)
    sup._meta.mode = SupervisorMode.DEFENSIVE
    sup._meta.mode_changed_at = datetime.now(timezone.utc) - timedelta(hours=3)
    sig = _hermes_sig()
    ctx = _favorable_ctx(hour=10)
    # UTC 7 = local 10 — в preferred
    ok, reason = sup.can_enter_with_hermes(
        "ETHUSDT", sig=sig, entry_context=ctx, utc_hour=7
    )
    assert ok, reason
    assert "hermes_bypass" not in reason

def test_bypass_defensive_non_preferred_with_hermes(tmp_path) -> None:
    sup = _make(tmp_path)
    sup._meta.mode = SupervisorMode.DEFENSIVE
    sup._meta.mode_changed_at = datetime.now(timezone.utc) - timedelta(hours=3)
    sig = _hermes_sig()
    ctx = _favorable_ctx(hour=14)
    # UTC 11 = local 14 — not in preferred
    ok, reason = sup.can_enter("ETHUSDT", utc_hour=11)
    assert not ok
    assert "DEFENSIVE" in reason

    ok2, reason2 = sup.can_enter_with_hermes(
        "ETHUSDT", sig=sig, entry_context=ctx, utc_hour=11
    )
    assert ok2, reason2
    assert "hermes_bypass" in reason2


def test_no_bypass_panic(tmp_path) -> None:
    sup = _make(tmp_path)
    sup._meta.panic_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    sig = _hermes_sig()
    ok, reason = sup.can_enter_with_hermes(
        "ETHUSDT",
        sig=sig,
        entry_context=_favorable_ctx(),
        utc_hour=11,
    )
    assert not ok
    assert "восстановления" in reason


def test_no_bypass_seed_blocked_hour(tmp_path) -> None:
    sup = _make(tmp_path)
    sig = _hermes_sig()
    ctx = _favorable_ctx(hour=4)
    # UTC 1 = local 4 — seed blocked
    ok, reason = sup.can_enter_with_hermes(
        "ETHUSDT", sig=sig, entry_context=ctx, utc_hour=1
    )
    assert not ok
    assert "заблокирован" in reason


def test_no_bypass_low_confidence(tmp_path) -> None:
    sup = _make(tmp_path)
    sup._meta.mode = SupervisorMode.DEFENSIVE
    sup._meta.mode_changed_at = datetime.now(timezone.utc) - timedelta(hours=3)
    sig = _hermes_sig(confidence=0.88)
    ok, reason = sup.can_enter_with_hermes(
        "ETHUSDT",
        sig=sig,
        entry_context=_favorable_ctx(hour=14),
        utc_hour=11,
    )
    assert not ok


def test_deploy_configs_have_hermes_disabled() -> None:
    import yaml
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("config.production.yaml", "config.agent_world_sandbox.yaml"):
        cfg = yaml.safe_load((root / "deploy" / name).read_text(encoding="utf-8"))
        assert cfg.get("hermes", {}).get("enabled") is False
        hb = cfg["supervisor_v4"]["hermes_link"]["hermes_bypass"]
        assert hb.get("enabled") is False
        assert cfg["supervisor_v4"]["hermes_link"].get("respect_entry_profile") is False
