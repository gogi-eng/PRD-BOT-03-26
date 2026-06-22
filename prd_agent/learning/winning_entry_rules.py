"""
Hermes / PRD-BOT: анализ сигналов, закрывшихся в плюс по TP.

Источники:
- data/supervisor/skipped_backtest/results.jsonl — симуляция пропущенных сигналов
- data/trades/trade_history.jsonl — реальные сделки с entry_context
- data/ledger/signal_ledger.jsonl — причина пропуска, raw сигнала
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("prd_agent.learning.winning_rules")

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


@dataclass
class WinningSignalRecord:
    signal_id: str
    symbol: str
    side: str
    source: str
    opened_on_exchange: bool
    skip_reason: str
    outcome: str
    pnl_pct: float
    features: Dict[str, Any]
    origin: str  # skipped_backtest | trade_journal
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
class WinningEntryRulesReport:
    hours: float
    tp_winners: int
    tp_skipped_virtual: int
    tp_opened_real: int
    sl_losers: int
    rules: List[RuleSuggestion] = field(default_factory=list)
    winner_feature_medians: Dict[str, float] = field(default_factory=dict)
    top_skip_reasons_on_tp: Dict[str, int] = field(default_factory=dict)
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
            "rules": [r.to_dict() for r in self.rules],
            "winner_feature_medians": self.winner_feature_medians,
            "top_skip_reasons_on_tp": self.top_skip_reasons_on_tp,
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


def _is_journal_tp_win(closed: Mapping[str, Any]) -> bool:
    pnl = float(closed.get("pnl") or closed.get("pnl_usdt") or 0)
    if pnl <= 0:
        return False
    reason = str(closed.get("reason", "") or "").lower()
    tp_tokens = (
        "take_profit",
        "tp_",
        "structural_tp",
        "target",
        "tp_hit",
        "profit_target",
        "partial_tp",
    )
    if any(t in reason for t in tp_tokens):
        return True
    bad = ("stop_loss", "sl_", "trend_exit", "early_exit", "liquidation")
    if any(b in reason for b in bad):
        return False
    return pnl > 0


def _load_ledger_index(ledger_path: Path) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(ledger_path):
        lid = str(row.get("id", "") or "")
        if lid:
            idx[lid] = row
    return idx


def load_skipped_tp_winners(
    *,
    skipped_path: Path,
    ledger_index: Mapping[str, Dict[str, Any]],
    hours: float,
) -> List[WinningSignalRecord]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: List[WinningSignalRecord] = []
    for row in _read_jsonl(skipped_path):
        if str(row.get("outcome", "")) != "take_profit":
            continue
        ts = _parse_ts(row, "backtested_at", "signal_at")
        if ts is not None and ts < cutoff:
            continue
        lid = str(row.get("ledger_id", "") or "")
        ledger_row = ledger_index.get(lid, {})
        ctx = ledger_row.get("entry_context")
        if isinstance(ctx, dict) and ctx:
            feats = _flatten_entry_context(ctx)
        else:
            feats = _features_from_ledger_row(ledger_row) if ledger_row else {}
        if not feats.get("confidence") and row.get("source"):
            feats.setdefault("source", row.get("source"))
        feats.setdefault("skip_reason_bucket", skip_reason_bucket(str(row.get("skip_reason", ""))))
        feats["pnl_pct"] = float(row.get("pnl_pct_net", row.get("pnl_pct", 0)) or 0)
        out.append(
            WinningSignalRecord(
                signal_id=lid or f"skip-{row.get('symbol')}-{row.get('signal_at')}",
                symbol=str(row.get("symbol", "")).upper(),
                side=str(row.get("side", "")),
                source=str(row.get("source", "") or feats.get("source", "")),
                opened_on_exchange=False,
                skip_reason=str(row.get("skip_reason", ""))[:200],
                outcome="take_profit",
                pnl_pct=feats["pnl_pct"],
                features=feats,
                origin="skipped_backtest",
                signal_at=str(row.get("signal_at", "")),
            )
        )
    return out


def load_journal_tp_winners(*, journal_path: Path, hours: float) -> List[WinningSignalRecord]:
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    pending: Dict[str, Dict[str, Any]] = {}
    out: List[WinningSignalRecord] = []
    for row in _read_jsonl(journal_path):
        ev = str(row.get("event", "")).lower()
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        if ev == "entered":
            key = f"{sym}:{row.get('side', '')}"
            pending[key] = row
            continue
        if ev != "closed":
            continue
        ts = _parse_ts(row, "ts", "closed_at", "time")
        if ts is not None and ts < cutoff:
            pending.pop(f"{sym}:{row.get('side', '')}", None)
            continue
        if not _is_journal_tp_win(row):
            continue
        side = str(row.get("side", "") or "")
        ent = pending.pop(f"{sym}:{side}", None) or pending.pop(sym, None) or {}
        ctx = ent.get("entry_context") or row.get("entry_context")
        feats = _flatten_entry_context(ctx) if isinstance(ctx, dict) else {}
        feats.setdefault("side", side.upper())
        feats.setdefault("source", ent.get("source") or row.get("source") or "")
        feats.setdefault("confidence", float(ent.get("confidence") or row.get("confidence") or 0))
        feats["pnl_pct"] = float(row.get("pnl_pct") or 0)
        if not feats.get("pnl_pct") and float(row.get("pnl") or 0) and float(ent.get("entry") or 0):
            try:
                feats["pnl_pct"] = float(row.get("pnl", 0)) / float(ent.get("entry", 1)) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        out.append(
            WinningSignalRecord(
                signal_id=str(ent.get("order_id") or row.get("order_id") or sym),
                symbol=sym,
                side=side,
                source=str(feats.get("source", "")),
                opened_on_exchange=True,
                skip_reason="",
                outcome="take_profit",
                pnl_pct=float(feats.get("pnl_pct", 0) or 0),
                features=feats,
                origin="trade_journal",
                signal_at=str(ent.get("ts") or row.get("ts") or ""),
            )
        )
    return out


def load_skipped_sl_losers(
    *,
    skipped_path: Path,
    ledger_index: Mapping[str, Dict[str, Any]],
    hours: float,
) -> List[WinningSignalRecord]:
    """Контрастная выборка для сравнения правил."""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: List[WinningSignalRecord] = []
    for row in _read_jsonl(skipped_path):
        if str(row.get("outcome", "")) != "stop_loss":
            continue
        ts = _parse_ts(row, "backtested_at", "signal_at")
        if ts is not None and ts < cutoff:
            continue
        lid = str(row.get("ledger_id", "") or "")
        ledger_row = ledger_index.get(lid, {})
        ctx = ledger_row.get("entry_context")
        feats = _flatten_entry_context(ctx) if isinstance(ctx, dict) else _features_from_ledger_row(ledger_row)
        feats["pnl_pct"] = float(row.get("pnl_pct_net", row.get("pnl_pct", 0)) or 0)
        out.append(
            WinningSignalRecord(
                signal_id=lid,
                symbol=str(row.get("symbol", "")).upper(),
                side=str(row.get("side", "")),
                source=str(row.get("source", "")),
                opened_on_exchange=False,
                skip_reason=str(row.get("skip_reason", ""))[:200],
                outcome="stop_loss",
                pnl_pct=feats["pnl_pct"],
                features=feats,
                origin="skipped_backtest",
            )
        )
    return out


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
        if v == v:  # not NaN
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
        if field in ("spread_pct", "atr_pct") and median < statistics.median(_numeric_values(losers, field) or [median]):
            op = "<="
            threshold = round(statistics.quantiles(w_vals, n=4)[2] if len(w_vals) >= 4 else max(w_vals), 4)

        w_pct = _pct_match(winners, field, op, float(threshold))
        l_pct = _pct_match(losers, field, op, float(threshold))
        if w_pct < min_winner_support:
            continue
        if losers and (w_pct - l_pct) < min_contrast_gap:
            continue
        desc = (
            f"У {w_pct:.0f}% удачных TP {field} {op} {threshold} "
            f"(медиана победителей {median:.4g}, у SL-пропусков {l_pct:.0f}%)"
        )
        rules.append(
            RuleSuggestion(
                rule_id=f"{field}_{op}",
                field=field,
                operator=op,
                value=threshold,
                support_pct=w_pct,
                loser_support_pct=l_pct,
                description_ru=desc,
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
                    f"В {support:.0f}% удачных TP {field}={best_val} "
                    f"(у SL-пропусков {l_support:.0f}%)"
                ),
            )
        )

    rules.sort(key=lambda r: (-r.support_pct, r.support_pct - r.loser_support_pct))
    return rules[:12]


class WinningEntryRulesAnalyzer:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
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
        skipped_tp = load_skipped_tp_winners(
            skipped_path=paths["skipped_bt"],
            ledger_index=ledger_index,
            hours=hours,
        )
        journal_tp = load_journal_tp_winners(journal_path=paths["journal"], hours=hours)
        losers = load_skipped_sl_losers(
            skipped_path=paths["skipped_bt"],
            ledger_index=ledger_index,
            hours=hours,
        )
        winners = skipped_tp + journal_tp

        medians: Dict[str, float] = {}
        for field in NUMERIC_FEATURES:
            vals = _numeric_values(winners, field)
            if vals:
                medians[field] = round(statistics.median(vals), 4)

        skip_top: Dict[str, int] = {}
        for w in skipped_tp:
            bucket = skip_reason_bucket(w.skip_reason)
            skip_top[bucket] = skip_top.get(bucket, 0) + 1

        rules = mine_rules(winners, losers)
        return WinningEntryRulesReport(
            hours=hours,
            tp_winners=len(winners),
            tp_skipped_virtual=len(skipped_tp),
            tp_opened_real=len(journal_tp),
            sl_losers=len(losers),
            rules=rules,
            winner_feature_medians=medians,
            top_skip_reasons_on_tp=dict(sorted(skip_top.items(), key=lambda x: -x[1])[:8]),
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
        "# Правила удачных входов (TP)",
        "",
        f"Период: **{report.hours:.0f} ч** | Сгенерировано: {report.generated_at}",
        "",
        "## Сводка",
        f"- Удачных TP всего: **{report.tp_winners}**",
        f"  - виртуальных (не открыты на бирже): **{report.tp_skipped_virtual}**",
        f"  - реальных сделок: **{report.tp_opened_real}**",
        f"- Контраст SL (пропуски): **{report.sl_losers}**",
        "",
    ]
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

    if not report.rules:
        lines.append(
            "_Недостаточно данных (нужно ≥3 TP). "
            "Дождитесь прогонов skipped-backtest supervisor._"
        )
        return "\n".join(lines)

    lines.append("## Предлагаемые правила входа")
    for i, rule in enumerate(report.rules, 1):
        lines.append(f"### {i}. `{rule.field}` {rule.operator} `{rule.value}`")
        lines.append(f"- {rule.description_ru}")
        lines.append(
            f"- Поддержка: **{rule.support_pct}%** победителей | "
            f"у SL-пропусков: **{rule.loser_support_pct}%**"
        )
        lines.append("")
    lines.append(
        "> Hermes: **не меняйте config автоматически**. "
        "Предложите пользователю одно правило за цикл (ZeroOne)."
    )
    return "\n".join(lines)


def build_telegram_report(report: WinningEntryRulesReport) -> str:
    lines = [
        f"<b>🎯 Правила удачных TP ({report.hours:.0f} ч)</b>",
        "",
        f"TP всего: <b>{report.tp_winners}</b> "
        f"(вирт {report.tp_skipped_virtual} + биржа {report.tp_opened_real})",
        f"Контраст SL: {report.sl_losers}",
        "",
    ]
    if not report.rules:
        lines.append(
            "<i>Мало данных. Нужны прогоны skipped-backtest и сделки с entry_context.</i>"
        )
        return "\n".join(lines)

    lines.append("<b>Топ правил</b>")
    for rule in report.rules[:6]:
        lines.append(
            f"• <code>{rule.field}</code> {rule.operator} <b>{rule.value}</b> "
            f"({rule.support_pct:.0f}% TP)"
        )
    if report.top_skip_reasons_on_tp:
        top = list(report.top_skip_reasons_on_tp.items())[:3]
        txt = ", ".join(f"{k}×{v}" for k, v in top)
        lines.append("")
        lines.append(f"<b>Пропуски, но TP:</b> {txt}")
    lines.append("")
    lines.append(f"<i>Файл: data/learning/winning_entry_rules_report.md</i>")
    return "\n".join(lines)
