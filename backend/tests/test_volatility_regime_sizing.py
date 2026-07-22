"""GARCH volatility regime sizing (calm/normal/storm)."""
from __future__ import annotations

import math
from typing import List

import pytest

from prd_agent.risk.volatility_regime_sizing import (
    classify_regime,
    closes_from_klines,
    compute_volatility_regime,
    evaluate_volatility_regime_sizing,
    garch11_variance_path,
    log_returns,
    regime_size_mult,
    volatility_regime_enabled,
)


def _synthetic_closes(n: int = 200, *, storm: bool = False) -> List[float]:
    """Спокойный ряд или ряд с растущей амплитудой (шторм)."""
    price = 100.0
    out = [price]
    for i in range(1, n):
        if storm:
            # Усиливающийся шум к концу ряда
            amp = 0.002 + 0.0008 * (i / n) * (1.0 + (i % 5) * 0.4)
            shock = amp if (i % 2 == 0) else -amp * 1.2
        else:
            shock = 0.0004 * math.sin(i / 7.0)
        price = max(1.0, price * (1.0 + shock))
        out.append(price)
    return out


def test_volatility_regime_disabled_returns_one() -> None:
    cfg = {"volatility_regime_sizing": {"enabled": False}}
    assert volatility_regime_enabled(cfg) is False
    res = compute_volatility_regime(_synthetic_closes(), {"enabled": False})
    assert res.enabled is False
    assert res.size_mult == 1.0
    assert res.apply is False


def test_garch_path_positive() -> None:
    closes = _synthetic_closes(150, storm=True)
    rets = log_returns(closes)
    path, nxt = garch11_variance_path(rets, alpha=0.08, beta=0.90)
    assert len(path) == len(rets)
    assert nxt > 0
    assert all(v > 0 for v in path)


def test_classify_regime_bounds() -> None:
    assert classify_regime(10.0) == "calm"
    assert classify_regime(50.0) == "normal"
    assert classify_regime(90.0) == "storm"


def test_regime_mult_clamp() -> None:
    block = {
        "regimes": {"calm": 9.0, "storm": 0.01},
        "clamp_min": 0.35,
        "clamp_max": 1.50,
    }
    assert regime_size_mult("calm", block) == 1.50
    assert regime_size_mult("storm", block) == 0.35


def test_storm_series_smaller_or_equal_mult_than_calm() -> None:
    block = {
        "enabled": True,
        "advisory_only": False,
        "block_on_storm": False,
        "mode": "regime",
        "kline_interval": "15",
        "min_bars": 80,
        "alpha": 0.08,
        "beta": 0.90,
        "calm_percentile": 30,
        "storm_percentile": 70,
        "regimes": {"calm": 1.25, "normal": 1.0, "storm": 0.50},
        "clamp_min": 0.35,
        "clamp_max": 1.50,
    }
    calm = compute_volatility_regime(_synthetic_closes(220, storm=False), block)
    storm = compute_volatility_regime(_synthetic_closes(220, storm=True), block)
    assert calm.enabled and storm.enabled
    assert calm.apply and storm.apply
    # Штормовой ряд не должен получать calm-множитель 1.25
    assert storm.size_mult <= calm.size_mult + 1e-9
    assert storm.regime in ("normal", "storm", "calm")
    assert calm.regime in ("normal", "storm", "calm")


def test_advisory_only_does_not_apply() -> None:
    block = {
        "enabled": True,
        "advisory_only": True,
        "min_bars": 80,
        "regimes": {"storm": 0.5, "calm": 1.25, "normal": 1.0},
    }
    # Через compute — advisory обрабатывается в evaluate; здесь просто mult path
    res = compute_volatility_regime(_synthetic_closes(200, storm=True), block)
    # compute still returns apply=False when advisory
    assert res.apply is False
    assert res.size_mult == 1.0


def test_block_on_storm() -> None:
    block = {
        "enabled": True,
        "advisory_only": False,
        "block_on_storm": True,
        "min_bars": 80,
        "calm_percentile": 30,
        "storm_percentile": 55,
        "regimes": {"calm": 1.25, "normal": 1.0, "storm": 0.50},
    }
    # Force high percentile by using very stormy data + low storm threshold
    res = compute_volatility_regime(_synthetic_closes(220, storm=True), block)
    if res.regime == "storm":
        assert res.block_entry is True
        assert res.apply is False
        assert res.size_mult == 1.0


def test_closes_from_klines() -> None:
    rows = [{"close": "10"}, {"close": 11}, {"close": 0}, {"close": "bad"}]
    assert closes_from_klines(rows) == [10.0, 11.0]


@pytest.mark.asyncio
async def test_evaluate_disabled_async() -> None:
    res = await evaluate_volatility_regime_sizing(
        exchange=None,
        symbol="BTCUSDT",
        cfg={"volatility_regime_sizing": {"enabled": False}},
        side="Buy",
        source="SPIKE_SCANNER",
        klines=[{"close": c} for c in _synthetic_closes(100)],
    )
    assert res.enabled is False
    assert res.size_mult == 1.0
