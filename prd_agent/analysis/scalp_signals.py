"""
Детекция скальп / spike-scalp сигналов и сделок в журналах бота.

Маркеры (актуальные на 07.2026):
- source: SPIKE_SCANNER
- reason: spike_scalp_pump / spike_scalp_dump
- raw_text: pump_dump_spike, SPIKE_SCANNER
- scanner_kind: spike_scalp (hermes_signal_maps / signal_queue)
- legacy: scalp_session, strategy=scalp
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCALP_SOURCES = frozenset(
    {
        "spike_scanner",
        "spike_scalp",
        "scalp",
        "scalp_session",
    }
)

SCALP_REASON_PREFIXES = (
    "spike_scalp_",
    "spike_scalp:",
    "scalp_session",
    "scalp_",
)

SCALP_TEXT_TOKENS = (
    "spike_scalp",
    "spike_scanner",
    "pump_dump_spike",
    "scalp_session",
    "scanner_kind",
)


def _parse_ts(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e12 else v
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _haystack(row: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for key in (
        "source",
        "reason",
        "status",
        "signal_source",
        "close_reason",
        "strategy",
        "scanner_kind",
        "entry_zone",
    ):
        parts.append(_norm(row.get(key)))
    raw = row.get("raw")
    if isinstance(raw, dict):
        parts.append(_norm(raw.get("scanner_kind")))
        parts.append(_norm(raw.get("source")))
        parts.append(_norm(raw.get("reason")))
        parts.append(_norm(raw.get("raw_text")))
        parts.append(_norm(raw.get("text")))
    raw_text = row.get("raw_text")
    if raw_text:
        parts.append(_norm(raw_text))
    snap = row.get("snapshot")
    if isinstance(snap, dict):
        parts.append(_norm(snap.get("scanner_kind")))
        parts.append(_norm(snap.get("strategy")))
    ec = row.get("entry_context")
    if isinstance(ec, dict):
        parts.append(_norm(ec.get("strategy")))
        parts.append(_norm(ec.get("entry_zone")))
        filters = ec.get("filters")
        if isinstance(filters, dict):
            sig_raw = filters.get("signal_raw")
            if isinstance(sig_raw, dict):
                parts.append(_norm(sig_raw.get("reason")))
                parts.append(_norm(sig_raw.get("source")))
    meta = row.get("metadata")
    if isinstance(meta, dict):
        parts.append(_norm(meta.get("strategy")))
    return " ".join(p for p in parts if p)


def is_scalp_row(row: Mapping[str, Any]) -> Tuple[bool, str]:
    """Возвращает (is_scalp, matched_by)."""
    source = _norm(row.get("source"))
    if source in SCALP_SOURCES:
        return True, f"source={source}"

    reason = _norm(row.get("reason"))
    for prefix in SCALP_REASON_PREFIXES:
        if reason.startswith(prefix):
            return True, f"reason={reason[:40]}"

    scanner_kind = _norm(row.get("scanner_kind"))
    if scanner_kind in {"spike_scalp", "spike", "scalp"}:
        return True, f"scanner_kind={scanner_kind}"

    raw = row.get("raw")
    if isinstance(raw, dict):
        sk = _norm(raw.get("scanner_kind"))
        if sk in {"spike_scalp", "spike", "scalp"}:
            return True, f"raw.scanner_kind={sk}"

    strategy = _norm(row.get("strategy"))
    if strategy in {"scalp", "scalp_session"}:
        return True, f"strategy={strategy}"

    ec = row.get("entry_context")
    if isinstance(ec, dict):
        if _norm(ec.get("strategy")) in {"scalp", "scalp_session"}:
            return True, "entry_context.strategy=scalp"
        if _norm(ec.get("entry_zone")) == "scalp_session":
            return True, "entry_context.entry_zone=scalp_session"

    hay = _haystack(row)
    if "spike_scalp_" in hay:
        return True, "text:spike_scalp_"
    if "spike_scanner" in hay:
        return True, "text:spike_scanner"
    if "pump_dump_spike" in hay:
        return True, "text:pump_dump_spike"
    if "scalp_session" in hay:
        return True, "text:scalp_session"

    return False, ""


def filter_rows_since(rows: Sequence[Dict[str, Any]], hours: float) -> List[Dict[str, Any]]:
    if hours <= 0:
        return list(rows)
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: List[Dict[str, Any]] = []
    for row in rows:
        ts = _parse_ts(row.get("updated_at") or row.get("created_at") or row.get("ts"))
        if ts >= cutoff:
            out.append(row)
    return out


@dataclass
class ScalpBucket:
    label: str
    total: int = 0
    virtual_tp: int = 0
    virtual_sl: int = 0
    virtual_other: int = 0
    executed: int = 0
    skipped: int = 0
    real_trades: int = 0
    real_wins: int = 0
    real_losses: int = 0
    real_pnl_usdt: float = 0.0
    samples: List[Dict[str, str]] = field(default_factory=list)

    def add_sample(self, row: Dict[str, Any], matched_by: str, *, limit: int = 8) -> None:
        if len(self.samples) >= limit:
            return
        self.samples.append(
            {
                "symbol": str(row.get("symbol", "")),
                "side": str(row.get("side", "")),
                "status": str(row.get("status", row.get("event", ""))),
                "source": str(row.get("source", "")),
                "reason": str(row.get("reason", ""))[:80],
                "matched_by": matched_by,
            }
        )


@dataclass
class ScalpAnalysisReport:
    hours: float
    data_dir: str
    virtual_signals: ScalpBucket
    real_trades: ScalpBucket
    ledger_signals: ScalpBucket
    queue_signals: ScalpBucket
    maps_signals: ScalpBucket
    config_notes: List[str] = field(default_factory=list)

    @property
    def total_virtual_like(self) -> int:
        return self.virtual_signals.total

    @property
    def total_real_trades(self) -> int:
        return self.real_trades.real_trades


def _classify_virtual(row: Dict[str, Any], bucket: ScalpBucket) -> None:
    bucket.total += 1
    outcome = _norm(
        row.get("outcome")
        or row.get("virtual_outcome")
        or row.get("close_reason")
        or row.get("result")
    )
    if "tp" in outcome or outcome in {"profit", "win", "take_profit"}:
        bucket.virtual_tp += 1
    elif "sl" in outcome or outcome in {"loss", "stop", "stop_loss"}:
        bucket.virtual_sl += 1
    else:
        bucket.virtual_other += 1


def _classify_ledger(row: Dict[str, Any], bucket: ScalpBucket) -> None:
    bucket.total += 1
    status = _norm(row.get("status"))
    if status == "executed":
        bucket.executed += 1
    elif status == "skipped":
        bucket.skipped += 1


def _classify_trade(row: Dict[str, Any], bucket: ScalpBucket) -> None:
    event = _norm(row.get("event"))
    if event and event not in {"closed", "entered", "open"}:
        return
    if event == "entered":
        bucket.total += 1
    if event != "closed":
        return
    origin = _norm(row.get("origin"))
    if origin == "manual":
        return
    bucket.real_trades += 1
    pnl = float(row.get("pnl_usdt", row.get("pnl", 0)) or 0)
    bucket.real_pnl_usdt += pnl
    if pnl > 0:
        bucket.real_wins += 1
    elif pnl < 0:
        bucket.real_losses += 1


def analyze_scalp_signals(
    data_dir: Path,
    *,
    hours: float = 168.0,
    signal_maps_path: Optional[Path] = None,
) -> ScalpAnalysisReport:
    data_dir = data_dir.resolve()
    ledger_path = data_dir / "ledger" / "signal_ledger.jsonl"
    queue_path = data_dir / "signals" / "signal_queue.jsonl"
    trades_path = data_dir / "trades" / "trade_history.jsonl"
    skipped_path = data_dir / "learning" / "skipped_signals_backtest.jsonl"
    virtual_path = data_dir / "learning" / "virtual_trades.jsonl"

    ledger_rows = filter_rows_since(_read_jsonl(ledger_path), hours)
    queue_rows = filter_rows_since(_read_jsonl(queue_path), hours)
    trade_rows = filter_rows_since(_read_jsonl(trades_path), hours)
    skipped_rows = filter_rows_since(_read_jsonl(skipped_path), hours)
    virtual_rows = filter_rows_since(_read_jsonl(virtual_path), hours)

    maps_rows: List[Dict[str, Any]] = []
    if signal_maps_path and signal_maps_path.is_file():
        maps_rows = filter_rows_since(_read_jsonl(signal_maps_path), hours)

    report = ScalpAnalysisReport(
        hours=hours,
        data_dir=str(data_dir),
        virtual_signals=ScalpBucket("virtual_skipped_backtest"),
        real_trades=ScalpBucket("real_trades"),
        ledger_signals=ScalpBucket("signal_ledger"),
        queue_signals=ScalpBucket("signal_queue"),
        maps_signals=ScalpBucket("hermes_signal_maps"),
    )

    for row in ledger_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            _classify_ledger(row, report.ledger_signals)
            report.ledger_signals.add_sample(row, matched)

    for row in queue_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            report.queue_signals.total += 1
            report.queue_signals.add_sample(row, matched)

    for row in skipped_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            _classify_virtual(row, report.virtual_signals)
            report.virtual_signals.add_sample(row, matched)

    for row in virtual_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            _classify_virtual(row, report.virtual_signals)
            report.virtual_signals.add_sample(row, matched)

    for row in maps_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            report.maps_signals.total += 1
            report.maps_signals.add_sample(row, matched)

    for row in trade_rows:
        ok, matched = is_scalp_row(row)
        if ok:
            _classify_trade(row, report.real_trades)
            report.real_trades.add_sample(row, matched)

    return report


def format_scalp_report_md(report: ScalpAnalysisReport) -> str:
    def block(bucket: ScalpBucket) -> List[str]:
        lines = [
            f"### {bucket.label}",
            f"- всего: **{bucket.total}**",
        ]
        if bucket.virtual_tp or bucket.virtual_sl or bucket.virtual_other:
            lines.append(
                f"- виртуально: TP={bucket.virtual_tp}, SL={bucket.virtual_sl}, прочее={bucket.virtual_other}"
            )
        if bucket.executed or bucket.skipped:
            lines.append(f"- ledger: executed={bucket.executed}, skipped={bucket.skipped}")
        if bucket.real_trades:
            wr = (
                bucket.real_wins / bucket.real_trades * 100.0
                if bucket.real_trades
                else 0.0
            )
            lines.append(
                f"- реальные закрытия: **{bucket.real_trades}**, "
                f"win={bucket.real_wins}, loss={bucket.real_losses}, "
                f"PnL={bucket.real_pnl_usdt:.2f} USDT, WR={wr:.1f}%"
            )
        if bucket.samples:
            lines.append("")
            lines.append("| symbol | side | status | matched_by |")
            lines.append("|--------|------|--------|------------|")
            for s in bucket.samples:
                lines.append(
                    f"| {s.get('symbol','')} | {s.get('side','')} | "
                    f"{s.get('status','')} | {s.get('matched_by','')} |"
                )
        return lines

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Scalp signals analysis ({report.hours:g}h)",
        "",
        f"data_dir: `{report.data_dir}`",
        f"generated: {now}",
        "",
        "## Сводка",
        f"- signal_ledger (скальп): **{report.ledger_signals.total}**",
        f"- signal_queue (скальп): **{report.queue_signals.total}**",
        f"- hermes_signal_maps (скальп): **{report.maps_signals.total}**",
        f"- виртуальные (skipped/virtual): **{report.virtual_signals.total}** "
        f"(TP={report.virtual_signals.virtual_tp}, SL={report.virtual_signals.virtual_sl})",
        f"- реальные закрытия: **{report.real_trades.real_trades}**, "
        f"PnL={report.real_trades.real_pnl_usdt:.2f} USDT",
        "",
    ]
    if report.config_notes:
        lines.append("## Заметки по конфигу")
        for note in report.config_notes:
            lines.append(f"- {note}")
        lines.append("")

    for bucket in (
        report.ledger_signals,
        report.queue_signals,
        report.maps_signals,
        report.virtual_signals,
        report.real_trades,
    ):
        if bucket.total or bucket.real_trades:
            lines.extend(block(bucket))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def spike_scalp_config_notes(cfg: Mapping[str, Any]) -> List[str]:
    """Подсказки, почему скальп может быть выключен."""
    from telegram_agent.pump_dump_spike_scan import SpikeScanConfig

    sc = SpikeScanConfig.from_cfg(dict(cfg))
    notes: List[str] = []
    if not sc.enabled:
        notes.append(
            "spike_scalp.enabled=false — скальп-сканер выключен (добавьте market_scanner.spike_scalp в config)"
        )
    mc = cfg.get("market_scanner") if isinstance(cfg.get("market_scanner"), dict) else {}
    if not bool(mc.get("enabled", True)):
        notes.append("market_scanner.enabled=false")
    agent = cfg.get("telegram_signal_agent") if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
    if agent and not bool(agent.get("market_scanner_enabled", True)):
        notes.append("telegram_signal_agent.market_scanner_enabled=false")
    if sc.enabled:
        notes.append(
            f"spike_scalp OK: interval={sc.interval_sec}s, min_move={sc.min_move_pct}%, "
            f"min_score={sc.execute_min_score}, auto_execute={sc.auto_execute}"
        )
    return notes
