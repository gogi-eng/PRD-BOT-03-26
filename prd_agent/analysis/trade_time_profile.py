"""
Анализ PnL, макс. профита и просадки по времени входа (час / день недели).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from prd_agent.analysis.scalp_signals import _parse_ts
from prd_agent.analysis.spike_trailing_report import (
    _pair_trades,
    filter_rows_since_inclusive,
    read_trade_history_all,
)

WEEKDAY_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _to_local_dt(ts_raw: Any, tz_offset_hours: float) -> Optional[datetime]:
    ts = _parse_ts(ts_raw)
    if ts <= 0:
        return None
    utc = datetime.fromtimestamp(ts, tz=timezone.utc)
    return utc + timedelta(hours=tz_offset_hours)


def _max_drawdown_from_pnls(pnls: Sequence[float]) -> float:
    """Просадка по цепочке сделок (peak → trough), USDT."""
    if not pnls:
        return 0.0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd


@dataclass
class TimeBucketStats:
    label: str
    key: int
    trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    winrate_pct: float = 0.0
    max_single_profit: float = 0.0
    max_single_loss: float = 0.0
    max_drawdown: float = 0.0
    best_symbol_profit: str = ""
    worst_symbol_loss: str = ""

    def finalize(self, ordered_pnls: List[float]) -> None:
        if self.trades:
            self.avg_pnl = self.total_pnl / self.trades
            self.winrate_pct = self.wins / self.trades * 100.0
        self.max_drawdown = _max_drawdown_from_pnls(ordered_pnls)


@dataclass
class TradeTimeProfileReport:
    hours: float
    tz_offset_hours: float
    tz_label: str
    data_dir: str
    trades_total: int
    trades_with_time: int
    trades_skipped_no_ts: int
    summary: Dict[str, Any] = field(default_factory=dict)
    by_hour: List[TimeBucketStats] = field(default_factory=list)
    by_weekday: List[TimeBucketStats] = field(default_factory=list)
    highlights: List[str] = field(default_factory=list)


def _bucket_key(dt: datetime, kind: str) -> int:
    if kind == "hour":
        return dt.hour
    return dt.weekday()


def _make_buckets(kind: str) -> Dict[int, TimeBucketStats]:
    if kind == "hour":
        return {
            h: TimeBucketStats(label=f"{h:02d}:00", key=h)
            for h in range(24)
        }
    return {
        d: TimeBucketStats(label=WEEKDAY_RU[d], key=d)
        for d in range(7)
    }


def _ingest_trade(
    buckets: Dict[int, TimeBucketStats],
    ordered_pnls: Dict[int, List[Tuple[float, str, datetime]]],
    *,
    dt: datetime,
    pnl: float,
    symbol: str,
    kind: str,
) -> None:
    key = _bucket_key(dt, kind)
    b = buckets[key]
    b.trades += 1
    b.total_pnl += pnl
    if pnl > 0:
        b.wins += 1
    elif pnl < 0:
        b.losses += 1
    if pnl > b.max_single_profit:
        b.max_single_profit = pnl
        b.best_symbol_profit = symbol
    if pnl < b.max_single_loss:
        b.max_single_loss = pnl
        b.worst_symbol_loss = symbol
    ordered_pnls[key].append((pnl, symbol, dt))


def analyze_trade_time_profile(
    data_dir,
    *,
    hours: float = 168.0,
    tz_offset_hours: float = 3.0,
) -> TradeTimeProfileReport:
    from pathlib import Path

    data_path = Path(data_dir).resolve()
    rows, _ = filter_rows_since_inclusive(read_trade_history_all(data_path), hours)
    pairs = _pair_trades(rows)

    hour_buckets = _make_buckets("hour")
    weekday_buckets = _make_buckets("weekday")
    hour_pnls: Dict[int, List[Tuple[float, str, datetime]]] = {h: [] for h in range(24)}
    weekday_pnls: Dict[int, List[Tuple[float, str, datetime]]] = {d: [] for d in range(7)}

    with_time = 0
    skipped = 0
    all_pnls: List[float] = []

    for p in pairs:
        pnl = float(p.get("pnl") or 0)
        all_pnls.append(pnl)
        sym = str(p.get("symbol") or "")
        dt = _to_local_dt(p.get("entry_ts") or p.get("close_ts"), tz_offset_hours)
        if dt is None:
            skipped += 1
            continue
        with_time += 1
        _ingest_trade(hour_buckets, hour_pnls, dt=dt, pnl=pnl, symbol=sym, kind="hour")
        _ingest_trade(weekday_buckets, weekday_pnls, dt=dt, pnl=pnl, symbol=sym, kind="weekday")

    for h, b in hour_buckets.items():
        seq = [x[0] for x in sorted(hour_pnls[h], key=lambda t: t[2])]
        b.finalize(seq)
    for d, b in weekday_buckets.items():
        seq = [x[0] for x in sorted(weekday_pnls[d], key=lambda t: t[2])]
        b.finalize(seq)

    wins = sum(1 for x in all_pnls if x > 0)
    summary = {
        "n": len(pairs),
        "wins": wins,
        "losses": len(all_pnls) - wins,
        "winrate_pct": wins / len(all_pnls) * 100.0 if all_pnls else 0.0,
        "total_pnl": round(sum(all_pnls), 4),
        "max_single_profit": max(all_pnls) if all_pnls else 0.0,
        "max_single_loss": min(all_pnls) if all_pnls else 0.0,
        "max_drawdown_all": _max_drawdown_from_pnls(
            [x[0] for x in sorted(
                [(float(p.get("pnl") or 0), _parse_ts(p.get("entry_ts") or p.get("close_ts"))) for p in pairs],
                key=lambda t: t[1],
            )]
        ),
    }

    tz_label = f"UTC{tz_offset_hours:+.0f}".replace("+", "+").replace(".0", "")
    report = TradeTimeProfileReport(
        hours=hours,
        tz_offset_hours=tz_offset_hours,
        tz_label=tz_label,
        data_dir=str(data_path),
        trades_total=len(pairs),
        trades_with_time=with_time,
        trades_skipped_no_ts=skipped,
        summary=summary,
        by_hour=[hour_buckets[h] for h in range(24)],
        by_weekday=[weekday_buckets[d] for d in range(7)],
    )
    report.highlights = _build_highlights(report)
    return report


def _build_highlights(report: TradeTimeProfileReport) -> List[str]:
    lines: List[str] = []
    active_hours = [b for b in report.by_hour if b.trades >= 3]
    active_days = [b for b in report.by_weekday if b.trades >= 3]

    if active_hours:
        best = max(active_hours, key=lambda b: b.total_pnl)
        worst = min(active_hours, key=lambda b: b.total_pnl)
        lines.append(
            f"Лучший час (≥3 сделок): {best.label} {report.tz_label} — "
            f"PnL {best.total_pnl:+.2f}, WR {best.winrate_pct:.0f}%"
        )
        lines.append(
            f"Худший час (≥3 сделок): {worst.label} {report.tz_label} — "
            f"PnL {worst.total_pnl:+.2f}, WR {worst.winrate_pct:.0f}%"
        )
        deep_dd = max(active_hours, key=lambda b: b.max_drawdown)
        if deep_dd.max_drawdown > 0:
            lines.append(
                f"Макс. просадка по часу: {deep_dd.label} — {deep_dd.max_drawdown:.2f} USDT "
                f"({deep_dd.trades} сделок)"
            )
        big_win = max(active_hours, key=lambda b: b.max_single_profit)
        if big_win.max_single_profit > 0:
            lines.append(
                f"Макс. одна прибыль: {big_win.label} — {big_win.max_single_profit:+.2f} "
                f"({big_win.best_symbol_profit})"
            )
        big_loss = min(active_hours, key=lambda b: b.max_single_loss)
        if big_loss.max_single_loss < 0:
            lines.append(
                f"Макс. один убыток: {big_loss.label} — {big_loss.max_single_loss:+.2f} "
                f"({big_loss.worst_symbol_loss})"
            )

    if active_days:
        best_d = max(active_days, key=lambda b: b.total_pnl)
        worst_d = min(active_days, key=lambda b: b.total_pnl)
        lines.append(
            f"Лучший день: {best_d.label} — PnL {best_d.total_pnl:+.2f}, WR {best_d.winrate_pct:.0f}%"
        )
        lines.append(
            f"Худший день: {worst_d.label} — PnL {worst_d.total_pnl:+.2f}, WR {worst_d.winrate_pct:.0f}%"
        )
    return lines


def _bucket_row_md(b: TimeBucketStats) -> str:
    if b.trades == 0:
        return f"| {b.label} | 0 | — | — | — | — | — | — |"
    return (
        f"| {b.label} | {b.trades} | {b.winrate_pct:.0f}% | {b.total_pnl:+.2f} | "
        f"{b.max_single_profit:+.2f} | {b.max_single_loss:+.2f} | {b.max_drawdown:.2f} | "
        f"{b.avg_pnl:+.2f} |"
    )


def format_trade_time_profile_md(report: TradeTimeProfileReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    s = report.summary
    lines = [
        f"# Профиль сделок по времени ({report.hours:g}h)",
        "",
        f"- data: `{report.data_dir}`",
        f"- часовой пояс входа: **{report.tz_label}** (UTC{report.tz_offset_hours:+.0f})",
        f"- generated: {now}",
        "",
        "## Сводка",
        f"- сделок: **{report.trades_total}** (с временем входа: {report.trades_with_time}, без ts: {report.trades_skipped_no_ts})",
        f"- WR: **{s.get('winrate_pct', 0):.1f}%**, PnL: **{s.get('total_pnl', 0):+.2f} USDT**",
        f"- макс. одна прибыль: **{s.get('max_single_profit', 0):+.2f}**, "
        f"макс. один убыток: **{s.get('max_single_loss', 0):+.2f}**",
        f"- макс. просадка (вся серия): **{s.get('max_drawdown_all', 0):.2f} USDT**",
        "",
    ]
    if report.highlights:
        lines.append("## Главное")
        for h in report.highlights:
            lines.append(f"- {h}")
        lines.append("")

    lines.extend(
        [
            "## По часу входа (локальное время)",
            "",
            "| час | сделок | WR | сумма PnL | макс + | макс − | просадка* | средн. |",
            "|-----|--------|----|-----------|--------|--------|-----------|--------|",
        ]
    )
    for b in report.by_hour:
        lines.append(_bucket_row_md(b))
    lines.extend(
        [
            "",
            "*просадка — peak→trough по цепочке сделок, открытых в этот час",
            "",
            "## По дню недели (вход)",
            "",
            "| день | сделок | WR | сумма PnL | макс + | макс − | просадка* | средн. |",
            "|------|--------|----|-----------|--------|--------|-----------|--------|",
        ]
    )
    for b in report.by_weekday:
        lines.append(_bucket_row_md(b))
    lines.append("")
    return "\n".join(lines)


def report_to_json(report: TradeTimeProfileReport) -> Dict[str, Any]:
    def bucket_dict(b: TimeBucketStats) -> Dict[str, Any]:
        return {
            "label": b.label,
            "key": b.key,
            "trades": b.trades,
            "wins": b.wins,
            "losses": b.losses,
            "total_pnl": round(b.total_pnl, 4),
            "avg_pnl": round(b.avg_pnl, 4),
            "winrate_pct": round(b.winrate_pct, 2),
            "max_single_profit": round(b.max_single_profit, 4),
            "max_single_loss": round(b.max_single_loss, 4),
            "max_drawdown": round(b.max_drawdown, 4),
            "best_symbol_profit": b.best_symbol_profit,
            "worst_symbol_loss": b.worst_symbol_loss,
        }

    return {
        "hours": report.hours,
        "tz_offset_hours": report.tz_offset_hours,
        "tz_label": report.tz_label,
        "data_dir": report.data_dir,
        "trades_total": report.trades_total,
        "trades_with_time": report.trades_with_time,
        "trades_skipped_no_ts": report.trades_skipped_no_ts,
        "summary": report.summary,
        "highlights": report.highlights,
        "by_hour": [bucket_dict(b) for b in report.by_hour],
        "by_weekday": [bucket_dict(b) for b in report.by_weekday],
    }
