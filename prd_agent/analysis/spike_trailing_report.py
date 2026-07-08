"""
Анализ spike/scalp (15m импульс) и трейлинга за N часов.

Источники:
- data/trades/trade_history.jsonl (+ archive/)
- data/ledger/signal_ledger.jsonl
- bot.log (+ ротации bot.log.1 …)
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from prd_agent.analysis.scalp_signals import (
    _parse_ts,
    _read_jsonl,
    filter_rows_since,
    is_scalp_row,
    spike_scalp_config_notes,
)

LOG_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)?\s+\[(?:\w+)\]"
)
ENTERED_RE = re.compile(
    r"ENTERED\s+(\w+)\s*:\s*(\w+)\s*\[([^\]]+)\]",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(
    r"CLOSED\s+(\w+)\s*:\s*pnl=\$([-\d.]+)\s*reason=(.+?)(?:\s*$)",
    re.IGNORECASE,
)
EXIT_RE = re.compile(
    r"EXIT\s+(\w+)\s+(\w+)\s+action=(\S+)\s+reason=(.+?)\s+peak=([-\d.]+)%\s+age=([-\d.]+)m",
    re.IGNORECASE,
)
SPIKE_SIGNAL_RE = re.compile(
    r"SPIKE_SCANNER\s+pump_dump_spike\s+(\w+)\s+(PUMP|DUMP)\s+move=([-\d.]+)%",
    re.IGNORECASE,
)
ADAPTIVE_TRAIL_RE = re.compile(
    r"Adaptive trailing\s+(\w+)\s+(\w+):\s+(.+?)\s+\(dist_factor=([-\d.]+)\)",
    re.IGNORECASE,
)
TRAIL_SL_RE = re.compile(
    r"Trailing SL\s+(\w+)\s+(\w+):\s+([\d.]+)\s*->\s*([\d.]+)",
    re.IGNORECASE,
)


def read_trade_history_all(data_dir: Path) -> List[Dict[str, Any]]:
    trades_dir = data_dir / "trades"
    paths: List[Path] = []
    main = trades_dir / "trade_history.jsonl"
    if main.is_file():
        paths.append(main)
    archive = trades_dir / "archive"
    if archive.is_dir():
        paths.extend(sorted(archive.glob("trade_history_*.jsonl")))
    rows: List[Dict[str, Any]] = []
    for path in paths:
        for row in _read_jsonl(path):
            row = dict(row)
            row["_journal_file"] = path.name
            rows.append(row)
    return rows


def filter_rows_since_inclusive(
    rows: Sequence[Dict[str, Any]], hours: float
) -> Tuple[List[Dict[str, Any]], int]:
    """Строки за период; без ts — включаем (иначе теряем старые записи)."""
    if hours <= 0:
        return list(rows), 0
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: List[Dict[str, Any]] = []
    no_ts = 0
    for row in rows:
        ts = _parse_ts(row.get("updated_at") or row.get("created_at") or row.get("ts"))
        if ts <= 0:
            no_ts += 1
            out.append(row)
            continue
        if ts >= cutoff:
            out.append(row)
    return out, no_ts


def _norm_source(value: Any) -> str:
    return str(value or "").strip().upper()


def is_spike_trade_row(row: Mapping[str, Any]) -> Tuple[bool, str]:
    ok, matched = is_scalp_row(row)
    if ok:
        return ok, matched
    source = _norm_source(row.get("source") or row.get("grade"))
    if source == "SPIKE_SCANNER":
        return True, "source=SPIKE_SCANNER"
    reason = str(row.get("reason") or "").lower()
    if reason.startswith("spike_scalp_"):
        return True, f"reason={reason[:32]}"
    ec = row.get("entry_context")
    if isinstance(ec, dict):
        raw = ec.get("filters", {})
        if isinstance(raw, dict):
            sig_raw = raw.get("signal_raw")
            if isinstance(sig_raw, dict):
                ok2, m2 = is_scalp_row(sig_raw)
                if ok2:
                    return ok2, f"entry_context.{m2}"
    return False, ""


def _pair_trades(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Сопоставить entered → closed по symbol+side (FIFO)."""
    pending: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    pairs: List[Dict[str, Any]] = []
    ordered = sorted(
        rows,
        key=lambda r: _parse_ts(r.get("ts") or r.get("created_at") or r.get("updated_at")),
    )
    for row in ordered:
        event = str(row.get("event", "")).lower()
        sym = str(row.get("symbol", "")).upper()
        side = str(row.get("side", ""))
        key = f"{sym}:{side}"
        if event == "entered":
            pending[key].append(row)
            continue
        if event != "closed":
            continue
        entry_row = pending[key].pop(0) if pending[key] else None
        source = str(row.get("source") or (entry_row or {}).get("source") or "")
        merged = {
            "symbol": sym,
            "side": side,
            "pnl": float(row.get("pnl_usdt", row.get("pnl", 0)) or 0),
            "close_reason": str(row.get("reason", "")),
            "source": source,
            "entry": float(row.get("entry") or (entry_row or {}).get("entry") or 0),
            "exit": float(row.get("exit") or 0),
            "entry_ts": (entry_row or {}).get("ts"),
            "close_ts": row.get("ts"),
            "entry_context": (entry_row or {}).get("entry_context"),
            "confidence": (entry_row or {}).get("confidence"),
        }
        ok, matched = is_spike_trade_row({**merged, "reason": source, "grade": source})
        if not ok and entry_row:
            ok, matched = is_spike_trade_row(entry_row)
        merged["is_spike"] = ok
        merged["matched_by"] = matched
        if entry_row and entry_row.get("ts") and row.get("ts"):
            try:
                t0 = datetime.fromisoformat(str(entry_row["ts"]).replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                merged["hold_minutes"] = max(0.0, (t1 - t0).total_seconds() / 60.0)
            except (TypeError, ValueError):
                merged["hold_minutes"] = None
        else:
            merged["hold_minutes"] = None
        pairs.append(merged)
    return pairs


def _parse_log_ts(line: str) -> float:
    m = LOG_TS_RE.match(line)
    if not m:
        return 0.0
    try:
        dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def parse_bot_log(path: Path, *, hours: float) -> Dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600 if hours > 0 else 0.0
    text = path.read_text(encoding="utf-8", errors="replace")
    entered: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []
    exits: List[Dict[str, Any]] = []
    spike_signals: List[Dict[str, Any]] = []
    adaptive: List[Dict[str, Any]] = []
    trail_moves: List[Dict[str, Any]] = []
    lines_in_window = 0
    for line in text.splitlines():
        ts = _parse_log_ts(line)
        if hours > 0 and ts > 0 and ts < cutoff:
            continue
        if ts > 0 or hours <= 0:
            lines_in_window += 1
        m = ENTERED_RE.search(line)
        if m:
            grade = m.group(3).strip().upper()
            entered.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "side": m.group(2).upper(),
                    "grade": grade,
                    "is_spike": grade == "SPIKE_SCANNER" or "SPIKE" in grade,
                }
            )
            continue
        m = CLOSED_RE.search(line)
        if m:
            closed.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "pnl": float(m.group(2)),
                    "reason": m.group(3).strip(),
                }
            )
            continue
        m = EXIT_RE.search(line)
        if m:
            exits.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "side": m.group(2),
                    "action": m.group(3),
                    "reason": m.group(4).strip(),
                    "peak_pct": float(m.group(5)),
                    "age_min": float(m.group(6)),
                }
            )
            continue
        m = SPIKE_SIGNAL_RE.search(line)
        if m:
            spike_signals.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "scenario": m.group(2).upper(),
                    "move_pct": float(m.group(3)),
                }
            )
            continue
        m = ADAPTIVE_TRAIL_RE.search(line)
        if m:
            adaptive.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "side": m.group(2),
                    "note": m.group(3),
                    "dist_factor": float(m.group(4)),
                }
            )
            continue
        m = TRAIL_SL_RE.search(line)
        if m:
            trail_moves.append(
                {
                    "ts": ts,
                    "symbol": m.group(1).upper(),
                    "side": m.group(2),
                    "sl_from": float(m.group(3)),
                    "sl_to": float(m.group(4)),
                }
            )
    return {
        "path": str(path),
        "exists": True,
        "lines_in_window": lines_in_window,
        "entered": entered,
        "closed": closed,
        "exits": exits,
        "spike_signals": spike_signals,
        "adaptive_trailing": adaptive,
        "trailing_sl_moves": trail_moves,
    }


def load_bot_logs(root: Path, *, hours: float) -> List[Dict[str, Any]]:
    paths = [root / "bot.log"]
    paths.extend(sorted(root.glob("bot.log.*"), reverse=True))
    return [parse_bot_log(p, hours=hours) for p in paths if p.is_file()]


def _merge_log_events(log_chunks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {
        "entered": [],
        "closed": [],
        "exits": [],
        "spike_signals": [],
        "adaptive_trailing": [],
        "trailing_sl_moves": [],
        "paths": [],
    }
    for chunk in log_chunks:
        if not chunk.get("exists"):
            continue
        merged["paths"].append(chunk["path"])
        for key in (
            "entered",
            "closed",
            "exits",
            "spike_signals",
            "adaptive_trailing",
            "trailing_sl_moves",
        ):
            merged[key].extend(chunk.get(key, []))
    return merged


def _classify_exit_reason(reason: str) -> str:
    r = reason.lower()
    if "trail" in r:
        return "trailing"
    if "time_stop" in r or "time stop" in r:
        return "time_stop"
    if "exchange" in r or "stop" in r or "sl" in r:
        return "stop_or_exchange"
    if "tp" in r or "take" in r:
        return "take_profit"
    if "manual" in r:
        return "manual"
    return "other"


def _trailing_profile_from_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    pos = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
    pd_raw = pos.get("pump_dump_trailing") if isinstance(pos.get("pump_dump_trailing"), dict) else {}
    em_pd = pd_raw.get("exit_management") if isinstance(pd_raw.get("exit_management"), dict) else {}

    def _f(block: Mapping[str, Any], key: str, default: float) -> float:
        return float(block.get(key, default) or default)

    general = {
        "activation_pct": _f(pos, "trailing_activation_pct", 2.0),
        "distance_pct": _f(pos, "trailing_distance_pct", 2.0),
        "min_distance_pct": _f(pos, "trailing_min_distance_pct", 1.1),
        "distance_atr_mult": _f(pos, "trailing_distance_atr_mult", 1.9),
    }
    spike = {
        "activation_pct": _f(pd_raw, "trailing_activation_pct", 1.0),
        "distance_pct": _f(pd_raw, "trailing_distance_pct", 0.55),
        "min_distance_pct": _f(pd_raw, "trailing_min_distance_pct", 0.35),
        "distance_atr_mult": _f(pd_raw, "trailing_distance_atr_mult", 1.15),
        "breakeven_after_pct": _f(pd_raw, "breakeven_after_pct", 0.85),
        "late_tighten_factor": float(em_pd.get("late_tighten_distance_factor", 0.65) or 0.65),
    }
    adaptive = pos.get("adaptive_trailing") if isinstance(pos.get("adaptive_trailing"), dict) else {}
    return {
        "general": general,
        "pump_dump_trailing": spike,
        "adaptive_trailing": {
            "enabled": bool(adaptive.get("enabled", False)),
            "apply_to_pump_dump": bool(adaptive.get("apply_to_pump_dump", False)),
            "tighten_from_mark_pct": float(adaptive.get("tighten_from_mark_pct", 0) or 0),
        },
    }


@dataclass
class SpikeTrailingReport:
    hours: float
    data_dir: str
    root_dir: str
    journal_rows_total: int
    journal_rows_in_window: int
    journal_rows_no_ts: int
    ledger_spike_total: int
    ledger_spike_executed: int
    ledger_spike_skipped: int
    trade_pairs_total: int
    spike_pairs: List[Dict[str, Any]] = field(default_factory=list)
    log_spike_entered: int = 0
    log_spike_signals: int = 0
    log_exits: List[Dict[str, Any]] = field(default_factory=list)
    log_adaptive_events: int = 0
    diagnostics: List[str] = field(default_factory=list)
    trailing_cfg: Dict[str, Any] = field(default_factory=dict)
    config_notes: List[str] = field(default_factory=list)


def analyze_spike_trailing(
    root: Path,
    *,
    data_dir: Optional[Path] = None,
    hours: float = 72.0,
    cfg: Optional[Mapping[str, Any]] = None,
) -> SpikeTrailingReport:
    root = root.resolve()
    data = (data_dir or root / "data").resolve()
    all_rows = read_trade_history_all(data)
    in_window, no_ts = filter_rows_since_inclusive(all_rows, hours)
    pairs = _pair_trades(in_window)
    spike_pairs = [p for p in pairs if p.get("is_spike")]

    ledger_path = data / "ledger" / "signal_ledger.jsonl"
    ledger_rows = filter_rows_since(_read_jsonl(ledger_path), hours)
    ledger_spike = [r for r in ledger_rows if is_scalp_row(r)[0]]
    ledger_executed = sum(1 for r in ledger_spike if str(r.get("status", "")).lower() == "executed")
    ledger_skipped = sum(1 for r in ledger_spike if str(r.get("status", "")).lower() == "skipped")

    log_chunks = load_bot_logs(root, hours=hours)
    log_data = _merge_log_events(log_chunks)
    log_spike_entered = sum(1 for e in log_data["entered"] if e.get("is_spike"))
    log_exits = log_data["exits"]

    report = SpikeTrailingReport(
        hours=hours,
        data_dir=str(data),
        root_dir=str(root),
        journal_rows_total=len(all_rows),
        journal_rows_in_window=len(in_window),
        journal_rows_no_ts=no_ts,
        ledger_spike_total=len(ledger_spike),
        ledger_spike_executed=ledger_executed,
        ledger_spike_skipped=ledger_skipped,
        trade_pairs_total=len(pairs),
        spike_pairs=spike_pairs,
        log_spike_entered=log_spike_entered,
        log_spike_signals=len(log_data["spike_signals"]),
        log_exits=log_exits,
        log_adaptive_events=len(log_data["adaptive_trailing"]),
    )

    if cfg:
        report.trailing_cfg = _trailing_profile_from_cfg(cfg)
        report.config_notes = spike_scalp_config_notes(dict(cfg))

    if report.journal_rows_total == 0:
        report.diagnostics.append(
            "trade_history пуст — проверьте data/trades/ и archive/, возможно бот не писал журнал"
        )
    if no_ts > 0:
        report.diagnostics.append(
            f"{no_ts} строк журнала без метки времени — включены в выборку целиком"
        )
    if not log_chunks or not any(c.get("exists") for c in log_chunks):
        report.diagnostics.append("bot.log не найден — анализ только по JSONL")
    if (
        not spike_pairs
        and log_spike_entered == 0
        and report.ledger_spike_total == 0
        and report.log_spike_signals == 0
    ):
        report.diagnostics.append(
            "За период нет spike/scalp: ни в журнале, ни в ledger, ни в bot.log"
        )
    elif log_spike_entered > 0 and not spike_pairs:
        report.diagnostics.append(
            f"В bot.log {log_spike_entered} входов SPIKE_SCANNER, но в trade_history нет пар — "
            "возможно журнал ротирован или source не сохранился при закрытии"
        )

    return report


def _summarize_spike_pairs(pairs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    if not pairs:
        return {"n": 0}
    reasons: Counter[str] = Counter()
    pnl_total = 0.0
    wins = 0
    early_small_loss = 0
    hold_times: List[float] = []
    for p in pairs:
        pnl = float(p.get("pnl") or 0)
        pnl_total += pnl
        if pnl > 0:
            wins += 1
        reasons[_classify_exit_reason(str(p.get("close_reason", "")))] += 1
        hm = p.get("hold_minutes")
        if isinstance(hm, (int, float)) and hm is not None:
            hold_times.append(float(hm))
            if hm <= 45 and pnl <= 0:
                early_small_loss += 1
    return {
        "n": len(pairs),
        "wins": wins,
        "losses": len(pairs) - wins,
        "winrate_pct": wins / len(pairs) * 100.0,
        "pnl_usdt": round(pnl_total, 4),
        "by_exit": dict(reasons),
        "early_loss_within_45m": early_small_loss,
        "avg_hold_min": round(sum(hold_times) / len(hold_times), 1) if hold_times else None,
    }


def _summarize_log_exits(exits: Sequence[Dict[str, Any]], symbols: Optional[set[str]] = None) -> Dict[str, Any]:
    filtered = [
        e
        for e in exits
        if not symbols or str(e.get("symbol", "")).upper() in symbols
    ]
    if not filtered:
        return {"n": 0}
    actions: Counter[str] = Counter()
    peaks: List[float] = []
    ages: List[float] = []
    quick_trail = 0
    for e in filtered:
        actions[str(e.get("action", ""))] += 1
        peaks.append(float(e.get("peak_pct") or 0))
        ages.append(float(e.get("age_min") or 0))
        if float(e.get("age_min") or 0) <= 45 and "trail" in str(e.get("action", "")).lower():
            quick_trail += 1
    return {
        "n": len(filtered),
        "by_action": dict(actions),
        "avg_peak_pct": round(sum(peaks) / len(peaks), 2),
        "avg_age_min": round(sum(ages) / len(ages), 1),
        "quick_trailing_exit_45m": quick_trail,
    }


def format_spike_trailing_md(report: SpikeTrailingReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    spike_summary = _summarize_spike_pairs(report.spike_pairs)
    spike_symbols = {str(p.get("symbol", "")).upper() for p in report.spike_pairs}
    exit_summary = _summarize_log_exits(report.log_exits, spike_symbols or None)

    lines = [
        f"# Spike / 15m импульс — трейлинг ({report.hours:g}h)",
        "",
        f"- root: `{report.root_dir}`",
        f"- data: `{report.data_dir}`",
        f"- generated: {now}",
        "",
        "## Диагностика данных",
        f"- строк в trade_history (всего): **{report.journal_rows_total}**",
        f"- строк в окне {report.hours:g}h: **{report.journal_rows_in_window}**",
        f"- пар entered→closed (все): **{report.trade_pairs_total}**",
        f"- пар spike/scalp: **{len(report.spike_pairs)}**",
        f"- ledger spike сигналов: **{report.ledger_spike_total}** "
        f"(executed={report.ledger_spike_executed}, skipped={report.ledger_spike_skipped})",
        f"- bot.log: входов SPIKE_SCANNER **{report.log_spike_entered}**, "
        f"детекций pump_dump_spike **{report.log_spike_signals}**, "
        f"EXIT событий **{len(report.log_exits)}**, adaptive trailing **{report.log_adaptive_events}**",
        "",
    ]
    if report.diagnostics:
        lines.append("### Замечания")
        for d in report.diagnostics:
            lines.append(f"- {d}")
        lines.append("")

    if report.trailing_cfg:
        pd = report.trailing_cfg.get("pump_dump_trailing", {})
        gen = report.trailing_cfg.get("general", {})
        ad = report.trailing_cfg.get("adaptive_trailing", {})
        lines.extend(
            [
                "## Текущий трейлинг (config)",
                "",
                "### pump_dump_trailing (spike / 15m импульс)",
                f"- activation: **{pd.get('activation_pct')}%**",
                f"- distance: **{pd.get('distance_pct')}%** (min {pd.get('min_distance_pct')}%, ATR×{pd.get('distance_atr_mult')})",
                f"- breakeven_after: {pd.get('breakeven_after_pct')}%",
                f"- late_tighten_factor: {pd.get('late_tighten_factor')}",
                "",
                "### general trailing (не spike)",
                f"- activation: {gen.get('activation_pct')}%, distance: {gen.get('distance_pct')}%",
                "",
                "### adaptive_trailing",
                f"- enabled: {ad.get('enabled')}, pump_dump: {ad.get('apply_to_pump_dump')}, "
                f"tighten_from_mark: {ad.get('tighten_from_mark_pct')}%",
                "",
            ]
        )

    if report.config_notes:
        lines.append("## Конфиг spike_scalp")
        for note in report.config_notes:
            lines.append(f"- {note}")
        lines.append("")

    lines.extend(
        [
            "## Реальные spike-сделки (trade_history)",
            "",
        ]
    )
    if spike_summary["n"]:
        lines.append(f"- закрыто: **{spike_summary['n']}**, WR **{spike_summary['winrate_pct']:.1f}%**, "
                     f"PnL **{spike_summary['pnl_usdt']} USDT**")
        lines.append(f"- выходы: `{spike_summary['by_exit']}`")
        if spike_summary.get("avg_hold_min") is not None:
            lines.append(f"- среднее удержание: **{spike_summary['avg_hold_min']} мин**")
        lines.append(f"- ранний минус (≤45 мин): **{spike_summary['early_loss_within_45m']}**")
        lines.append("")
        lines.append("| symbol | side | pnl | hold_min | reason | source |")
        lines.append("|--------|------|-----|----------|--------|--------|")
        for p in report.spike_pairs[:30]:
            hm = p.get("hold_minutes")
            hm_s = f"{hm:.0f}" if isinstance(hm, (int, float)) and hm is not None else "?"
            lines.append(
                f"| {p.get('symbol','')} | {p.get('side','')} | {p.get('pnl',0):.4f} | "
                f"{hm_s} | {str(p.get('close_reason',''))[:40]} | {p.get('source','')} |"
            )
        lines.append("")
    else:
        lines.append("_Нет закрытых spike-пар в журнале за период._")
        lines.append("")

    lines.extend(["## EXIT из bot.log (трейлинг / time-stop)", ""])
    if exit_summary["n"]:
        lines.append(
            f"- событий: **{exit_summary['n']}**, avg peak **{exit_summary['avg_peak_pct']}%**, "
            f"avg age **{exit_summary['avg_age_min']} мин**"
        )
        lines.append(f"- actions: `{exit_summary['by_action']}`")
        lines.append(f"- быстрый trailing (≤45 мин): **{exit_summary['quick_trailing_exit_45m']}**")
        lines.append("")
    else:
        lines.append("_Нет EXIT строк в bot.log за период (или лог ротирован)._")
        lines.append("")

    # Recommendation
    pd_dist = float((report.trailing_cfg.get("pump_dump_trailing") or {}).get("distance_pct") or 0.55)
    suggest_dist = round(max(pd_dist, 0.75), 2)
    suggest_min = round(max(float((report.trailing_cfg.get("pump_dump_trailing") or {}).get("min_distance_pct") or 0.35), 0.50), 2)
    early_hits = spike_summary.get("early_loss_within_45m", 0) + exit_summary.get("quick_trailing_exit_45m", 0)
    lines.extend(
        [
            "## Рекомендация (одно изменение ZeroOne)",
            "",
        ]
    )
    if early_hits >= 2 or (spike_summary["n"] and spike_summary.get("early_loss_within_45m", 0) >= 1):
        lines.append(
            f"Есть признаки раннего выбивания после импульса. Тест на WORLD: "
            f"`pump_dump_trailing.trailing_distance_pct`: **{pd_dist} → {suggest_dist}**, "
            f"`trailing_min_distance_pct`: **→ {suggest_min}** (activation {pd_dist} не трогать сначала)."
        )
    elif report.log_spike_entered > 0 or report.ledger_spike_total > 0:
        lines.append(
            "Spike-активность есть, но мало закрытий в окне — сначала соберите 5–10 сделок, "
            f"затем тест: distance {pd_dist} → {suggest_dist}."
        )
    else:
        lines.append(
            "Недостаточно данных за 72h. Увеличьте `--hours 168` или дождитесь spike-сделок."
        )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def report_to_json(report: SpikeTrailingReport) -> Dict[str, Any]:
    return {
        "hours": report.hours,
        "data_dir": report.data_dir,
        "root_dir": report.root_dir,
        "journal_rows_total": report.journal_rows_total,
        "journal_rows_in_window": report.journal_rows_in_window,
        "ledger_spike_total": report.ledger_spike_total,
        "spike_pairs": report.spike_pairs,
        "spike_summary": _summarize_spike_pairs(report.spike_pairs),
        "log_spike_entered": report.log_spike_entered,
        "log_spike_signals": report.log_spike_signals,
        "log_exit_summary": _summarize_log_exits(report.log_exits),
        "diagnostics": report.diagnostics,
        "trailing_cfg": report.trailing_cfg,
    }
