"""
Hermes / PRD-BOT: анализ сигналов и сделок — TP, SL, безубыток.

Источники:
- skipped_backtest/results.jsonl — виртуальные исходы пропущенных сигналов
- trade_history.jsonl — реальные сделки с entry_context
- signal_ledger.jsonl — причины пропуска, raw
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from prd_agent.entry.entry_soft_rules import (
    POSITIVE_RULE_IDS,
    RULE_POINTS,
    detect_active_rules,
)

logger = logging.getLogger("prd_agent.learning.winning_rules")

OutcomeQuality = Literal["profit", "loss", "neutral"]

NUMERIC_FEATURES = (
    "confidence",
    "atr_pct",
    "adx",
    "rsi",
    "normalized_imbalance",
    "spread_pct",
    "rr_at_entry",
    "soft_score",
    "local_hour",
    "volume_24h_usdt",
)

CATEGORICAL_FEATURES = (
    "regime",
    "trend",
    "htf_trend",
    "volatility",
    "entry_zone",
    "side",
    "source",
    "soft_label",
    "skip_reason_bucket",
)

ALL_SOFT_RULE_IDS = tuple(RULE_POINTS.keys())


@dataclass
class WinningSignalRecord:
    signal_id: str
    symbol: str
    side: str
    source: str
    opened_on_exchange: bool
    skip_reason: str
    outcome: str
    outcome_quality: OutcomeQuality
    pnl_pct: float
    pnl_usdt: float
    features: Dict[str, Any]
    origin: str
    active_rules: List[str] = field(default_factory=list)
    signal_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RuleSuggestion:
    rule_id: str
    field: str
    operator: str
    value: Any
    support_pct: float
    loser_support_pct: float
    description_ru: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FilterImpactStat:
    filter_id: str
    filter_kind: str
    n_profit: int
    n_loss: int
    n_neutral: int
    win_rate_pct: float
    avg_pnl_pct: float
    baseline_win_rate_pct: float
    lift_pct: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WeightRecommendation:
    filter_id: str
    action: str
    suggested_weight_mult: float
    confidence: str
    reason_ru: str
    n_samples: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkipFilterReview:
    skip_bucket: str
    n_total: int
    n_virtual_profit: int
    n_virtual_loss: int
    n_virtual_neutral: int
    virtual_tp_rate_pct: float
    recommendation: str
    reason_ru: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WinningEntryRulesReport:
    hours: float
    tp_winners: int
    tp_skipped_virtual: int
    tp_opened_real: int
    sl_losers: int
    outcome_counts: Dict[str, int] = field(default_factory=dict)
    rules: List[RuleSuggestion] = field(default_factory=list)
    winner_feature_medians: Dict[str, float] = field(default_factory=dict)
    top_skip_reasons_on_tp: Dict[str, int] = field(default_factory=dict)
    filter_impacts: List[FilterImpactStat] = field(default_factory=list)
    weight_recommendations: List[WeightRecommendation] = field(default_factory=list)
    skip_filter_reviews: List[SkipFilterReview] = field(default_factory=list)
    suggested_rule_weights: Dict[str, float] = field(default_factory=dict)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hours": self.hours,
            "tp_winners": self.tp_winners,
            "tp_skipped_virtual": self.tp_skipped_virtual,
            "tp_opened_real": self.tp_opened_real,
            "sl_losers": self.sl_losers,
            "outcome_counts": self.outcome_counts,
            "rules": [r.to_dict() for r in self.rules],
            "winner_feature_medians": self.winner_feature_medians,
            "top_skip_reasons_on_tp": self.top_skip_reasons_on_tp,
            "filter_impacts": [f.to_dict() for f in self.filter_impacts],
            "weight_recommendations": [w.to_dict() for w in self.weight_recommendations],
            "skip_filter_reviews": [s.to_dict() for s in self.skip_filter_reviews],
            "suggested_rule_weights": self.suggested_rule_weights,
            "generated_at": self.generated_at,
        }


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _parse_ts(row: Mapping[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        raw = row.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            continue
    return None


def classify_outcome_quality(
    *,
    pnl_usdt: float = 0.0,
    pnl_pct: float = 0.0,
    simulated_outcome: str = "",
    neutral_usdt: float = 0.35,
    neutral_pct: float = 0.04,
) -> OutcomeQuality:
    sim = str(simulated_outcome or "").lower()
    if sim == "take_profit":
        return "profit"
    if sim == "stop_loss":
        return "loss"
    if sim in ("still_open", "no_data", "expired", "invalid"):
        return "neutral"
    if abs(pnl_usdt) <= neutral_usdt and abs(pnl_pct) <= neutral_pct:
        return "neutral"
    if pnl_usdt > neutral_usdt or pnl_pct > neutral_pct:
        return "profit"
    if pnl_usdt < -neutral_usdt or pnl_pct < -neutral_pct:
        return "loss"
    return "neutral"


def skip_reason_bucket(reason: str) -> str:
    r = str(reason or "").strip().lower()
    if not r:
        return "unknown"
    if "quality_gate" in r:
        return "quality_gate"
    if "orderflow" in r:
        return "orderflow"
    if "supervisor_v4" in r or "supervisor" in r:
        return "supervisor"
    if "позиция уже открыта" in r or "position" in r:
        return "position_open"
    if "volume_guard" in r or "score_below" in r:
        return "entry_engine"
    if "derivatives" in r:
        return "derivatives_guard"
    if "pullback" in r:
        return "pullback_entry"
    if ":" in r:
        return r.split(":", 1)[0].strip()[:40]
    return r[:40]


def _flatten_entry_context(ctx: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(ctx)
    filters = ctx.get("filters")
    if isinstance(filters, dict):
        for k, v in filters.items():
            if k == "signal_raw" and isinstance(v, dict):
                for sk, sv in v.items():
                    if isinstance(sv, (int, float, str, bool)):
                        out[f"raw_{sk}"] = sv
            elif isinstance(v, (int, float, str, bool)):
                out[f"filter_{k}"] = v
    return out


def _features_from_ledger_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    feats: Dict[str, Any] = {
        "confidence": float(row.get("confidence", 0) or 0),
        "side": str(row.get("side", "") or "").upper(),
        "source": str(row.get("source", "") or ""),
        "skip_reason_bucket": skip_reason_bucket(str(row.get("reason", ""))),
    }
    raw = row.get("raw")
    if isinstance(raw, dict):
        for key in ("regime", "trend", "htf_trend", "atr_pct", "rsi", "adx"):
            if key in raw:
                feats[key] = raw[key]
        if "imbalance" in raw:
            feats["normalized_imbalance"] = raw.get("imbalance")
    entry = float(row.get("entry", 0) or 0)
    sl = float(row.get("stop_loss", 0) or 0)
    tp = float(row.get("take_profit", 0) or 0)
    if entry > 0 and sl > 0 and tp > 0:
        side = feats.get("side", "BUY")
        if side in ("BUY", "LONG"):
            risk = abs(entry - sl)
            reward = abs(tp - entry)
        else:
            risk = abs(sl - entry)
            reward = abs(entry - tp)
        if risk > 0:
            feats["rr_at_entry"] = round(reward / risk, 4)
    return feats


def _active_rules_from_features(
    feats: Mapping[str, Any], side: str, tz_offset: int = 3
) -> List[str]:
    stored = feats.get("active_rules")
    if isinstance(stored, list) and stored:
        return [str(r) for r in stored if r]
    ctx = dict(feats)
    return detect_active_rules(ctx, side=side, tz_offset=tz_offset)


def _build_record(
    *,
    signal_id: str,
    symbol: str,
    side: str,
    source: str,
    opened_on_exchange: bool,
    skip_reason: str,
    outcome: str,
    outcome_quality: OutcomeQuality,
    pnl_pct: float,
    pnl_usdt: float,
    feats: Dict[str, Any],
    origin: str,
    signal_at: str = "",
    tz_offset: int = 3,
) -> WinningSignalRecord:
    rules = _active_rules_from_features(feats, side, tz_offset=tz_offset)
    return WinningSignalRecord(
        signal_id=signal_id,
        symbol=symbol.upper(),
        side=side,
        source=source,
        opened_on_exchange=opened_on_exchange,
        skip_reason=skip_reason[:200],
        outcome=outcome,
        outcome_quality=outcome_quality,
        pnl_pct=pnl_pct,
        pnl_usdt=pnl_usdt,
        features=feats,
        origin=origin,
        active_rules=rules,
        signal_at=signal_at,
    )


def _load_ledger_index(ledger_path: Path) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(ledger_path):
        lid = str(row.get("id", "") or "")
        if lid:
            idx[lid] = row
    return idx


def _feats_for_skipped_row(
    row: Mapping[str, Any], ledger_index: Mapping[str, Dict[str, Any]]
) -> Dict[str, Any]:
    lid = str(row.get("ledger_id", "") or "")
    ledger_row = ledger_index.get(lid, {})
    ctx = ledger_row.get("entry_context")
    if isinstance(ctx, dict) and ctx:
        feats = _flatten_entry_context(ctx)
    elif ledger_row:
        feats = _features_from_ledger_row(ledger_row)
    else:
        feats = {}
    feats.setdefault("skip_reason_bucket", skip_reason_bucket(str(row.get("skip_reason", ""))))
    if row.get("source"):
        feats.setdefault("source", row.get("source"))
    feats["pnl_pct"] = float(row.get("pnl_pct_net", row.get("pnl_pct", 0)) or 0)
    return feats


def load_skipped_records(
    *,
    skipped_path: Path,
    ledger_index: Mapping[str, Dict[str, Any]],
    hours: float,
    outcomes: Optional[Sequence[str]] = None,
) -> List[WinningSignalRecord]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    allow = set(outcomes) if outcomes else None
    out: List[WinningSignalRecord] = []
    for row in _read_jsonl(skipped_path):
        oc = str(row.get("outcome", ""))
        if allow is not None and oc not in allow:
            continue
        ts = _parse_ts(row, "backtested_at", "signal_at")
        if ts is not None and ts < cutoff:
            continue
        feats = _feats_for_skipped_row(row, ledger_index)
        quality = classify_outcome_quality(
            pnl_pct=feats.get("pnl_pct", 0), simulated_outcome=oc
        )
        lid = str(row.get("ledger_id", "") or "")
        out.append(
            _build_record(
                signal_id=lid or f"skip-{row.get('symbol')}",
                symbol=str(row.get("symbol", "")),
                side=str(row.get("side", "")),
                source=str(row.get("source", "") or feats.get("source", "")),
                opened_on_exchange=False,
                skip_reason=str(row.get("skip_reason", "")),
                outcome=oc,
                outcome_quality=quality,
                pnl_pct=float(feats.get("pnl_pct", 0)),
                pnl_usdt=0.0,
                feats=feats,
                origin="skipped_backtest",
                signal_at=str(row.get("signal_at", "")),
            )
        )
    return out


def load_skipped_tp_winners(
    *, skipped_path: Path, ledger_index: Mapping[str, Dict[str, Any]], hours: float
) -> List[WinningSignalRecord]:
    return load_skipped_records(
        skipped_path=skipped_path,
        ledger_index=ledger_index,
        hours=hours,
        outcomes=("take_profit",),
    )


def load_skipped_sl_losers(
    *, skipped_path: Path, ledger_index: Mapping[str, Dict[str, Any]], hours: float
) -> List[WinningSignalRecord]:
    return load_skipped_records(
        skipped_path=skipped_path,
        ledger_index=ledger_index,
        hours=hours,
        outcomes=("stop_loss",),
    )


def load_journal_closed_records(*, journal_path: Path, hours: float) -> List[WinningSignalRecord]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    pending: Dict[str, Dict[str, Any]] = {}
    out: List[WinningSignalRecord] = []
    for row in _read_jsonl(journal_path):
        ev = str(row.get("event", "")).lower()
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        if ev == "entered":
            pending[f"{sym}:{row.get('side', '')}"] = row
            continue
        if ev != "closed":
            continue
        ts = _parse_ts(row, "ts", "closed_at", "time")
        if ts is not None and ts < cutoff:
            pending.pop(f"{sym}:{row.get('side', '')}", None)
            continue
        side = str(row.get("side", "") or "")
        ent = pending.pop(f"{sym}:{side}", None) or pending.pop(sym, None) or {}
        ctx = ent.get("entry_context") or row.get("entry_context")
        feats = _flatten_entry_context(ctx) if isinstance(ctx, dict) else {}
        feats.setdefault("side", side.upper())
        feats.setdefault("source", ent.get("source") or row.get("source") or "")
        feats.setdefault("confidence", float(ent.get("confidence") or row.get("confidence") or 0))
        pnl_usdt = float(row.get("pnl") or row.get("pnl_usdt") or 0)
        pnl_pct = float(row.get("pnl_pct") or 0)
        if not pnl_pct and pnl_usdt and float(ent.get("entry") or 0):
            try:
                pnl_pct = pnl_usdt / float(ent.get("entry", 1)) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                pnl_pct = 0.0
        quality = classify_outcome_quality(pnl_usdt=pnl_usdt, pnl_pct=pnl_pct)
        reason = str(row.get("reason", "") or "").lower()
        outcome = "closed"
        if "take_profit" in reason or "tp_" in reason:
            outcome = "take_profit"
        elif "stop_loss" in reason or "sl_" in reason:
            outcome = "stop_loss"
        out.append(
            _build_record(
                signal_id=str(ent.get("order_id") or row.get("order_id") or sym),
                symbol=sym,
                side=side,
                source=str(feats.get("source", "")),
                opened_on_exchange=True,
                skip_reason="",
                outcome=outcome,
                outcome_quality=quality,
                pnl_pct=pnl_pct,
                pnl_usdt=pnl_usdt,
                feats=feats,
                origin="trade_journal",
                signal_at=str(ent.get("ts") or row.get("ts") or ""),
            )
        )
    return out


def load_journal_tp_winners(*, journal_path: Path, hours: float) -> List[WinningSignalRecord]:
    return [r for r in load_journal_closed_records(journal_path=journal_path, hours=hours) if r.outcome_quality == "profit"]


def _numeric_values(records: Sequence[WinningSignalRecord], field: str) -> List[float]:
    vals: List[float] = []
    for rec in records:
        raw = rec.features.get(field)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v == v:
            vals.append(v)
    return vals


def _categorical_counts(records: Sequence[WinningSignalRecord], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for rec in records:
        val = rec.features.get(field)
        if val is None or val == "":
            continue
        key = str(val).lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _pct_match(records: Sequence[WinningSignalRecord], field: str, op: str, value: float) -> float:
    if not records:
        return 0.0
    matched = 0
    total = 0
    for rec in records:
        raw = rec.features.get(field)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        total += 1
        if op == ">=" and v >= value:
            matched += 1
        elif op == "<=" and v <= value:
            matched += 1
    return round(matched / max(total, 1) * 100, 1)


def mine_rules(
    winners: Sequence[WinningSignalRecord],
    losers: Sequence[WinningSignalRecord],
    *,
    min_winner_support: float = 55.0,
    min_contrast_gap: float = 12.0,
) -> List[RuleSuggestion]:
    rules: List[RuleSuggestion] = []
    if len(winners) < 3:
        return rules

    for field in NUMERIC_FEATURES:
        w_vals = _numeric_values(winners, field)
        if len(w_vals) < 3:
            continue
        median = statistics.median(w_vals)
        p25 = statistics.quantiles(w_vals, n=4)[0] if len(w_vals) >= 4 else min(w_vals)
        threshold = round(p25, 4) if field != "local_hour" else int(p25)
        op = ">="
        l_vals = _numeric_values(losers, field)
        if field in ("spread_pct", "atr_pct") and l_vals and median < statistics.median(l_vals):
            op = "<="
            threshold = round(
                statistics.quantiles(w_vals, n=4)[2] if len(w_vals) >= 4 else max(w_vals), 4
            )
        w_pct = _pct_match(winners, field, op, float(threshold))
        l_pct = _pct_match(losers, field, op, float(threshold))
        if w_pct < min_winner_support:
            continue
        if losers and (w_pct - l_pct) < min_contrast_gap:
            continue
        rules.append(
            RuleSuggestion(
                rule_id=f"{field}_{op}",
                field=field,
                operator=op,
                value=threshold,
                support_pct=w_pct,
                loser_support_pct=l_pct,
                description_ru=(
                    f"У {w_pct:.0f}% удачных TP {field} {op} {threshold} "
                    f"(медиана {median:.4g}, у SL {l_pct:.0f}%)"
                ),
            )
        )

    for field in CATEGORICAL_FEATURES:
        w_counts = _categorical_counts(winners, field)
        if not w_counts:
            continue
        total_w = sum(w_counts.values())
        best_val, best_n = max(w_counts.items(), key=lambda x: x[1])
        support = best_n / max(total_w, 1) * 100
        if support < 60.0:
            continue
        l_counts = _categorical_counts(losers, field)
        l_total = sum(l_counts.values()) or 1
        l_support = l_counts.get(best_val, 0) / l_total * 100
        if losers and (support - l_support) < min_contrast_gap:
            continue
        rules.append(
            RuleSuggestion(
                rule_id=f"{field}_is_{best_val}",
                field=field,
                operator="==",
                value=best_val,
                support_pct=round(support, 1),
                loser_support_pct=round(l_support, 1),
                description_ru=(
                    f"В {support:.0f}% удачных TP {field}={best_val} (у SL {l_support:.0f}%)"
                ),
            )
        )

    rules.sort(key=lambda r: (-r.support_pct, r.support_pct - r.loser_support_pct))
    return rules[:12]


def _baseline_win_rate(records: Sequence[WinningSignalRecord]) -> float:
    profit = sum(1 for r in records if r.outcome_quality == "profit")
    loss = sum(1 for r in records if r.outcome_quality == "loss")
    if profit + loss == 0:
        return 50.0
    return profit / (profit + loss) * 100.0


def analyze_soft_rule_impacts(
    records: Sequence[WinningSignalRecord],
    *,
    min_samples: int = 4,
) -> List[FilterImpactStat]:
    if not records:
        return []
    baseline = _baseline_win_rate(records)
    stats: List[FilterImpactStat] = []

    for rule_id in ALL_SOFT_RULE_IDS:
        active_recs = [r for r in records if rule_id in r.active_rules]
        if len(active_recs) < min_samples:
            continue
        n_p = sum(1 for r in active_recs if r.outcome_quality == "profit")
        n_l = sum(1 for r in active_recs if r.outcome_quality == "loss")
        n_n = sum(1 for r in active_recs if r.outcome_quality == "neutral")
        decisive = n_p + n_l
        if decisive < 2:
            continue
        wr = n_p / decisive * 100.0
        avg_pnl = statistics.mean([r.pnl_pct for r in active_recs]) if active_recs else 0.0
        stats.append(
            FilterImpactStat(
                filter_id=rule_id,
                filter_kind="soft_rule",
                n_profit=n_p,
                n_loss=n_l,
                n_neutral=n_n,
                win_rate_pct=round(wr, 1),
                avg_pnl_pct=round(avg_pnl, 4),
                baseline_win_rate_pct=round(baseline, 1),
                lift_pct=round(wr - baseline, 1),
            )
        )
    stats.sort(key=lambda s: (-s.lift_pct, -s.win_rate_pct))
    return stats


def analyze_indicator_impacts(
    records: Sequence[WinningSignalRecord],
    *,
    min_samples: int = 5,
) -> List[FilterImpactStat]:
    if not records:
        return []
    baseline = _baseline_win_rate(records)
    out: List[FilterImpactStat] = []
    for field in NUMERIC_FEATURES:
        groups: Dict[str, List[WinningSignalRecord]] = {"high": [], "low": []}
        vals = [(r, float(r.features.get(field, 0) or 0)) for r in records if field in r.features]
        if len(vals) < min_samples * 2:
            continue
        med = statistics.median([v for _, v in vals])
        for rec, v in vals:
            groups["high" if v >= med else "low"].append(rec)
        for band, band_recs in groups.items():
            if len(band_recs) < min_samples:
                continue
            n_p = sum(1 for r in band_recs if r.outcome_quality == "profit")
            n_l = sum(1 for r in band_recs if r.outcome_quality == "loss")
            n_n = sum(1 for r in band_recs if r.outcome_quality == "neutral")
            if n_p + n_l < 2:
                continue
            wr = n_p / (n_p + n_l) * 100
            out.append(
                FilterImpactStat(
                    filter_id=f"{field}_{band}",
                    filter_kind="indicator",
                    n_profit=n_p,
                    n_loss=n_l,
                    n_neutral=n_n,
                    win_rate_pct=round(wr, 1),
                    avg_pnl_pct=round(statistics.mean([r.pnl_pct for r in band_recs]), 4),
                    baseline_win_rate_pct=round(baseline, 1),
                    lift_pct=round(wr - baseline, 1),
                )
            )
    out.sort(key=lambda s: -abs(s.lift_pct))
    return out[:16]


def analyze_skip_filter_reviews(
    skipped: Sequence[WinningSignalRecord],
    *,
    min_total: int = 3,
    relax_tp_rate: float = 58.0,
    keep_sl_rate: float = 55.0,
) -> List[SkipFilterReview]:
    groups: Dict[str, List[WinningSignalRecord]] = {}
    for rec in skipped:
        bucket = skip_reason_bucket(rec.skip_reason) or rec.features.get("skip_reason_bucket", "unknown")
        groups.setdefault(str(bucket), []).append(rec)

    reviews: List[SkipFilterReview] = []
    for bucket, rows in groups.items():
        if len(rows) < min_total:
            continue
        n_p = sum(1 for r in rows if r.outcome_quality == "profit")
        n_l = sum(1 for r in rows if r.outcome_quality == "loss")
        n_n = sum(1 for r in rows if r.outcome_quality == "neutral")
        decisive = n_p + n_l
        tp_rate = (n_p / decisive * 100.0) if decisive else 0.0
        sl_rate = (n_l / decisive * 100.0) if decisive else 0.0

        if tp_rate >= relax_tp_rate and n_p >= 2:
            rec_action = "consider_relax"
            reason = (
                f"Фильтр «{bucket}» отсек {len(rows)} сигналов; "
                f"{tp_rate:.0f}% дошли бы до TP — возможно слишком жёстко."
            )
        elif sl_rate >= keep_sl_rate and n_l >= 2:
            rec_action = "keep_strict"
            reason = (
                f"Фильтр «{bucket}» спасает от SL в {sl_rate:.0f}% случаев — оставить."
            )
        else:
            rec_action = "review"
            reason = f"Смешанный эффект фильтра «{bucket}» — нужно больше данных."

        reviews.append(
            SkipFilterReview(
                skip_bucket=bucket,
                n_total=len(rows),
                n_virtual_profit=n_p,
                n_virtual_loss=n_l,
                n_virtual_neutral=n_n,
                virtual_tp_rate_pct=round(tp_rate, 1),
                recommendation=rec_action,
                reason_ru=reason,
            )
        )
    reviews.sort(key=lambda x: (-x.virtual_tp_rate_pct, -x.n_total))
    return reviews


def build_weight_recommendations(
    impacts: Sequence[FilterImpactStat],
    skip_reviews: Sequence[SkipFilterReview],
    *,
    max_weight: float = 1.35,
) -> Tuple[List[WeightRecommendation], Dict[str, float]]:
    recs: List[WeightRecommendation] = []
    weights: Dict[str, float] = {}

    for imp in impacts:
        if imp.filter_kind != "soft_rule":
            continue
        n = imp.n_profit + imp.n_loss
        base_pts = RULE_POINTS.get(imp.filter_id, 0.0)
        is_positive = imp.filter_id in POSITIVE_RULE_IDS and base_pts > 0
        is_penalty = base_pts < 0

        if is_positive:
            if n >= 5 and imp.win_rate_pct >= 58 and imp.lift_pct >= 8:
                mult = min(max_weight, 1.0 + min(0.35, imp.lift_pct / 100.0 * 1.5))
                recs.append(
                    WeightRecommendation(
                        filter_id=imp.filter_id,
                        action="increase_weight",
                        suggested_weight_mult=round(mult, 3),
                        confidence="high" if n >= 8 else "medium",
                        reason_ru=(
                            f"Правило «{imp.filter_id}»: WR {imp.win_rate_pct:.0f}% "
                            f"(+{imp.lift_pct:.0f}% к базе) — усилить вес."
                        ),
                        n_samples=n,
                    )
                )
                weights[imp.filter_id] = round(mult, 3)
            elif n >= 5 and imp.win_rate_pct < 45:
                recs.append(
                    WeightRecommendation(
                        filter_id=imp.filter_id,
                        action="decrease_weight",
                        suggested_weight_mult=1.0,
                        confidence="medium",
                        reason_ru=(
                            f"Правило «{imp.filter_id}»: WR {imp.win_rate_pct:.0f}% — "
                            f"снизить влияние или отключить."
                        ),
                        n_samples=n,
                    )
                )
            else:
                recs.append(
                    WeightRecommendation(
                        filter_id=imp.filter_id,
                        action="keep",
                        suggested_weight_mult=1.0,
                        confidence="low",
                        reason_ru=f"Правило «{imp.filter_id}»: недостаточно преимущества для смены веса.",
                        n_samples=n,
                    )
                )
        elif is_penalty:
            if n >= 5 and imp.n_loss > imp.n_profit * 1.5:
                recs.append(
                    WeightRecommendation(
                        filter_id=imp.filter_id,
                        action="keep",
                        suggested_weight_mult=1.0,
                        confidence="medium",
                        reason_ru=f"Штраф «{imp.filter_id}» чаще при убытках — оставить.",
                        n_samples=n,
                    )
                )
            elif n >= 5 and imp.n_profit > imp.n_loss:
                recs.append(
                    WeightRecommendation(
                        filter_id=imp.filter_id,
                        action="consider_remove",
                        suggested_weight_mult=0.0,
                        confidence="medium",
                        reason_ru=(
                            f"Штраф «{imp.filter_id}» чаще при профите — "
                            f"возможно мешает хорошим входам."
                        ),
                        n_samples=n,
                    )
                )

    for review in skip_reviews:
        if review.recommendation == "consider_relax":
            recs.append(
                WeightRecommendation(
                    filter_id=f"skip:{review.skip_bucket}",
                    action="consider_remove",
                    suggested_weight_mult=0.0,
                    confidence="high" if review.virtual_tp_rate_pct >= 65 else "medium",
                    reason_ru=review.reason_ru,
                    n_samples=review.n_total,
                )
            )
        elif review.recommendation == "keep_strict":
            recs.append(
                WeightRecommendation(
                    filter_id=f"skip:{review.skip_bucket}",
                    action="keep",
                    suggested_weight_mult=1.0,
                    confidence="medium",
                    reason_ru=review.reason_ru,
                    n_samples=review.n_total,
                )
            )

    recs.sort(
        key=lambda r: (
            0 if r.action == "increase_weight" else 1 if r.action == "consider_remove" else 2,
            -r.n_samples,
        )
    )
    return recs[:15], weights


class WinningEntryRulesAnalyzer:
    def __init__(self, data_dir: Path, *, tz_offset: int = 3):
        self.data_dir = Path(data_dir)
        self.tz_offset = tz_offset
        self.learning_dir = self.data_dir / "learning"
        self.learning_dir.mkdir(parents=True, exist_ok=True)

    def paths(self) -> Dict[str, Path]:
        return {
            "skipped_bt": self.data_dir / "supervisor" / "skipped_backtest" / "results.jsonl",
            "ledger": self.data_dir / "ledger" / "signal_ledger.jsonl",
            "journal": self.data_dir / "trades" / "trade_history.jsonl",
        }

    def analyze(self, hours: float = 168.0) -> WinningEntryRulesReport:
        paths = self.paths()
        ledger_index = _load_ledger_index(paths["ledger"])
        skipped_all = load_skipped_records(
            skipped_path=paths["skipped_bt"],
            ledger_index=ledger_index,
            hours=hours,
        )
        journal_all = load_journal_closed_records(journal_path=paths["journal"], hours=hours)
        all_records = skipped_all + journal_all

        skipped_tp = [r for r in skipped_all if r.outcome_quality == "profit"]
        journal_tp = [r for r in journal_all if r.outcome_quality == "profit"]
        losers = [r for r in skipped_all if r.outcome_quality == "loss"]
        winners = skipped_tp + journal_tp

        outcome_counts = {
            "profit": sum(1 for r in all_records if r.outcome_quality == "profit"),
            "loss": sum(1 for r in all_records if r.outcome_quality == "loss"),
            "neutral": sum(1 for r in all_records if r.outcome_quality == "neutral"),
        }

        medians: Dict[str, float] = {}
        for fld in NUMERIC_FEATURES:
            vals = _numeric_values(winners, fld)
            if vals:
                medians[fld] = round(statistics.median(vals), 4)

        skip_top: Dict[str, int] = {}
        for w in skipped_tp:
            bucket = skip_reason_bucket(w.skip_reason)
            skip_top[bucket] = skip_top.get(bucket, 0) + 1

        real_for_rules = journal_all if len(journal_all) >= 3 else all_records
        soft_impacts = analyze_soft_rule_impacts(real_for_rules)
        indicator_impacts = analyze_indicator_impacts(real_for_rules)
        filter_impacts = soft_impacts + indicator_impacts
        skip_reviews = analyze_skip_filter_reviews(skipped_all)
        weight_recs, suggested_weights = build_weight_recommendations(
            filter_impacts, skip_reviews
        )

        return WinningEntryRulesReport(
            hours=hours,
            tp_winners=len(winners),
            tp_skipped_virtual=len(skipped_tp),
            tp_opened_real=len(journal_tp),
            sl_losers=len(losers),
            outcome_counts=outcome_counts,
            rules=mine_rules(winners, losers),
            winner_feature_medians=medians,
            top_skip_reasons_on_tp=dict(sorted(skip_top.items(), key=lambda x: -x[1])[:8]),
            filter_impacts=filter_impacts,
            weight_recommendations=weight_recs,
            skip_filter_reviews=skip_reviews,
            suggested_rule_weights=suggested_weights,
        )

    def save(self, report: WinningEntryRulesReport) -> Tuple[Path, Path]:
        json_path = self.learning_dir / "winning_entry_rules.json"
        md_path = self.learning_dir / "winning_entry_rules_report.md"
        json_path.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md_path.write_text(build_markdown_report(report), encoding="utf-8")
        return json_path, md_path


def analyze_winning_entries(data_dir: Path, hours: float = 168.0) -> WinningEntryRulesReport:
    return WinningEntryRulesAnalyzer(data_dir).analyze(hours=hours)


def build_markdown_report(report: WinningEntryRulesReport) -> str:
    lines = [
        "# Анализ входов: TP, убытки, безубыток",
        "",
        f"Период: **{report.hours:.0f} ч** | Сгенерировано: {report.generated_at}",
        "",
        "## Сводка исходов",
        f"- **Профит:** {report.outcome_counts.get('profit', 0)}",
        f"- **Убыток:** {report.outcome_counts.get('loss', 0)}",
        f"- **Безубыток / нейтраль:** {report.outcome_counts.get('neutral', 0)}",
        f"- TP (вирт + биржа): **{report.tp_winners}** "
        f"(вирт {report.tp_skipped_virtual}, биржа {report.tp_opened_real})",
        "",
    ]

    if report.weight_recommendations:
        lines.append("## Рекомендации по весам и фильтрам")
        action_ru = {
            "increase_weight": "⬆ Усилить вес",
            "decrease_weight": "⬇ Ослабить",
            "consider_remove": "🗑 Рассмотреть отказ",
            "keep": "✓ Оставить",
        }
        for rec in report.weight_recommendations[:12]:
            label = action_ru.get(rec.action, rec.action)
            mult = (
                f" → mult **{rec.suggested_weight_mult}**"
                if rec.suggested_weight_mult and rec.action == "increase_weight"
                else ""
            )
            lines.append(
                f"- {label} `{rec.filter_id}`{mult} "
                f"({rec.confidence}, n={rec.n_samples}): {rec.reason_ru}"
            )
        if report.suggested_rule_weights:
            lines.append("")
            lines.append("### Предлагаемые веса soft-rules (для rule_weight_learning)")
            for k, v in report.suggested_rule_weights.items():
                lines.append(f"- `{k}`: **{v}**")
        lines.append("")

    if report.skip_filter_reviews:
        lines.append("## Пропуски: влияние фильтров (виртуальный исход)")
        for rev in report.skip_filter_reviews[:8]:
            lines.append(
                f"- **{rev.skip_bucket}**: n={rev.n_total}, "
                f"вирт.TP={rev.virtual_tp_rate_pct:.0f}% "
                f"(+{rev.n_virtual_profit}/−{rev.n_virtual_loss}/≈{rev.n_virtual_neutral}) — "
                f"{rev.reason_ru}"
            )
        lines.append("")

    if report.filter_impacts:
        lines.append("## Индикаторы и soft-rules (WR при срабатывании)")
        for imp in report.filter_impacts[:12]:
            lines.append(
                f"- `{imp.filter_id}` ({imp.filter_kind}): "
                f"WR **{imp.win_rate_pct:.0f}%** (lift {imp.lift_pct:+.0f}%), "
                f"+{imp.n_profit}/−{imp.n_loss}/≈{imp.n_neutral}"
            )
        lines.append("")

    if report.top_skip_reasons_on_tp:
        lines.append("## Пропущенные, но дошли бы до TP")
        for reason, cnt in report.top_skip_reasons_on_tp.items():
            lines.append(f"- **{reason}**: {cnt}")
        lines.append("")

    if report.winner_feature_medians:
        lines.append("## Медианы индикаторов у TP-победителей")
        for k, v in report.winner_feature_medians.items():
            lines.append(f"- `{k}`: **{v}**")
        lines.append("")

    if report.rules:
        lines.append("## Правила удачного входа (TP)")
        for i, rule in enumerate(report.rules, 1):
            lines.append(f"### {i}. `{rule.field}` {rule.operator} `{rule.value}`")
            lines.append(f"- {rule.description_ru}")
        lines.append("")

    if not report.rules and not report.weight_recommendations:
        lines.append(
            "_Мало данных. Нужны закрытые сделки с entry_context и "
            "прогоны skipped-backtest._"
        )

    lines.append(
        "> Hermes: **не меняйте config автоматически**. "
        "Одно изменение за цикл (ZeroOne)."
    )
    return "\n".join(lines)


def build_telegram_report(report: WinningEntryRulesReport) -> str:
    lines = [
        f"<b>🎯 Анализ входов ({report.hours:.0f} ч)</b>",
        "",
        f"✅ профит: <b>{report.outcome_counts.get('profit', 0)}</b> | "
        f"❌ убыток: <b>{report.outcome_counts.get('loss', 0)}</b> | "
        f"≈ безубыток: <b>{report.outcome_counts.get('neutral', 0)}</b>",
        "",
    ]
    inc = [r for r in report.weight_recommendations if r.action == "increase_weight"][:3]
    rem = [r for r in report.weight_recommendations if r.action == "consider_remove"][:3]
    if inc:
        lines.append("<b>⬆ Усилить вес</b>")
        for r in inc:
            lines.append(f"• <code>{r.filter_id}</code> ×{r.suggested_weight_mult}")
        lines.append("")
    if rem:
        lines.append("<b>🗑 Ослабить/убрать фильтр</b>")
        for r in rem:
            lines.append(f"• <code>{r.filter_id}</code>")
        lines.append("")
    if report.rules:
        lines.append("<b>Топ TP-правила</b>")
        for rule in report.rules[:4]:
            lines.append(
                f"• <code>{rule.field}</code> {rule.operator} <b>{rule.value}</b>"
            )
    lines.append("")
    lines.append("<i>data/learning/winning_entry_rules_report.md</i>")
    return "\n".join(lines)
