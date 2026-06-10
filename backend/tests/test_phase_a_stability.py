"""Фаза A: API-кеш, проверка config, алерт рассинхрона позиций."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from prd_agent.config_validate import validate_config_data
from prd_agent.exchange.api_cache import ExchangeApiCache
from prd_agent.positions.sync_guard import PositionSyncGuard


def test_validate_config_rejects_bad_leverage():
    ok, errors = validate_config_data(
        {
            "trading": {"leverage": 500, "loop_interval_sec": 60, "max_positions": 3},
            "risk": {"max_daily_loss_pct": 5},
            "quality_gate": {"min_rr_ratio": 2.0},
        }
    )
    assert not ok
    assert any("leverage" in e for e in errors)


def test_validate_config_accepts_production_shape():
    ok, errors = validate_config_data(
        {
            "trading": {
                "leverage": 20,
                "loop_interval_sec": 60,
                "max_positions": 6,
                "risk_pct_per_trade": 0.35,
                "min_signal_confidence": 0.85,
            },
            "risk": {"max_daily_loss_pct": 5.0, "max_consecutive_losses": 4},
            "quality_gate": {"min_rr_ratio": 2.0, "min_confidence": 0.85},
            "api_cache": {"price_ttl_sec": 8, "max_parallel_requests": 6},
            "position_sync": {"alert_cooldown_sec": 600},
        }
    )
    assert ok, errors


def test_api_cache_hits_price_without_duplicate_fetch():
    calls = {"n": 0}

    async def _fetch():
        calls["n"] += 1
        return 100.5

    async def _run():
        cache = ExchangeApiCache(enabled=True, price_ttl_sec=30)
        p1 = await cache.get_price("BTCUSDT", _fetch)
        p2 = await cache.get_price("BTCUSDT", _fetch)
        return p1, p2

    p1, p2 = asyncio.run(_run())
    assert p1 == p2 == 100.5
    assert calls["n"] == 1


@dataclass
class _Pos:
    origin: str


def test_sync_guard_alerts_registry_without_exchange():
    guard = PositionSyncGuard(cooldown_sec=60)
    alerts = guard.check(
        bot_symbols={"ETHUSDT"},
        live_symbols=set(),
        tracked={},
    )
    assert len(alerts) == 1
    assert "ETHUSDT" in alerts[0]
    assert guard.check(
        bot_symbols={"ETHUSDT"},
        live_symbols=set(),
        tracked={},
    ) == []


def test_sync_guard_alerts_tracked_bot_position_gone():
    guard = PositionSyncGuard(cooldown_sec=600)
    alerts = guard.check(
        bot_symbols=set(),
        live_symbols=set(),
        tracked={"SOLUSDT": _Pos(origin="bot")},
    )
    assert any("SOLUSDT" in a for a in alerts)
    assert guard.check(
        bot_symbols=set(),
        live_symbols=set(),
        tracked={"SOLUSDT": _Pos(origin="bot")},
    ) == []
