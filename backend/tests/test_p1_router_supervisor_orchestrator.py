"""Тесты Пакета 2 (P1): роутер сигналов, пороги confidence, SupervisorV4, orchestrator и scanner singleton."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from prd_agent.entry.entry_engine_bridge import should_apply_zone_entry
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.market.market_scanner_bridge import (
    _SCANNER_AGENTS,
    close_scanner_agent,
    run_market_scan_once,
    run_spike_scan_once,
)
from prd_agent.ops.runtime_controls import save_runtime_controls
from prd_agent.risk.entry_guard import EntryGuard
from prd_agent.risk.quality_gate import QualityGate
from prd_agent.signals.confidence_filter import passes_emit_gate
from prd_agent.signals.router import SignalRouter
from prd_agent.signals.types import UnifiedSignal
from prd_agent.supervisor.supervisor_v4 import SupervisorMode, SupervisorV4


def _base_cfg(tmp_path: Path) -> Dict[str, Any]:
    return {
        "_root": str(tmp_path),
        "bybit": {
            "api_key": "",
            "api_secret": "",
            "testnet": True,
            "category": "linear",
        },
        "trading": {
            "min_signal_confidence": 0.85,
            "min_own_agent_confidence": 0.82,
            "min_multi_agent_score": 0.4,
            "risk_pct_per_trade": 0.35,
            "leverage": 10,
            "dynamic_leverage": {
                "enabled": True,
                "min": 5,
                "max": 25,
                "min_confidence": 0.68,
                "max_confidence": 0.95,
            },
        },
        "signals": {
            "own_agents_enabled": False,
            "telegram_inbox_enabled": False,
            "whale_news_enabled": False,
            "min_telegram_confidence": 0.65,
            "min_analysis_confidence": 0.88,
            "min_multi_agent_score": 0.4,
        },
        "ta_scanner": {
            "enabled": False,
            "min_confidence": 0.82,
            "min_rr": 2.0,
        },
        "external_sentiment": {"enabled": False},
        "quality_gate": {
            "enabled": True,
            "min_confidence": 0.85,
            "min_rr_ratio": 2.0,
            "require_sl_tp": True,
            "min_24h_volume_usdt": 1_000_000,
        },
        "pump_dump_trade": {
            "enabled": True,
            "min_confidence": 0.65,
        },
        "zone_entry": {
            "enabled": True,
            "sources": ["own_multi_agent", "ta_volatility", "hybrid"],
            "skip_sources_with_levels": ["telegram", "tg", "mirror_pump_dump"],
        },
        "entry_guard": {
            "enabled": True,
            "max_market_drift_pct": 0.005,
            "max_limit_drift_pct": 0.015,
            "max_skip_drift_pct": 0.02,
            "telegram_limit_entry": True,
            "telegram_sources": ["telegram", "tg", "tg_inbox"],
        },
        "supervisor_v4": {
            "enabled": True,
            "mode_cooldown_minutes": 120,
            "preferred_utc_hours": [4, 10],
            "defensive_max_atr_pct": 3.0,
            "max_atr_pct": 5.0,
        },
    }


def test_p1_router_merge_preserves_full_levels_and_primary_source(tmp_path: Path):
    """Голый entry от whale_liquidation без SL/TP не затирает уровни telegram, а primary_source сохраняется."""
    cfg = _base_cfg(tmp_path)
    router = SignalRouter(cfg, tmp_path / "signals")

    tg_sig = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.72,
        source="telegram",
        entry=60000.0,
        stop_loss=59000.0,
        take_profit=62500.0,
        reason="TG VIP channel",
    )
    own_sig = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.84,
        source="own_multi_agent",
        entry=60150.0,
        stop_loss=59800.0,
        take_profit=60750.0,
        reason="multi-agent score=0.55",
        raw={"aggregate": 0.55},
    )
    whale_sig = UnifiedSignal(
        symbol="BTCUSDT",
        side="Buy",
        confidence=0.88,
        source="whale_liquidation",
        entry=60500.0,  # голый рыночный entry без SL/TP
        stop_loss=0.0,
        take_profit=0.0,
        reason="short liq $1.2M",
    )

    merged = router.merge_and_rank([own_sig, tg_sig, whale_sig])
    assert len(merged) == 1
    m = merged[0]
    assert m.source == "hybrid"
    assert m.raw.get("primary_source") == "telegram"
    # Уровни взяты единым пакетом от telegram (высший приоритет с полной тройкой), а не от whale (60500)
    assert m.entry == pytest.approx(60000.0)
    assert m.stop_loss == pytest.approx(59000.0)
    assert m.take_profit == pytest.approx(62500.0)

    # zone_entry не должен перезаписывать уровни для гибрида с primary_source=telegram
    assert should_apply_zone_entry(m, cfg) is False

    # EntryGuard сохраняет лимитный вход для гибрида с primary_source=telegram при drift 1%
    eg = EntryGuard(cfg)
    plan = eg.plan_execution(m, plan_entry=m.entry, market_price=60600.0)
    assert plan.allowed is True
    assert plan.order_type == "Limit"
    assert plan.limit_price == pytest.approx(60000.0)


def test_p1_router_hybrid_telegram_and_own_passes_threshold(tmp_path: Path):
    """Гибрид из валидного telegram (0.70 >= 0.65) и валидного own (0.83 >= 0.82) не отсекается порогом 0.85."""
    cfg = _base_cfg(tmp_path)
    router = SignalRouter(cfg, tmp_path / "signals")
    tg_sig = UnifiedSignal(
        symbol="ETHUSDT",
        side="Buy",
        confidence=0.70,
        source="telegram",
        entry=3000.0,
        stop_loss=2940.0,
        take_profit=3150.0,
    )
    own_sig = UnifiedSignal(
        symbol="ETHUSDT",
        side="Buy",
        confidence=0.83,
        source="own_multi_agent",
        entry=3005.0,
        stop_loss=2980.0,
        take_profit=3060.0,
        raw={"aggregate": 0.48},
    )
    merged = router.merge_and_rank([tg_sig, own_sig])
    assert len(merged) == 1
    assert merged[0].raw.get("primary_source") == "telegram"


def test_p1_confidence_filter_and_quality_gate_synced_by_source(tmp_path: Path):
    """Пороги синхронизированы по источнику: telegram (0.65), ta_volatility (0.82), pump_dump (0.65)."""
    cfg = _base_cfg(tmp_path)
    qg = QualityGate(cfg)

    class _FakeEx:
        async def get_tickers(self):
            return [{"symbol": "SOLUSDT", "turnover24h": "50000000"}]

    ex = _FakeEx()

    # 1. Telegram сигнал с conf=0.70 (< 0.85 quality_gate default, но >= 0.65 min_telegram_confidence)
    tg_sig = UnifiedSignal(
        symbol="SOLUSDT",
        side="Buy",
        confidence=0.70,
        source="telegram",
        entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
    )
    assert passes_emit_gate(tg_sig, cfg) is True
    ok_tg, reason_tg = asyncio.run(qg.check(tg_sig, ex, entry=100.0, sl=98.0, tp=105.0))
    assert ok_tg is True, reason_tg

    # 2. TA сигнал с conf=0.83 (< 0.88 min_analysis_confidence, но >= 0.82 ta_scanner.min_confidence)
    ta_sig = UnifiedSignal(
        symbol="SOLUSDT",
        side="Buy",
        confidence=0.83,
        source="ta_volatility",
        entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
    )
    assert passes_emit_gate(ta_sig, cfg) is True
    ok_ta, reason_ta = asyncio.run(qg.check(ta_sig, ex, entry=100.0, sl=98.0, tp=105.0))
    assert ok_ta is True, reason_ta

    # 3. Pump/dump сигнал с conf=0.68 (>= 0.65 pump_dump_trade.min_confidence)
    pd_sig = UnifiedSignal(
        symbol="SOLUSDT",
        side="Buy",
        confidence=0.68,
        source="mirror_pump_dump_agent",
        entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
    )
    assert passes_emit_gate(pd_sig, cfg) is True
    ok_pd, reason_pd = asyncio.run(qg.check(pd_sig, ex, entry=100.0, sl=98.0, tp=105.0))
    assert ok_pd is True, reason_pd

    # 4. Whale сигнал с conf=0.80 (< 0.85 quality_gate.min_confidence) отклоняется
    whale_sig = UnifiedSignal(
        symbol="SOLUSDT",
        side="Buy",
        confidence=0.80,
        source="whale_oi",
        entry=100.0,
        stop_loss=98.0,
        take_profit=105.0,
    )
    assert passes_emit_gate(whale_sig, cfg) is False
    ok_w, reason_w = asyncio.run(qg.check(whale_sig, ex, entry=100.0, sl=98.0, tp=105.0))
    assert ok_w is False
    assert "confidence" in reason_w


def test_p1_supervisor_defensive_bypasses_mode_cooldown_and_checks_atr(tmp_path: Path):
    """Переход в DEFENSIVE не блокируется mode_cooldown_minutes, а can_enter учитывает atr_pct_frac."""
    cfg = _base_cfg(tmp_path)
    imp = SelfImprover(cfg, tmp_path)
    sup = SupervisorV4(cfg, tmp_path / "data", imp)

    # Переводим в AGGRESSIVE только что (активен 120-минутный кулдаун)
    sup._meta.mode = SupervisorMode.AGGRESSIVE
    sup._meta.mode_changed_at = datetime.now(timezone.utc)
    assert sup._mode_cooldown_active() is True

    # При убытке дня или серии убытков переход в DEFENSIVE должен сработать немедленно, несмотря на кулдаун!
    sup.tick_meta(day_pnl_usdt=-20.0, consecutive_losses=2)
    assert sup._meta.mode == SupervisorMode.DEFENSIVE

    # Но обратный переход из DEFENSIVE в NORMAL при активном кулдауне блокируется
    sup.tick_meta(day_pnl_usdt=1.0, consecutive_losses=0)
    assert sup._meta.mode == SupervisorMode.DEFENSIVE

    # Проверка atr_pct_frac в can_enter (в DEFENSIVE лимит defensive_max_atr_pct=3.0%)
    ok_norm_atr, _ = sup.can_enter("BTCUSDT", utc_hour=4, atr_pct_frac=0.015)  # 1.5% ATR
    assert ok_norm_atr is True
    ok_high_atr, reason_atr = sup.can_enter("BTCUSDT", utc_hour=4, atr_pct_frac=0.042)  # 4.2% ATR > 3.0%
    assert ok_high_atr is False
    assert "ATR" in reason_atr


def test_p1_recommend_leverage_uses_entry_context(tmp_path: Path):
    """recommend_leverage учитывает переданный entry_context (spread_pct, atr_pct, normalized_imbalance)."""
    cfg = _base_cfg(tmp_path)
    imp = SelfImprover(cfg, tmp_path)
    sup = SupervisorV4(cfg, tmp_path / "data", imp)
    sig = UnifiedSignal("BTCUSDT", "Buy", 0.90, "ta_volatility", entry=100.0, stop_loss=99.0, take_profit=103.0)

    good_ctx = {
        "spread_pct": 0.02,
        "atr_pct": 0.65,
        "normalized_imbalance": 0.25,
        "regime": "trend",
        "htf_trend": "bullish",
        "entry_zone": "demand_fvg",
    }
    bad_ctx = {
        "spread_pct": 0.45,  # > 0.15 -> spread_ok=False
        "atr_pct": 0.005,    # < 0.02 -> atr_ok=False
        "normalized_imbalance": 0.01,  # < 0.04 -> imbalance_ok=False
        "regime": "chop",
        "htf_trend": "bearish",  # countertrend для BUY -> countertrend_ok=False
        "entry_zone": "no_zone",
    }
    adv_good = sup.recommend_leverage(sig, entry=100.0, stop_loss=99.0, take_profit=103.0, entry_context=good_ctx)
    adv_bad = sup.recommend_leverage(sig, entry=100.0, stop_loss=99.0, take_profit=103.0, entry_context=bad_ctx)
    assert adv_good.score > adv_bad.score
    assert adv_good.leverage >= adv_bad.leverage
    assert adv_bad.checks["spread_ok"] is False
    assert adv_bad.checks["countertrend_ok"] is False


def test_p1_scanner_bridge_reuses_singleton_and_preserves_rtc_state(tmp_path: Path, monkeypatch):
    """run_market_scan_once и run_spike_scan_once переиспользуют один экземпляр и закрывают его в close_scanner_agent."""
    created: List[Any] = []
    closed: List[Any] = []

    class _FakeScannerAgent:
        def __init__(self, repo: Path) -> None:
            self.repo = repo
            self.market_scanner_enabled = True
            self.spike_scalp_enabled = True
            self.telegram_notify = True
            created.append(self)

        async def run_market_scan_once(self) -> List[str]:
            return ["m1"]

        async def run_spike_scan_once(self) -> List[str]:
            return ["s1"]

        async def close(self) -> None:
            closed.append(self)

    import scripts.telegram_signal_agent as tsa_mod

    monkeypatch.setattr(tsa_mod, "TelegramSignalAgent", _FakeScannerAgent)
    _SCANNER_AGENTS.clear()

    cfg = {
        "market_scanner": {
            "enabled": True,
            "bos_scan_enabled": True,
            "run_loop_in_unified_bot": True,
            "spike_scalp": {"enabled": True, "run_loop_in_unified_bot": True},
        }
    }
    res_m1 = asyncio.run(run_market_scan_once(tmp_path, cfg))
    res_s1 = asyncio.run(run_spike_scan_once(tmp_path, cfg))
    res_m2 = asyncio.run(run_market_scan_once(tmp_path, cfg))

    assert res_m1 == ["m1"]
    assert res_s1 == ["s1"]
    assert res_m2 == ["m1"]
    assert len(created) == 1, "TelegramSignalAgent должен создаваться один раз (singleton)"
    assert len(closed) == 0, "Не должен закрываться после каждого прохода"

    asyncio.run(close_scanner_agent(tmp_path))
    assert len(closed) == 1


def test_p1_orchestrator_skip_without_light_snapshot_on_global_block(tmp_path: Path, monkeypatch):
    """При глобальном блоке (can_trade=False / supervisor.can_enter=False) _record_ledger_signal с include_snapshot=False не вызывает API."""
    from prd_agent.analysis.signal_ledger import SignalStatus
    from prd_agent.engine.orchestrator import UnifiedOrchestrator

    cfg = _base_cfg(tmp_path)
    orch = UnifiedOrchestrator(cfg)
    snap_calls = 0

    async def _fail_if_called(sig):
        nonlocal snap_calls
        snap_calls += 1
        return {"called": True}

    monkeypatch.setattr(orch, "_light_snapshot_for_signal", _fail_if_called)
    sig = UnifiedSignal("BTCUSDT", "Buy", 0.90, "telegram", entry=60000.0, stop_loss=59000.0, take_profit=62000.0)
    entry = asyncio.run(
        orch._record_ledger_signal(
            sig,
            status=SignalStatus.SKIPPED,
            reason="Дневной лимит убытка",
            include_snapshot=False,
        )
    )
    assert snap_calls == 0
    assert entry.snapshot == {}
