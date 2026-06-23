"""
Hermes → Cursor: живой брифинг для агента в IDE.

Пишет:
- `.cursor/HERMES_LIVE.md` в корне репозитория (читает Cursor)
- `data/learning/hermes_cursor_live.md` (копия для scp с сервера)
- `data/learning/hermes_cursor_feed.jsonl` (история событий)
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from prd_agent.learning.winning_entry_rules import (
    WinningEntryRulesReport,
    WeightRecommendation,
)

CURSOR_LIVE_FILENAME = "HERMES_LIVE.md"
FEED_JSONL_NAME = "hermes_cursor_feed.jsonl"
MIRROR_MD_NAME = "hermes_cursor_live.md"

WATCH_PATHS_REL = (
    "supervisor/skipped_backtest/results.jsonl",
    "trades/trade_history.jsonl",
    "ledger/signal_ledger.jsonl",
    "supervisor/skip_stats.json",
)

ACTION_RU = {
    "increase_weight": "Усилить вес",
    "decrease_weight": "Ослабить",
    "consider_remove": "Рассмотреть отказ от фильтра",
    "keep": "Оставить как есть",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def report_fingerprint(report: WinningEntryRulesReport) -> str:
    payload = json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def pick_zero_one_recommendation(
    report: WinningEntryRulesReport,
) -> Optional[WeightRecommendation]:
    priority = ("consider_remove", "increase_weight", "decrease_weight", "keep")
    by_action: Dict[str, List[WeightRecommendation]] = {a: [] for a in priority}
    for rec in report.weight_recommendations:
        if rec.action in by_action:
            by_action[rec.action].append(rec)
    for action in priority:
        candidates = by_action[action]
        if not candidates:
            continue
        ranked = sorted(
            candidates,
            key=lambda r: (
                {"high": 0, "medium": 1, "low": 2}.get(r.confidence, 3),
                -r.n_samples,
            ),
        )
        if action == "keep":
            continue
        return ranked[0]
    return None


def build_cursor_brief(
    report: WinningEntryRulesReport,
    *,
    source_label: str = "PRD-BOT",
    host_hint: str = "",
) -> str:
    zero_one = pick_zero_one_recommendation(report)
    fp = report_fingerprint(report)

    lines: List[str] = [
        "---",
        "hermes_feed: true",
        f"generated_at: {report.generated_at}",
        f"fingerprint: {fp}",
        f"source: {source_label}",
        f"lookback_hours: {report.hours}",
    ]
    if host_hint:
        lines.append(f"host: {host_hint}")
    lines.extend(
        [
            "---",
            "",
            "# Hermes → Cursor (живой брифинг)",
            "",
            "> **Для агента Cursor:** прочитай этот файл в начале сессии по PRD-BOT.",
            "> Рекомендации — только предложения. ZeroOne: **максимум одна** правка config за раз.",
            "> Не меняй `config.yaml` и не ставь ордера без явной просьбы пользователя.",
            "",
            f"**Обновлено:** {report.generated_at} | окно **{report.hours:.0f} ч** | id `{fp}`",
            "",
            "## Сводка исходов",
            "",
            f"| Исход | Кол-во |",
            f"|-------|--------|",
            f"| Профит | {report.outcome_counts.get('profit', 0)} |",
            f"| Убыток | {report.outcome_counts.get('loss', 0)} |",
            f"| Безубыток | {report.outcome_counts.get('neutral', 0)} |",
            "",
            f"TP всего: **{report.tp_winners}** "
            f"(вирт {report.tp_skipped_virtual}, биржа {report.tp_opened_real}) | "
            f"SL/убытки: **{report.sl_losers}**",
            "",
        ]
    )

    if zero_one:
        label = ACTION_RU.get(zero_one.action, zero_one.action)
        mult = (
            f", mult **{zero_one.suggested_weight_mult}**"
            if zero_one.suggested_weight_mult and zero_one.action == "increase_weight"
            else ""
        )
        lines.extend(
            [
                "## ⚡ Одна гипотеза (ZeroOne) — приоритет для агента",
                "",
                f"**{label}** фильтр/правило `{zero_one.filter_id}`{mult}",
                f"- Уверенность: {zero_one.confidence}, выборка n={zero_one.n_samples}",
                f"- Почему: {zero_one.reason_ru}",
                "",
            ]
        )

    if report.weight_recommendations:
        lines.append("## Рекомендации по весам и фильтрам")
        lines.append("")
        for rec in report.weight_recommendations[:10]:
            label = ACTION_RU.get(rec.action, rec.action)
            mult = (
                f" → mult **{rec.suggested_weight_mult}**"
                if rec.suggested_weight_mult and rec.action == "increase_weight"
                else ""
            )
            lines.append(
                f"- **{label}** `{rec.filter_id}`{mult} "
                f"({rec.confidence}, n={rec.n_samples}): {rec.reason_ru}"
            )
        lines.append("")

    if report.skip_filter_reviews:
        lines.append("## Ложные пропуски (виртуальный исход)")
        lines.append("")
        for rev in report.skip_filter_reviews[:6]:
            lines.append(
                f"- **{rev.skip_bucket}**: n={rev.n_total}, "
                f"вирт.TP={rev.virtual_tp_rate_pct:.0f}% — {rev.reason_ru}"
            )
        lines.append("")

    if report.rules:
        lines.append("## Топ правила удачного входа (TP)")
        lines.append("")
        for i, rule in enumerate(report.rules[:5], 1):
            lines.append(
                f"{i}. `{rule.field}` {rule.operator} `{rule.value}` — {rule.description_ru}"
            )
        lines.append("")

    if report.top_skip_reasons_on_tp:
        lines.append("## Пропущено, но дошло бы до TP")
        lines.append("")
        for reason, cnt in list(report.top_skip_reasons_on_tp.items())[:6]:
            lines.append(f"- **{reason}**: {cnt}")
        lines.append("")

    if report.suggested_rule_weights:
        lines.append("## Предлагаемые веса soft-rules (не авто-применять)")
        lines.append("")
        for k, v in list(report.suggested_rule_weights.items())[:12]:
            lines.append(f"- `{k}`: **{v}**")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "_Полный отчёт: `winning_entry_rules_report.md` (репо Analise_Hermes)_",
            "_История: `hermes_cursor_feed.jsonl` (репо [Analise_Hermes](https://github.com/gogi-eng/Analise_Hermes))_",
        ]
    )
    return "\n".join(lines)


def build_feed_event(
    report: WinningEntryRulesReport,
    *,
    source_label: str,
    cursor_path: str,
    fingerprint: str,
) -> Dict[str, Any]:
    zero_one = pick_zero_one_recommendation(report)
    return {
        "ts": _utc_now_iso(),
        "event": "hermes_analysis",
        "source": source_label,
        "hours": report.hours,
        "fingerprint": fingerprint,
        "outcome_counts": dict(report.outcome_counts),
        "tp_winners": report.tp_winners,
        "sl_losers": report.sl_losers,
        "top_recommendations": [
            asdict(r) for r in report.weight_recommendations[:8]
        ],
        "zero_one": asdict(zero_one) if zero_one else None,
        "rules_top3": [r.to_dict() for r in report.rules[:3]],
        "cursor_file": cursor_path,
    }


def append_jsonl(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_cursor_feed_files(
    report: WinningEntryRulesReport,
    *,
    repo_root: Path,
    data_dir: Path,
    source_label: str = "PRD-BOT",
    host_hint: str = "",
    append_history: bool = True,
) -> Tuple[Path, Path, Path]:
    """Записать брифинг для Cursor и зеркало в data/learning/."""
    brief = build_cursor_brief(
        report, source_label=source_label, host_hint=host_hint
    )
    fp = report_fingerprint(report)

    cursor_dir = repo_root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = cursor_dir / CURSOR_LIVE_FILENAME
    cursor_path.write_text(brief, encoding="utf-8")

    learning_dir = data_dir / "learning"
    learning_dir.mkdir(parents=True, exist_ok=True)
    mirror_path = learning_dir / MIRROR_MD_NAME
    mirror_path.write_text(brief, encoding="utf-8")

    feed_path = learning_dir / FEED_JSONL_NAME
    if append_history:
        append_jsonl(
            feed_path,
            build_feed_event(
                report,
                source_label=source_label,
                cursor_path=str(cursor_path.relative_to(repo_root)),
                fingerprint=fp,
            ),
        )

    return cursor_path, mirror_path, feed_path


def data_sources_mtime(data_dir: Path) -> float:
    """Максимальный mtime ключевых файлов данных (для watch-режима)."""
    latest = 0.0
    for rel in WATCH_PATHS_REL:
        p = data_dir / rel
        if p.is_file():
            latest = max(latest, p.stat().st_mtime)
    return latest


def load_last_feed_fingerprint(feed_path: Path) -> Optional[str]:
    if not feed_path.is_file():
        return None
    last_line = ""
    for line in feed_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last_line = line
    if not last_line:
        return None
    try:
        row = json.loads(last_line)
        return str(row.get("fingerprint") or "")
    except json.JSONDecodeError:
        return None
