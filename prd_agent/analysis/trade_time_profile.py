"""
Анализ PnL, макс. профита и просадки по времени входа и закрытия (час / день недели).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

from prd_agent.analysis.scalp_signals import _parse_ts
from prd_agent.analysis.spike_trailing_report import (
    _pair_trades,
    filter_rows_since_inclusive,
    read_trade_history_all,
)

WEEKDAY_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
TimeAxis = Literal["entry", "close"]


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
class TimeProfileSlice:
    axis: TimeAxis
    trades_with_time: int = 0
    trades_skipped_no_ts: int = 0
    by_hour: List[TimeBucketStats] = field(default_factory=list)
    by_weekday: List[TimeBucketStats] = field(default_factory=list)
    highlights: List[str] = field(default_factory=list)


@dataclass
class TradeTimeProfileReport:
    hours: float
    tz_offset_hours: float
    tz_label: str
    data_dir: str
    trades_total: int
    summary: Dict[str, Any] = field(default_factory=dict)
    entry: TimeProfileSlice = field(default_factory=lambda: TimeProfileSlice(axis="entry"))
    close: TimeProfileSlice = field(default_factory=lambda: TimeProfileSlice(axis="close"))


def _bucket_key(dt: datetime, kind: str) -> int:
    if kind == "hour":
        return dt.hour
    return dt.weekday()


def _make_buckets(kind: str) -> Dict[int, TimeBucketStats]:
    if kind == "hour":
        return {h: TimeBucketStats(label=f"{h:02d}:00", key=h) for h in range(24)}
    return {d: TimeBucketStats(label=WEEKDAY_RU[d], key=d) for d in range(7)}


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


def _trade_ts(p: Dict[str, Any], axis: TimeAxis) -> Any:
    if axis == "close":
        return p.get("close_ts")
    return p.get("entry_ts") or p.get("close_ts")


def _aggregate_slice(
    pairs: Sequence[Dict[str, Any]],
    *,
    axis: TimeAxis,
    tz_offset_hours: float,
) -> TimeProfileSlice:
    hour_buckets = _make_buckets("hour")
    weekday_buckets = _make_buckets("weekday")
    hour_pnls: Dict[int, List[Tuple[float, str, datetime]]] = {h: [] for h in range(24)}
    weekday_pnls: Dict[int, List[Tuple[float, str, datetime]]] = {d: [] for d in range(7)}

    with_time = 0
    skipped = 0

    for p in pairs:
        pnl = float(p.get("pnl") or 0)
        sym = str(p.get("symbol") or "")
        dt = _to_local_dt(_trade_ts(p, axis), tz_offset_hours)
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

    slice_ = TimeProfileSlice(
        axis=axis,
        trades_with_time=with_time,
        trades_skipped_no_ts=skipped,
        by_hour=[hour_buckets[h] for h in range(24)],
        by_weekday=[weekday_buckets[d] for d in range(7)],
    )
    slice_.highlights = _build_highlights(slice_, tz_label=f"UTC{tz_offset_hours:+.0f}".replace(".0", ""))
    return slice_


def analyze_trade_time_profile(
    data_dir,
    *,
    hours: float = 168.0,
    tz_offset_hours: float = 3.0,
) -> TradeTimeProfileReport:
    data_path = Path(data_dir).resolve()
    rows, _ = filter_rows_since_inclusive(read_trade_history_all(data_path), hours)
    pairs = _pair_trades(rows)

    all_pnls = [float(p.get("pnl") or 0) for p in pairs]
    wins = sum(1 for x in all_pnls if x > 0)
    summary = {
        "n": len(pairs),
        "wins": wins,
        "losses": len(all_pnls) - wins,
        "winrate_pct": wins / len(all_pnls) * 100.0 if all_pnls else 0.0,
        "total_pnl": round(sum(all_pnls), 4),
        "max_single_profit": max(all_pnls) if all_pnls else 0.0,
        "max_single_loss": min(all_pnls) if all_pnls else 0.0,
        "max_drawdown_by_entry": _max_drawdown_from_pnls(
            [
                float(p.get("pnl") or 0)
                for p in sorted(
                    pairs,
                    key=lambda p: _parse_ts(p.get("entry_ts") or p.get("close_ts")),
                )
            ]
        ),
        "max_drawdown_by_close": _max_drawdown_from_pnls(
            [
                float(p.get("pnl") or 0)
                for p in sorted(
                    pairs,
                    key=lambda p: _parse_ts(p.get("close_ts") or p.get("entry_ts")),
                )
            ]
        ),
    }

    tz_label = f"UTC{tz_offset_hours:+.0f}".replace(".0", "")
    return TradeTimeProfileReport(
        hours=hours,
        tz_offset_hours=tz_offset_hours,
        tz_label=tz_label,
        data_dir=str(data_path),
        trades_total=len(pairs),
        summary=summary,
        entry=_aggregate_slice(pairs, axis="entry", tz_offset_hours=tz_offset_hours),
        close=_aggregate_slice(pairs, axis="close", tz_offset_hours=tz_offset_hours),
    )


def _build_highlights(slice_: TimeProfileSlice, *, tz_label: str) -> List[str]:
    axis_ru = "входа" if slice_.axis == "entry" else "закрытия"
    lines: List[str] = []
    active_hours = [b for b in slice_.by_hour if b.trades >= 3]
    active_days = [b for b in slice_.by_weekday if b.trades >= 3]

    if active_hours:
        best = max(active_hours, key=lambda b: b.total_pnl)
        worst = min(active_hours, key=lambda b: b.total_pnl)
        lines.append(
            f"Лучший час {axis_ru} (≥3): {best.label} {tz_label} — "
            f"PnL {best.total_pnl:+.2f}, WR {best.winrate_pct:.0f}%"
        )
        lines.append(
            f"Худший час {axis_ru} (≥3): {worst.label} {tz_label} — "
            f"PnL {worst.total_pnl:+.2f}, WR {worst.winrate_pct:.0f}%"
        )
        deep_dd = max(active_hours, key=lambda b: b.max_drawdown)
        if deep_dd.max_drawdown > 0:
            lines.append(
                f"Макс. просадка (час {axis_ru}): {deep_dd.label} — "
                f"{deep_dd.max_drawdown:.2f} USDT"
            )

    if active_days:
        best_d = max(active_days, key=lambda b: b.total_pnl)
        worst_d = min(active_days, key=lambda b: b.total_pnl)
        lines.append(
            f"Лучший день {axis_ru}: {best_d.label} — PnL {best_d.total_pnl:+.2f}"
        )
        lines.append(
            f"Худший день {axis_ru}: {worst_d.label} — PnL {worst_d.total_pnl:+.2f}"
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


def _format_slice_md(slice_: TimeProfileSlice, *, tz_label: str) -> List[str]:
    axis_title = "входа" if slice_.axis == "entry" else "закрытия"
    dd_note = (
        "открытых в этот час"
        if slice_.axis == "entry"
        else "закрытых в этот час"
    )
    lines = [
        f"### По времени {axis_title} ({tz_label})",
        f"- сделок с ts {axis_title}: **{slice_.trades_with_time}**, без ts: {slice_.trades_skipped_no_ts}",
        "",
    ]
    if slice_.highlights:
        for h in slice_.highlights:
            lines.append(f"- {h}")
        lines.append("")

    lines.extend(
        [
            f"#### Час {axis_title}",
            "",
            "| час | сделок | WR | сумма PnL | макс + | макс − | просадка* | средн. |",
            "|-----|--------|----|-----------|--------|--------|-----------|--------|",
        ]
    )
    for b in slice_.by_hour:
        lines.append(_bucket_row_md(b))
    lines.extend(
        [
            "",
            f"*просадка — peak→trough по цепочке сделок, {dd_note}",
            "",
            f"#### День недели ({axis_title})",
            "",
            "| день | сделок | WR | сумма PnL | макс + | макс − | просадка* | средн. |",
            "|------|--------|----|-----------|--------|--------|-----------|--------|",
        ]
    )
    for b in slice_.by_weekday:
        lines.append(_bucket_row_md(b))
    lines.append("")
    return lines


def format_trade_time_profile_md(report: TradeTimeProfileReport) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    s = report.summary
    lines = [
        f"# Профиль сделок по времени ({report.hours:g}h)",
        "",
        f"- data: `{report.data_dir}`",
        f"- часовой пояс: **{report.tz_label}**",
        f"- generated: {now}",
        "",
        "## Сводка",
        f"- сделок: **{report.trades_total}**",
        f"- WR: **{s.get('winrate_pct', 0):.1f}%**, PnL: **{s.get('total_pnl', 0):+.2f} USDT**",
        f"- макс. прибыль / убыток (одна сделка): **{s.get('max_single_profit', 0):+.2f}** / "
        f"**{s.get('max_single_loss', 0):+.2f}**",
        f"- просадка (серия по входу): **{s.get('max_drawdown_by_entry', 0):.2f} USDT**",
        f"- просадка (серия по закрытию): **{s.get('max_drawdown_by_close', 0):.2f} USDT**",
        "",
        "## По времени ВХОДА",
        "",
    ]
    lines.extend(_format_slice_md(report.entry, tz_label=report.tz_label))
    lines.append("## По времени ЗАКРЫТИЯ")
    lines.append("")
    lines.extend(_format_slice_md(report.close, tz_label=report.tz_label))
    return "\n".join(lines).rstrip() + "\n"


def _slice_to_json(slice_: TimeProfileSlice) -> Dict[str, Any]:
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
        "axis": slice_.axis,
        "trades_with_time": slice_.trades_with_time,
        "trades_skipped_no_ts": slice_.trades_skipped_no_ts,
        "highlights": slice_.highlights,
        "by_hour": [bucket_dict(b) for b in slice_.by_hour],
        "by_weekday": [bucket_dict(b) for b in slice_.by_weekday],
    }


def report_to_json(report: TradeTimeProfileReport) -> Dict[str, Any]:
    return {
        "hours": report.hours,
        "tz_offset_hours": report.tz_offset_hours,
        "tz_label": report.tz_label,
        "data_dir": report.data_dir,
        "trades_total": report.trades_total,
        "summary": report.summary,
        "entry": _slice_to_json(report.entry),
        "close": _slice_to_json(report.close),
    }
