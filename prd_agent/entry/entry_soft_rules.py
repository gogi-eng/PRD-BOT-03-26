"""
Мягкие правила входа (Entry Soft Score) — некатегоричные факторы с базовыми баллами.
Веса правил усиливаются через rule_weight_tracker после 2 недель подтверждённой точности.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

SOFT_SCORE_BASE = 50.0

# id → базовые баллы (отрицательные — штрафы, их вес не усиливаем)
RULE_POINTS: Dict[str, float] = {
    "hour_green": 15.0,
    "hour_red": -12.0,
    "htf_aligned": 18.0,
    "htf_misaligned": -20.0,
    "regime_trend": 15.0,
    "regime_chop": -15.0,
    "adx_ok": 12.0,
    "adx_strong": 8.0,
    "adx_weak": -15.0,
    "atr_sweet": 5.0,
    "atr_extreme": 5.0,
    "imb_strong": 10.0,
    "volume_2x": 5.0,
    "short_green_hour": 5.0,
    "spread_wide": -10.0,
}

POSITIVE_RULE_IDS = frozenset(rid for rid, pts in RULE_POINTS.items() if pts > 0)


@dataclass
class SoftScoreResult:
    score: float
    label: str
    active_rules: List[str] = field(default_factory=list)
    breakdown: Dict[str, float] = field(default_factory=dict)
    size_mult: float = 1.0
    confidence_boost: float = 0.0


def _f(ctx: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(ctx.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _s(ctx: Mapping[str, Any], key: str) -> str:
    return str(ctx.get(key, "") or "").strip().lower()


def _local_hour(ctx: Mapping[str, Any], tz_offset: int = 3) -> int:
    if "local_hour" in ctx:
        try:
            return int(ctx["local_hour"]) % 24
        except (TypeError, ValueError):
            pass
    return 0


def _is_buy(side: str) -> bool:
    return str(side or "").strip().upper() in ("BUY", "LONG")


def detect_active_rules(
    entry_context: Optional[Mapping[str, Any]],
    *,
    side: str = "",
    tz_offset: int = 3,
) -> List[str]:
    """Какие правила сработали на входе (по entry_context)."""
    if not entry_context:
        return []
    ctx = entry_context
    side_u = str(side or ctx.get("side") or "").upper()
    hour = _local_hour(ctx, tz_offset)
    active: List[str] = []

    # Часы по стороне: лаборатория skipped_backtest Buy vs Sell (UTC+3).
    if _is_buy(side_u):
        if hour in (6, 9, 12, 13, 14, 16, 17, 18, 19, 21):
            active.append("hour_green")
        if hour in (3, 4, 5, 10, 20):
            active.append("hour_red")
    else:
        if hour in (4, 5, 7, 10, 14, 16, 17, 19, 20):
            active.append("hour_green")
        if hour in (6, 9, 12, 13, 22):
            active.append("hour_red")

    htf = _s(ctx, "htf_trend")
    htf_bull = htf in ("bullish", "1", "up", "long", "buy")
    htf_bear = htf in ("bearish", "-1", "down", "short", "sell")
    if _is_buy(side_u) and htf_bull:
        active.append("htf_aligned")
    elif (not _is_buy(side_u)) and side_u in ("SELL", "SHORT") and htf_bear:
        active.append("htf_aligned")
    elif (htf_bull or htf_bear) and side_u:
        if (_is_buy(side_u) and htf_bear) or (
            (not _is_buy(side_u)) and htf_bull
        ):
            active.append("htf_misaligned")

    regime = _s(ctx, "regime")
    if regime == "trend":
        active.append("regime_trend")
    elif regime == "chop":
        active.append("regime_chop")

    adx = _f(ctx, "adx")
    if adx >= 24:
        active.append("adx_ok")
        active.append("adx_strong")
    elif adx >= 15:
        active.append("adx_ok")
    elif adx > 0 and adx < 15:
        active.append("adx_weak")

    atr = _f(ctx, "atr_pct")
    if 0.15 <= atr < 1.5:
        active.append("atr_sweet")
    elif atr >= 1.5:
        active.append("atr_extreme")

    imb = abs(_f(ctx, "normalized_imbalance"))
    if 0.25 <= imb < 0.55:
        active.append("imb_strong")

    vol = _f(ctx, "volume_24h_usdt")
    if vol >= 20_000_000:
        active.append("volume_2x")

    if (not _is_buy(side_u)) and side_u in ("SELL", "SHORT") and "hour_green" in active:
        active.append("short_green_hour")

    spread = _f(ctx, "spread_pct")
    if spread >= 0.008:
        active.append("spread_wide")

    return active


def _label_for_score(score: float, cfg: Dict[str, Any]) -> str:
    block = cfg.get("rule_weight_learning", {})
    if not isinstance(block, dict):
        block = {}
    fav = float(block.get("favorable_threshold", 65) or 65)
    neu = float(block.get("neutral_threshold", 50) or 50)
    caution = float(block.get("caution_threshold", 40) or 40)
    if score >= fav:
        return "favorable"
    if score >= neu:
        return "neutral"
    if score >= caution:
        return "caution"
    return "weak"


def compute_soft_score(
    entry_context: Optional[Mapping[str, Any]],
    *,
    side: str = "",
    cfg: Optional[Dict[str, Any]] = None,
    rule_weights: Optional[Mapping[str, float]] = None,
) -> SoftScoreResult:
    """
    Entry Soft Score с учётом обученных весов и ручных weight_overrides из config.

    weight_overrides (rule_weight_learning): множитель к баллам правила (можно < 1 —
    ослабить правила с отрицательным lift по статистике сделок).
    """
    cfg = cfg or {}
    rwl = cfg.get("rule_weight_learning", {})
    if not isinstance(rwl, dict):
        rwl = {}
    tz = int(cfg.get("timezone_offset", 3) or 3)
    weights = dict(rule_weights or {})
    raw_ov = rwl.get("weight_overrides")
    overrides: Dict[str, float] = {}
    if isinstance(raw_ov, Mapping):
        for k, v in raw_ov.items():
            try:
                overrides[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    max_conf_boost = float(rwl.get("max_confidence_boost", 0.05) or 0.05)
    max_size_mult = float(rwl.get("max_size_mult", 1.12) or 1.12)
    max_w = float(rwl.get("max_weight_mult", 1.35) or 1.35)

    active = detect_active_rules(entry_context, side=side, tz_offset=tz)
    breakdown: Dict[str, float] = {}
    total = SOFT_SCORE_BASE
    validated_hits = 0

    for rid in active:
        base = RULE_POINTS.get(rid, 0.0)
        if base == 0.0:
            continue
        mult = 1.0
        if base > 0 and rid in POSITIVE_RULE_IDS:
            mult = float(weights.get(rid, 1.0) or 1.0)
            if rid in overrides:
                mult *= float(overrides[rid])
            mult = max(0.15, min(max_w, mult))
            if mult > 1.001:
                validated_hits += 1
        pts = base * mult
        breakdown[rid] = round(pts, 3)
        total += pts

    score = round(total, 2)
    label = _label_for_score(score, cfg)

    # Небольшой boost размера/confidence за validated правила (не bypass gate)
    boost_factor = min(1.0, validated_hits * 0.25)
    size_mult = 1.0 + min(max_size_mult - 1.0, boost_factor * (max_size_mult - 1.0))
    confidence_boost = min(max_conf_boost, validated_hits * 0.015)

    # Caution/weak раньше не резали размер (только boost ≥1) → caution 47 шёл full size.
    if label == "weak":
        size_mult = min(size_mult, 0.35)
    elif label == "caution":
        # Широкий спред + caution — ещё жёстче (как CBRSUSDT: spread_wide + caution 47)
        cap = 0.35 if "spread_wide" in active else 0.55
        size_mult = min(size_mult, cap)

    return SoftScoreResult(
        score=score,
        label=label,
        active_rules=active,
        breakdown=breakdown,
        size_mult=round(size_mult, 4),
        confidence_boost=round(confidence_boost, 4),
    )
