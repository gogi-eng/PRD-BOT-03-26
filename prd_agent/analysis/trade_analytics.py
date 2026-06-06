"""
Агрегация data/trades/trade_history.jsonl для отчётов в Telegram.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _origin_bucket(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Разделяет bot vs manual (поле origin или source)."""
    mapped: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        origin = str(row.get("origin") or row.get("source") or "bot").lower()
        row["origin_group"] = "manual" if origin == "manual" else "bot"
        mapped.append(row)
    return _bucket_stats(mapped, "origin_group")


def build_report(journal_path: Path, hours: float = 24.0) -> str:
    rows = load_closed_trades(journal_path, hours)
    summary = summarize_trades(rows)
    by_origin = _origin_bucket(rows)
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
