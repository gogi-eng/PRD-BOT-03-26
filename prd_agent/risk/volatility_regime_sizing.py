"""
GARCH(1,1) → режим calm / normal / storm → множитель размера позиции.

Идея как у Miles Deutscher / BeInCrypto: модель оценивает силу «тряски»
(волатильность), а не направление LONG/SHORT. В шторм — меньше размер,
в спокойствие — чуть больше. Входы остаются у вашей стратегии.

Config: volatility_regime_sizing (сначала AGENT-WORLD).
Маркер лога: «Volatility regime».
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("prd_agent.volatility_regime")

_LOG_MARKER = "Volatility regime"


@dataclass(frozen=True)
class VolatilityRegimeResult:
    enabled: bool
    regime: str  # calm | normal | storm | unknown | disabled
    size_mult: float
    forecast_vol_ann: float
    percentile: float
    reason: str
    apply: bool
    block_entry: bool = False


def read_volatility_regime_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("volatility_regime_sizing")
    return dict(raw) if isinstance(raw, dict) else {}


def volatility_regime_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_volatility_regime_cfg(cfg).get("enabled", False))


def log_volatility_regime_startup(cfg: Mapping[str, Any], log: Optional[logging.Logger] = None) -> None:
    lg = log or logger
    block = read_volatility_regime_cfg(cfg)
    if not bool(block.get("enabled", False)):
        return
    advisory = bool(block.get("advisory_only", False))
    mode = "только лог (advisory)" if advisory else "меняет размер позиции"
    lg.info(
        "%s: GARCH calm/normal/storm включён (%s)",
        _LOG_MARKER,
        mode,
    )


def _source_applies(source: str, block: Mapping[str, Any]) -> bool:
    src = str(source or "").strip().lower()
    skip = {
        str(x).strip().lower()
        for x in (block.get("skip_sources") or [])
        if str(x).strip()
    }
    if src in skip:
        return False
    # По умолчанию SPIKE тоже под размер — иначе фильтр только в orchestrator.
    if bool(block.get("skip_fast_sources", False)):
        for needle in ("spike", "pump_dump", "agent_world", "world_feed"):
            if needle in src:
                return False
    apply = block.get("apply_to_sources")
    if isinstance(apply, list) and apply:
        allowed = {str(x).strip().lower() for x in apply if str(x).strip()}
        return src in allowed or any(a in src for a in allowed)
    return True


def _periods_per_year(interval: str) -> float:
    key = str(interval or "15").strip().lower()
    mapping = {
        "1": 365.0 * 24 * 60,
        "3": 365.0 * 24 * 20,
        "5": 365.0 * 24 * 12,
        "15": 365.0 * 24 * 4,
        "30": 365.0 * 24 * 2,
        "60": 365.0 * 24,
        "1h": 365.0 * 24,
        "120": 365.0 * 12,
        "240": 365.0 * 6,
        "4h": 365.0 * 6,
        "d": 365.0,
        "1d": 365.0,
        "day": 365.0,
    }
    return float(mapping.get(key, 365.0 * 24 * 4))


def closes_from_klines(klines: Sequence[Mapping[str, Any]]) -> List[float]:
    out: List[float] = []
    for row in klines:
        try:
            c = float(row.get("close", 0) or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out.append(c)
    return out


def log_returns(closes: Sequence[float]) -> List[float]:
    rets: List[float] = []
    for i in range(1, len(closes)):
        a = float(closes[i - 1])
        b = float(closes[i])
        if a <= 0 or b <= 0:
            continue
        rets.append(math.log(b / a))
    return rets


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def garch11_variance_path(
    returns: Sequence[float],
    *,
    alpha: float = 0.08,
    beta: float = 0.90,
) -> Tuple[List[float], float]:
    """
    GARCH(1,1) с variance targeting.
    Возвращает (список условных дисперсий, прогноз следующей дисперсии).
    """
    if len(returns) < 10:
        return [], 0.0
    a = _clamp(float(alpha), 0.01, 0.30)
    b = _clamp(float(beta), 0.50, 0.98)
    if a + b >= 0.999:
        scale = 0.99 / (a + b)
        a *= scale
        b *= scale
    uvar = sum(r * r for r in returns) / len(returns)
    if uvar <= 0:
        return [], 0.0
    omega = uvar * (1.0 - a - b)
    if omega <= 0:
        omega = uvar * 0.01
    var_t = uvar
    path: List[float] = []
    for r in returns:
        var_t = omega + a * (r * r) + b * var_t
        if var_t < 1e-18:
            var_t = 1e-18
        path.append(var_t)
    next_var = omega + a * (returns[-1] * returns[-1]) + b * path[-1]
    if next_var < 1e-18:
        next_var = 1e-18
    return path, next_var


def _percentile_rank(values: Sequence[float], current: float) -> float:
    if not values:
        return 50.0
    n = len(values)
    below = sum(1 for v in values if v <= current)
    return 100.0 * below / n


def classify_regime(
    percentile: float,
    *,
    calm_percentile: float = 30.0,
    storm_percentile: float = 70.0,
) -> str:
    calm_p = _clamp(float(calm_percentile), 5.0, 45.0)
    storm_p = _clamp(float(storm_percentile), 55.0, 95.0)
    if storm_p <= calm_p:
        storm_p = min(95.0, calm_p + 20.0)
    if percentile <= calm_p:
        return "calm"
    if percentile >= storm_p:
        return "storm"
    return "normal"


def regime_size_mult(regime: str, block: Mapping[str, Any]) -> float:
    regimes = block.get("regimes") if isinstance(block.get("regimes"), dict) else {}
    defaults = {"calm": 1.25, "normal": 1.0, "storm": 0.50, "unknown": 1.0}
    key = str(regime or "unknown").lower()
    try:
        raw = float(regimes.get(key, defaults.get(key, 1.0)))
    except (TypeError, ValueError):
        raw = float(defaults.get(key, 1.0))
    lo = float(block.get("clamp_min", 0.35) or 0.35)
    hi = float(block.get("clamp_max", 1.50) or 1.50)
    if lo > hi:
        lo, hi = hi, lo
    return _clamp(raw, lo, hi)


def compute_volatility_regime(
    closes: Sequence[float],
    block: Mapping[str, Any],
) -> VolatilityRegimeResult:
    """Синхронный расчёт по ряду close (для тестов и кэша klines)."""
    if not bool(block.get("enabled", False)):
        return VolatilityRegimeResult(
            enabled=False,
            regime="disabled",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason="disabled",
            apply=False,
        )

    min_bars = int(block.get("min_bars", 80) or 80)
    if len(closes) < min_bars:
        return VolatilityRegimeResult(
            enabled=True,
            regime="unknown",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason=f"мало_свечей={len(closes)}<{min_bars}",
            apply=False,
        )

    rets = log_returns(closes)
    if len(rets) < max(20, min_bars // 2):
        return VolatilityRegimeResult(
            enabled=True,
            regime="unknown",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason=f"мало_доходностей={len(rets)}",
            apply=False,
        )

    alpha = float(block.get("alpha", 0.08) or 0.08)
    beta = float(block.get("beta", 0.90) or 0.90)
    path, next_var = garch11_variance_path(rets, alpha=alpha, beta=beta)
    if not path or next_var <= 0:
        return VolatilityRegimeResult(
            enabled=True,
            regime="unknown",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason="garch_fail",
            apply=False,
        )

    interval = str(block.get("kline_interval", "15") or "15")
    ppy = _periods_per_year(interval)
    sigmas = [math.sqrt(v) for v in path]
    forecast_sigma = math.sqrt(next_var)
    forecast_ann = forecast_sigma * math.sqrt(ppy)
    pct = _percentile_rank(sigmas, forecast_sigma)
    regime = classify_regime(
        pct,
        calm_percentile=float(block.get("calm_percentile", 30) or 30),
        storm_percentile=float(block.get("storm_percentile", 70) or 70),
    )
    mult = regime_size_mult(regime, block)

    # Опционально: множитель target_vol / forecast (как у Deutscher).
    mode = str(block.get("mode", "regime") or "regime").lower()
    if mode in ("vol_target", "hybrid"):
        target = float(block.get("target_ann_vol", 0.60) or 0.60)
        if forecast_ann > 1e-9 and target > 0:
            vt = target / forecast_ann
            lo = float(block.get("clamp_min", 0.35) or 0.35)
            hi = float(block.get("clamp_max", 1.50) or 1.50)
            vt = _clamp(vt, lo, hi)
            if mode == "vol_target":
                mult = vt
            else:
                mult = _clamp(0.5 * mult + 0.5 * vt, lo, hi)

    advisory = bool(block.get("advisory_only", False))
    block_storm = bool(block.get("block_on_storm", False)) and regime == "storm"
    apply = (not advisory) and (not block_storm)
    reason = (
        f"garch σ_ann={forecast_ann:.3f} pct={pct:.0f} "
        f"α={alpha:.2f} β={beta:.2f} mode={mode}"
    )
    return VolatilityRegimeResult(
        enabled=True,
        regime=regime,
        size_mult=mult if apply else 1.0,
        forecast_vol_ann=forecast_ann,
        percentile=pct,
        reason=reason,
        apply=apply,
        block_entry=block_storm,
    )


async def evaluate_volatility_regime_sizing(
    *,
    exchange: Any,
    symbol: str,
    cfg: Mapping[str, Any],
    side: str = "",
    source: str = "",
    klines: Optional[Sequence[Mapping[str, Any]]] = None,
) -> VolatilityRegimeResult:
    """
    Полный путь: при необходимости тянет klines с биржи.
    Если disabled / источник не подходит → mult=1, apply=False.
    """
    block = read_volatility_regime_cfg(cfg)
    if not bool(block.get("enabled", False)):
        return VolatilityRegimeResult(
            enabled=False,
            regime="disabled",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason="disabled",
            apply=False,
        )
    if not _source_applies(source, block):
        return VolatilityRegimeResult(
            enabled=True,
            regime="unknown",
            size_mult=1.0,
            forecast_vol_ann=0.0,
            percentile=50.0,
            reason=f"source_skip={source or '-'}",
            apply=False,
        )

    rows: Sequence[Mapping[str, Any]] = klines or []
    if not rows and exchange is not None and hasattr(exchange, "get_klines"):
        interval = str(block.get("kline_interval", "15") or "15")
        limit = int(block.get("lookback_bars", 200) or 200)
        try:
            rows = await exchange.get_klines(
                str(symbol).upper(), interval=interval, limit=limit
            ) or []
        except Exception as exc:
            logger.warning("%s: klines failed %s: %s", _LOG_MARKER, symbol, exc)
            return VolatilityRegimeResult(
                enabled=True,
                regime="unknown",
                size_mult=1.0,
                forecast_vol_ann=0.0,
                percentile=50.0,
                reason=f"klines_error={exc}",
                apply=False,
            )

    closes = closes_from_klines(rows)
    result = compute_volatility_regime(closes, block)
    # Если advisory — оставляем «информационный» mult в reason, size_mult=1.
    if bool(block.get("advisory_only", False)) and result.enabled:
        info_mult = regime_size_mult(result.regime, block)
        result = VolatilityRegimeResult(
            enabled=True,
            regime=result.regime,
            size_mult=1.0,
            forecast_vol_ann=result.forecast_vol_ann,
            percentile=result.percentile,
            reason=f"{result.reason} advisory_mult={info_mult:.2f}",
            apply=False,
            block_entry=False,
        )

    logger.info(
        "%s %s %s: %s mult=%.2f apply=%s block=%s %s",
        _LOG_MARKER,
        str(symbol or "").upper(),
        str(side or "-").upper(),
        result.regime,
        result.size_mult if result.apply else (
            regime_size_mult(result.regime, block) if result.regime not in ("disabled",) else 1.0
        ),
        result.apply,
        result.block_entry,
        result.reason,
    )
    return result
