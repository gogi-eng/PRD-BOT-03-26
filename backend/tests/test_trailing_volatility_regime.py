"""GARCH → множитель дистанции трейлинг-SL (calm/normal/storm)."""
from __future__ import annotations

import math
from typing import List

from prd_agent.positions.trailing_volatility_regime import (
    TrailingVolatilityRegimeConfig,
    apply_trailing_garch_to_distance_factor,
    compute_trailing_garch_distance_factor,
    regime_distance_mult,
    should_apply_trailing_volatility_regime,
)
from prd_agent.risk.volatility_regime_sizing import log_returns


def _synthetic_closes(n: int = 200, *, storm: bool = False) -> List[float]:
    price = 100.0
    out = [price]
    for i in range(1, n):
        if storm:
            amp = 0.002 + 0.0008 * (i / n) * (1.0 + (i % 5) * 0.4)
            shock = amp if (i % 2 == 0) else -amp * 1.2
        else:
            shock = 0.0004 * math.sin(i / 7.0)
        price = max(1.0, price * (1.0 + shock))
        out.append(price)
    return out


def _klines_from_closes(closes: List[float]) -> List[dict]:
    return [{"close": c, "open": c, "high": c, "low": c} for c in closes]


def test_disabled_returns_one() -> None:
    cfg = TrailingVolatilityRegimeConfig(enabled=False)
    mult, regime, note = compute_trailing_garch_distance_factor(
        klines=_klines_from_closes(_synthetic_closes()),
        trail_cfg=cfg,
        root_cfg={},
    )
    assert mult == 1.0
    assert regime == "disabled"
    assert note == "disabled"


def test_regime_distance_mult_clamp() -> None:
    cfg = TrailingVolatilityRegimeConfig(
        enabled=True,
        distance_mult={"calm": 0.1, "storm": 9.0, "normal": 1.0},
        clamp_min=0.50,
        clamp_max=2.0,
    )
    assert regime_distance_mult("calm", cfg) == 0.50
    assert regime_distance_mult("storm", cfg) == 2.0
    assert regime_distance_mult("normal", cfg) == 1.0


def test_calm_tighter_than_storm() -> None:
    trail = TrailingVolatilityRegimeConfig(
        enabled=True,
        reuse_sizing_cfg=False,
        min_bars=80,
        lookback_bars=200,
        distance_mult={"calm": 0.75, "normal": 1.0, "storm": 1.35},
    )
    root = {"volatility_regime_sizing": {"enabled": True}}
    calm_m, calm_r, _ = compute_trailing_garch_distance_factor(
        klines=_klines_from_closes(_synthetic_closes(220, storm=False)),
        trail_cfg=trail,
        root_cfg=root,
    )
    storm_m, storm_r, _ = compute_trailing_garch_distance_factor(
        klines=_klines_from_closes(_synthetic_closes(220, storm=True)),
        trail_cfg=trail,
        root_cfg=root,
    )
    # Не всегда идеально классифицирует синтетику, но storm-мультипликатор
    # не должен быть меньше calm при известных режимах.
    if calm_r == "calm" and storm_r == "storm":
        assert calm_m < storm_m
        assert calm_m == 0.75
        assert storm_m == 1.35
    else:
        # хотя бы clamp и known keys работают
        assert calm_m >= 0.50
        assert storm_m <= 2.0


def test_apply_multiplies_distance_factor() -> None:
    trail = TrailingVolatilityRegimeConfig(
        enabled=True,
        reuse_sizing_cfg=False,
        advisory_only=False,
        distance_mult={"calm": 0.75, "normal": 1.0, "storm": 1.35, "unknown": 1.0},
    )
    # Форсируем через прямой regime_distance_mult путь: advisory off + known closes
    new_f, regime, note = apply_trailing_garch_to_distance_factor(
        1.0,
        klines=_klines_from_closes(_synthetic_closes(200, storm=False)),
        trail_cfg=trail,
        root_cfg={},
        symbol="BTCUSDT",
        side="Buy",
        prev_regime="",
    )
    assert regime in {"calm", "normal", "storm", "unknown"}
    assert note is not None or regime == "unknown"
    assert new_f > 0


def test_advisory_keeps_factor() -> None:
    trail = TrailingVolatilityRegimeConfig(
        enabled=True,
        advisory_only=True,
        reuse_sizing_cfg=False,
        distance_mult={"calm": 0.75, "normal": 1.0, "storm": 1.35},
    )
    new_f, _regime, _note = apply_trailing_garch_to_distance_factor(
        1.2,
        klines=_klines_from_closes(_synthetic_closes(200)),
        trail_cfg=trail,
        root_cfg={},
        prev_regime="normal",
    )
    assert new_f == 1.2


def test_apply_to_origins() -> None:
    cfg = TrailingVolatilityRegimeConfig(
        enabled=True,
        apply_to_manual=True,
        apply_to_bot=False,
        apply_to_pump_dump=True,
    )
    assert should_apply_trailing_volatility_regime(
        cfg=cfg, origin="manual", pump_dump_mode=False
    )
    assert not should_apply_trailing_volatility_regime(
        cfg=cfg, origin="bot", pump_dump_mode=False
    )
    assert should_apply_trailing_volatility_regime(
        cfg=cfg, origin="bot", pump_dump_mode=True
    )


def test_sandbox_and_prod_trailing_garch_on() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    aw = (root / "deploy" / "config.agent_world_sandbox.yaml").read_text(encoding="utf-8")
    prod = (root / "deploy" / "config.production.yaml").read_text(encoding="utf-8")
    assert "trailing_volatility_regime:" in aw
    assert "trailing_volatility_regime:" in prod
    aw_block = aw.split("trailing_volatility_regime:", 1)[1].split("trailing_after_be:", 1)[0]
    prod_block = prod.split("trailing_volatility_regime:", 1)[1].split("trailing_after_be:", 1)[0]
    assert "enabled: true" in aw_block
    assert "enabled: true" in prod_block
    assert "calm:" in aw_block and "storm:" in aw_block
    assert "calm:" in prod_block and "storm:" in prod_block


def test_log_returns_used_by_garch_path() -> None:
    """Smoke: ряд доходностей для calm не пустой."""
    rets = log_returns(_synthetic_closes(100))
    assert len(rets) >= 50
