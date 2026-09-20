"""
Агрегация data/trades/trade_history.jsonl для отчётов в Telegram.
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from prd_agent.analysis.portfolio_metrics import (
    compute_portfolio_metrics,
    format_portfolio_quality_telegram,
)
from prd_agent.positions.bot_position_registry import (
    bot_symbols_from_registry,
    bot_symbols_from_trade_log,
    had_bot_entered_before,
    resolve_closed_origin,
    source_implies_bot,
    symbols_from_telegram_audit,
)

# Колонки CSV «выгрузить неделю» (закрытые сделки).
_TRADES_CSV_FIELDS = (
    "ts",
    "local_day",
    "symbol",
    "side",
    "pnl",
    "reason",
    "source",
    "origin",
    "entry",
    "exit_price",
    "qty",
    "order_id",
)


def _parse_ts(raw: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def load_closed_trades(path: Path, hours: float = 24.0) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("event") != "closed":
            continue
        ts = _parse_ts(str(row.get("ts", "")))
        if ts and ts < cutoff:
            continue
        out.append(row)
    return out


def summarize_trades(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0, "wins": 0, "losses": 0, "winrate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0}
    pnls = [float(r.get("pnl", 0) or 0) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    losses = len(pnls) - wins
    total = sum(pnls)
    return {
        "n": len(rows),
        "wins": wins,
        "losses": losses,
        "winrate": wins / len(pnls) * 100 if pnls else 0.0,
        "total_pnl": total,
        "avg_pnl": total / len(pnls) if pnls else 0.0,
    }


def _bucket_stats(rows: List[Dict[str, Any]], key: str, limit: int = 6) -> List[Dict[str, Any]]:
    groups: Dict[str, List[float]] = defaultdict(list)
    for r in rows:
        name = str(r.get(key, "?") or "?")
        groups[name].append(float(r.get("pnl", 0) or 0))
    stats: List[Dict[str, Any]] = []
    for name, pnls in groups.items():
        w = sum(1 for p in pnls if p > 0)
        stats.append(
            {
                "name": name,
                "n": len(pnls),
                "pnl": sum(pnls),
                "winrate": w / len(pnls) * 100 if pnls else 0.0,
            }
        )
    stats.sort(key=lambda x: x["pnl"])
    return stats[:limit]


def format_telegram_report(
    summary: Dict[str, Any],
    *,
    hours: float,
    by_source: List[Dict[str, Any]],
    by_symbol: List[Dict[str, Any]],
    by_reason: List[Dict[str, Any]],
) -> str:
    if summary.get("n", 0) == 0:
        return (
            f"<b>📈 Статистика сделок ({hours:.0f} ч)</b>\n\n"
            "Закрытых сделок в журнале нет.\n"
            "<i>Журнал: data/trades/trade_history.jsonl</i>"
        )
    lines = [
        f"<b>📈 Статистика сделок ({hours:.0f} ч)</b>",
        "",
        f"Сделок: <b>{summary['n']}</b> | Win: {summary['wins']} | Loss: {summary['losses']}",
        f"Winrate: <b>{summary['winrate']:.1f}%</b>",
        f"PnL: <b>{summary['total_pnl']:+.2f}</b> USDT (средн. {summary['avg_pnl']:+.2f})",
        "",
    ]
    if by_reason:
        lines.append("<b>По причине выхода</b>")
        for r in sorted(by_reason, key=lambda x: x["pnl"]):
            lines.append(
                f"• {r['name']}: n={r['n']}, PnL={r['pnl']:+.2f}, WR={r['winrate']:.0f}%"
            )
        lines.append("")
    if by_symbol:
        lines.append("<b>По символам (худшие → лучшие)</b>")
        for r in by_symbol:
            lines.append(
                f"• {r['name']}: n={r['n']}, PnL={r['pnl']:+.2f}, WR={r['winrate']:.0f}%"
            )
        lines.append("")
    if by_source:
        lines.append("<b>По источнику сигнала</b>")
        for r in sorted(by_source, key=lambda x: -x["n"]):
            lines.append(
                f"• {r['name']}: n={r['n']}, PnL={r['pnl']:+.2f}, WR={r['winrate']:.0f}%"
            )
        lines.append("")
    return "\n".join(lines)


def _origin_bucket(
    rows: List[Dict[str, Any]],
    *,
    journal_path: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    telegram_audit_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Разделяет bot vs manual (поле origin, журнал, реестр бота)."""
    audit_syms: Set[str] = set()
    if telegram_audit_path and telegram_audit_path.exists():
        audit_syms = symbols_from_telegram_audit(telegram_audit_path)
    registry_syms: Set[str] = set()
    trade_log_syms: Set[str] = set()
    if data_dir is not None:
        registry_syms = bot_symbols_from_registry(data_dir)
        trade_log_syms = bot_symbols_from_trade_log(data_dir)

    mapped: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        stored = str(row.get("origin") or "").strip().lower()
        sym = str(row.get("symbol", "") or "").upper()
        oid = str(row.get("order_id", "") or "")

        if stored == "bot":
            group = "bot"
        elif stored == "manual":
            if sym in registry_syms or sym in audit_syms or sym in trade_log_syms:
                group = "bot"
            elif source_implies_bot(str(row.get("source") or "")):
                group = "bot"
            elif journal_path and data_dir is not None:
                resolved = resolve_closed_origin(
                    data_dir,
                    sym,
                    order_id=oid,
                    journal_path=journal_path,
                    telegram_audit_path=telegram_audit_path,
                )
                if resolved == "bot":
                    group = "bot"
                else:
                    closed_ts = _parse_ts(str(row.get("ts", "")))
                    if closed_ts and had_bot_entered_before(journal_path, sym, closed_ts):
                        group = "bot"
                    else:
                        group = "manual"
            else:
                group = "manual"
        else:
            source = str(row.get("source") or row.get("origin") or "bot").lower()
            group = "manual" if source == "manual" else "bot"
        row["origin_group"] = group
        mapped.append(row)
    return _bucket_stats(mapped, "origin_group")


def build_report(
    journal_path: Path,
    hours: float = 24.0,
    *,
    data_dir: Optional[Path] = None,
    telegram_audit_path: Optional[Path] = None,
) -> str:
    rows = load_closed_trades(journal_path, hours)
    summary = summarize_trades(rows)
    resolved_data_dir = data_dir if data_dir is not None else journal_path.parent.parent
    by_origin = _origin_bucket(
        rows,
        journal_path=journal_path,
        data_dir=resolved_data_dir,
        telegram_audit_path=telegram_audit_path,
    )
    text = format_telegram_report(
        summary,
        hours=hours,
        by_source=_bucket_stats(rows, "source"),
        by_symbol=_bucket_stats(rows, "symbol"),
        by_reason=_bucket_stats(rows, "reason"),
    )
    if by_origin:
        extra = ["<b>По типу сделки</b>"]
        for r in by_origin:
            label = "Ручные" if r["name"] == "manual" else "Бот"
            extra.append(
                f"• {label}: n={r['n']}, PnL={r['pnl']:+.2f}, WR={r['winrate']:.0f}%"
            )
        text = text + "\n" + "\n".join(extra)
    return text


def build_portfolio_quality_report(journal_path: Path, hours: float = 168.0) -> str:
    """Расширенный отчёт качества (Sharpe, drawdown, profit factor) для кнопки Telegram."""
    rows = load_closed_trades(journal_path, hours)
    metrics = compute_portfolio_metrics(rows)
    return format_portfolio_quality_telegram(metrics, hours=hours)


def _local_day_key(ts: datetime, timezone_offset: int) -> str:
    """Календарный день в местном UTC+offset (как block_entry_utc_hours)."""
    local = ts.astimezone(timezone.utc) + timedelta(hours=int(timezone_offset))
    return local.strftime("%d.%m.%Y")


def _bucket_trades_by_local_day(
    rows: List[Dict[str, Any]], timezone_offset: int
) -> List[Tuple[str, List[Dict[str, Any]]]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = _parse_ts(str(row.get("ts", "")))
        if not ts:
            continue
        groups[_local_day_key(ts, timezone_offset)].append(row)
    ordered = sorted(groups.items(), key=lambda x: x[0], reverse=True)
    return ordered


def _trade_origin_label(row: Dict[str, Any]) -> str:
    """Простая метка origin из журнала: manual vs bot (всё остальное = бот)."""
    origin = str(row.get("origin") or "").strip().lower()
    if origin == "manual":
        return "manual"
    return "bot"


def _split_rows_by_origin(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    bot_rows: List[Dict[str, Any]] = []
    manual_rows: List[Dict[str, Any]] = []
    for row in rows:
        if _trade_origin_label(row) == "manual":
            manual_rows.append(row)
        else:
            bot_rows.append(row)
    return bot_rows, manual_rows


def compute_daily_pnl_extremes(
    day_rows: List[Tuple[str, List[Dict[str, Any]]]],
    *,
    exclude_manual: bool = False,
) -> Optional[Dict[str, Any]]:
    """Лучший/худший день и макс. серия убыточных календарных дней подряд."""
    daily: List[Tuple[str, float, int]] = []
    for day_label, rows in day_rows:
        view = (
            [r for r in rows if _trade_origin_label(r) != "manual"]
            if exclude_manual
            else rows
        )
        if not view:
            continue
        summary = summarize_trades(view)
        daily.append((day_label, float(summary["total_pnl"]), int(summary["n"])))
    if not daily:
        return None

    best_label, best_pnl, _ = max(daily, key=lambda x: x[1])
    worst_label, worst_pnl, _ = min(daily, key=lambda x: x[1])

    ordered = sorted(daily, key=lambda x: datetime.strptime(x[0], "%d.%m.%Y"))
    max_streak = 0
    cur_streak = 0
    prev_date: Optional[datetime] = None
    for label, pnl, _n in ordered:
        day_dt = datetime.strptime(label, "%d.%m.%Y")
        if pnl >= 0:
            cur_streak = 0
        else:
            if (
                prev_date is not None
                and cur_streak > 0
                and (day_dt - prev_date).days == 1
            ):
                cur_streak += 1
            else:
                cur_streak = 1
            if cur_streak > max_streak:
                max_streak = cur_streak
        prev_date = day_dt

    return {
        "best_day": best_label,
        "best_pnl": best_pnl,
        "worst_day": worst_label,
        "worst_pnl": worst_pnl,
        "max_loss_streak": int(max_streak),
    }


def format_daily_pnl_telegram(
    day_rows: List[Tuple[str, List[Dict[str, Any]]]],
    *,
    days: int,
    timezone_offset: int,
    split_origin: bool = True,
    exclude_manual: bool = False,
) -> str:
    if not day_rows:
        tz_label = f"UTC+{timezone_offset}" if timezone_offset >= 0 else f"UTC{timezone_offset}"
        return (
            f"<b>📅 PnL по дням ({days} дн., {tz_label})</b>\n\n"
            "Закрытых сделок в журнале нет.\n"
            "<i>Журнал: data/trades/trade_history.jsonl</i>"
        )
    tz_label = f"UTC+{timezone_offset}" if timezone_offset >= 0 else f"UTC{timezone_offset}"
    mode_note = ""
    if exclude_manual:
        mode_note = ", без ручных"
    elif split_origin:
        mode_note = ", бот/ручные отдельно"
    lines = [f"<b>📅 PnL по дням ({days} дн., {tz_label}{mode_note})</b>", ""]
    total_all = 0.0
    total_bot = 0.0
    total_manual = 0.0
    n_all = 0
    n_bot = 0
    n_manual = 0
    for day_label, rows in day_rows:
        bot_rows, manual_rows = _split_rows_by_origin(rows)
        if exclude_manual:
            view_rows = bot_rows
        else:
            view_rows = rows
        summary = summarize_trades(view_rows)
        bot_sum = summarize_trades(bot_rows)
        man_sum = summarize_trades(manual_rows)
        total_all += float(summary["total_pnl"])
        n_all += int(summary["n"])
        total_bot += float(bot_sum["total_pnl"])
        n_bot += int(bot_sum["n"])
        total_manual += float(man_sum["total_pnl"])
        n_manual += int(man_sum["n"])
        short_day = day_label[:5]
        if split_origin and not exclude_manual:
            lines.append(
                f"<b>{short_day}</b>: всё {summary['total_pnl']:+.2f} "
                f"({summary['n']} сд.) | бот {bot_sum['total_pnl']:+.2f} "
                f"({bot_sum['n']}) | ручн. {man_sum['total_pnl']:+.2f} ({man_sum['n']})"
            )
        else:
            lines.append(
                f"<b>{short_day}</b>: {summary['total_pnl']:+.2f} USDT "
                f"({summary['n']} сд., W{summary['wins']}/L{summary['losses']})"
            )
    lines.append("")
    if split_origin and not exclude_manual:
        lines.extend(
            [
                f"<b>Итого бот:</b> {total_bot:+.2f} USDT за {n_bot} сделок",
                f"<b>Итого ручные:</b> {total_manual:+.2f} USDT за {n_manual} сделок",
                f"<b>Итого всё:</b> {total_all:+.2f} USDT за {n_all} сделок",
            ]
        )
    else:
        label = "Итого (бот)" if exclude_manual else "Итого"
        lines.append(f"<b>{label}:</b> {total_all:+.2f} USDT за {n_all} сделок")

    extremes = compute_daily_pnl_extremes(day_rows, exclude_manual=exclude_manual)
    if extremes:
        lines.append("")
        lines.append("<b>Сводка периода</b>")
        lines.append(
            f"📈 Лучший день: <b>{extremes['best_day']}</b> "
            f"({extremes['best_pnl']:+.2f} USDT)"
        )
        lines.append(
            f"📉 Худший день: <b>{extremes['worst_day']}</b> "
            f"({extremes['worst_pnl']:+.2f} USDT)"
        )
        streak = int(extremes["max_loss_streak"])
        if streak > 0:
            n100 = streak % 100
            n10 = streak % 10
            if 11 <= n100 <= 14:
                word = "дней"
            elif n10 == 1:
                word = "день"
            elif 2 <= n10 <= 4:
                word = "дня"
            else:
                word = "дней"
            lines.append(
                f"🔴 Серия минусов подряд: <b>{streak}</b> {word}"
            )
        else:
            lines.append("🔴 Серия минусов подряд: <b>0</b> (убыточных дней не было)")

    lines.append(
        "<i>Аналог Freqtrade /daily — по календарным дням местного времени.</i>"
    )
    return "\n".join(lines)


def build_daily_pnl_report(
    journal_path: Path,
    days: int = 7,
    *,
    timezone_offset: int = 3,
    split_origin: bool = True,
    exclude_manual: bool = False,
) -> str:
    """Дневной PnL для кнопки Telegram «📅 По дням» (бот / ручные / всё)."""
    d = max(1, int(days))
    rows = load_closed_trades(journal_path, hours=float(d * 24))
    if exclude_manual:
        rows = [r for r in rows if _trade_origin_label(r) != "manual"]
    day_rows = _bucket_trades_by_local_day(rows, timezone_offset)
    return format_daily_pnl_telegram(
        day_rows,
        days=d,
        timezone_offset=timezone_offset,
        split_origin=split_origin,
        exclude_manual=exclude_manual,
    )


def build_trades_csv_text(
    journal_path: Path,
    days: int = 7,
    *,
    timezone_offset: int = 3,
) -> Tuple[str, Dict[str, Any]]:
    """CSV закрытых сделок за N дней + краткая сводка."""
    d = max(1, int(days))
    rows = load_closed_trades(journal_path, hours=float(d * 24))
    rows_sorted = sorted(
        rows,
        key=lambda r: str(r.get("ts", "") or ""),
    )
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_TRADES_CSV_FIELDS), extrasaction="ignore")
    writer.writeheader()
    for row in rows_sorted:
        ts = _parse_ts(str(row.get("ts", "")))
        local_day = _local_day_key(ts, timezone_offset) if ts else ""
        writer.writerow(
            {
                "ts": str(row.get("ts", "") or ""),
                "local_day": local_day,
                "symbol": str(row.get("symbol", "") or ""),
                "side": str(row.get("side", "") or ""),
                "pnl": float(row.get("pnl", 0) or 0),
                "reason": str(row.get("reason", "") or ""),
                "source": str(row.get("source", "") or ""),
                "origin": str(row.get("origin", "") or ""),
                "entry": row.get("entry", ""),
                "exit_price": row.get("exit_price", ""),
                "qty": row.get("qty", ""),
                "order_id": str(row.get("order_id", "") or ""),
            }
        )
    summary = summarize_trades(rows_sorted)
    summary["days"] = d
    summary["timezone_offset"] = int(timezone_offset)
    return buf.getvalue(), summary


def export_trades_csv(
    journal_path: Path,
    export_dir: Path,
    days: int = 7,
    *,
    timezone_offset: int = 3,
) -> Tuple[Path, str]:
    """
    Сохраняет CSV в export_dir и возвращает (путь, HTML-подпись для Telegram).
    """
    csv_text, summary = build_trades_csv_text(
        journal_path, days=days, timezone_offset=timezone_offset
    )
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    d = max(1, int(days))
    out_path = export_dir / f"trades_{d}d_{stamp}.csv"
    out_path.write_text(csv_text, encoding="utf-8")

    tz = int(timezone_offset)
    tz_label = f"UTC+{tz}" if tz >= 0 else f"UTC{tz}"
    n = int(summary.get("n", 0))
    if n == 0:
        caption = (
            f"<b>📥 CSV за {d} дн. ({tz_label})</b>\n\n"
            "Закрытых сделок в журнале нет.\n"
            f"<i>Файл: {out_path.name}</i>"
        )
    else:
        caption = (
            f"<b>📥 CSV за {d} дн. ({tz_label})</b>\n\n"
            f"Сделок: <b>{n}</b> | Win {summary['wins']} / Loss {summary['losses']}\n"
            f"Winrate: <b>{summary['winrate']:.1f}%</b>\n"
            f"PnL: <b>{summary['total_pnl']:+.2f}</b> USDT\n"
            f"<i>Файл: {out_path.name}</i>"
        )
    return out_path, caption


def format_trades_csv_telegram_caption(
    summary: Dict[str, Any],
    *,
    filename: str = "",
) -> str:
    """Краткая подпись к CSV без записи на диск (для тестов / превью)."""
    d = int(summary.get("days", 7))
    tz = int(summary.get("timezone_offset", 3))
    tz_label = f"UTC+{tz}" if tz >= 0 else f"UTC{tz}"
    n = int(summary.get("n", 0))
    name_line = f"\n<i>Файл: {filename}</i>" if filename else ""
    if n == 0:
        return (
            f"<b>📥 CSV за {d} дн. ({tz_label})</b>\n\n"
            f"Закрытых сделок в журнале нет.{name_line}"
        )
    return (
        f"<b>📥 CSV за {d} дн. ({tz_label})</b>\n\n"
        f"Сделок: <b>{n}</b> | Win {summary['wins']} / Loss {summary['losses']}\n"
        f"Winrate: <b>{summary['winrate']:.1f}%</b>\n"
        f"PnL: <b>{summary['total_pnl']:+.2f}</b> USDT{name_line}"
    )
