"""
Краткий брифинг Hermes для Telegram / Cursor.
Разделяет рекомендации на «лимиты безопасности» (не трогать) и «настраиваемые фильтры».
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Жёсткие лимиты — Hermes не должен предлагать «снять» без явного решения пользователя.
_SAFETY_PATTERNS = (
    re.compile(r"на бирже уже открыта позиция", re.I),
    re.compile(r"дневной лимит убытка", re.I),
    re.compile(r"лимит сделок на сегодня", re.I),
    re.compile(r"кулдаун после убытка", re.I),
    re.compile(r"order does not meet minimum", re.I),
    re.compile(r"max_positions", re.I),
)

_TUNABLE_IDS = (
    "entry_guard",
    "impulse_retest",
    "zone_entry",
    "supervisor",
    "derivatives_entry_guard",
    "spread_wide",
    "regime_chop",
    "atr_sweet",
)


def classify_skip_bucket(bucket: str) -> str:
    """
    safety — защита депозита / инфраструктура;
    tunable — можно ослабить в config после теста на песочнице;
    review — смешанный эффект, нужно больше данных.
    """
    text = str(bucket or "").strip()
    if not text:
        return "review"
    low = text.lower()
    if low == "supervisor":
        return "safety"
    for pat in _SAFETY_PATTERNS:
        if pat.search(text):
            return "safety"
    for tid in _TUNABLE_IDS:
        if tid in low:
            return "tunable"
    return "review"


def classify_weight_recommendation(filter_id: str) -> str:
    fid = str(filter_id or "")
    if fid.startswith("skip:"):
        return classify_skip_bucket(fid[5:])
    return classify_skip_bucket(fid)


def _hermes_search_paths(root: Path) -> List[Path]:
    root = root.resolve()
    parent = root.parent
    return [
        root / "data" / "hermes" / "winning_entry_rules.json",
        parent / "Analise_Hermes" / "winning_entry_rules.json",
        root / ".cursor" / "winning_entry_rules.json",
    ]


def load_winning_entry_rules(root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Path]]:
    for path in _hermes_search_paths(root):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data, path
            except (json.JSONDecodeError, OSError):
                continue
    return None, None


def _load_live_md(root: Path) -> str:
    for path in (
        root / ".cursor" / "HERMES_LIVE.md",
        root.parent / "Analise_Hermes" / "HERMES_LIVE.md",
        root / "data" / "hermes" / "HERMES_LIVE.md",
    ):
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return ""


def _extract_zero_one_from_md(md: str) -> str:
    if not md:
        return ""
    for line in md.splitlines():
        if line.startswith("**Рассмотреть отказ от фильтра**"):
            return line.strip()
        if line.startswith("- **Рассмотреть отказ от фильтра**"):
            return line.strip().lstrip("- ").strip()
    return ""


def build_hermes_telegram_briefing(root: Path) -> str:
    """HTML-отчёт для кнопки 📊 Hermes в Telegram."""
    data, src = load_winning_entry_rules(root)
    if not data:
        return (
            "<b>📊 Hermes</b>\n\n"
            "Нет файла <code>winning_entry_rules.json</code>.\n"
            "Синхронизируйте Analise_Hermes (скрипт <code>hermes_sync_from_github.ps1</code>) "
            "или дождитесь отчёта с сервера."
        )

    oc = data.get("outcome_counts") or {}
    profit = int(oc.get("profit") or 0)
    loss = int(oc.get("loss") or 0)
    neutral = int(oc.get("neutral") or 0)
    hours = data.get("hours", "?")
    gen = str(data.get("generated_at") or "")[:19]

    lines = [
        "<b>📊 Hermes — брифинг</b>",
        f"Окно: <code>{hours}</code> ч | обновлено: <code>{gen}</code>",
        f"Исходы: ✅ {profit} | ❌ {loss} | ≈ {neutral}",
        f"TP: <code>{data.get('tp_winners', '—')}</code> "
        f"(вирт {data.get('tp_skipped_virtual', '—')}, биржа {data.get('tp_opened_real', '—')})",
        "",
    ]
    if src:
        lines.append(f"<i>Источник: {src.name}</i>")
        lines.append("")

    tunable: List[str] = []
    safety_notes: List[str] = []
    for rec in data.get("weight_recommendations") or []:
        if not isinstance(rec, dict):
            continue
        fid = str(rec.get("filter_id") or "")
        kind = classify_weight_recommendation(fid)
        action = str(rec.get("action") or "")
        if action != "consider_remove":
            continue
        label = fid.replace("skip:", "")[:48]
        n = rec.get("n_samples", "?")
        conf = rec.get("confidence", "?")
        if kind == "safety":
            safety_notes.append(f"🔒 {label} (n={n}) — <b>не снимать</b> (лимит безопасности)")
        elif kind == "tunable":
            tunable.append(
                f"🔧 {label} (n={n}, {conf}): {str(rec.get('reason_ru') or '')[:80]}"
            )

    if tunable:
        lines.append("<b>Можно тестировать на AGENT-WORLD (по одной правке):</b>")
        lines.extend(tunable[:5])
        lines.append("")

    if safety_notes:
        lines.append("<b>Не трогать (защита депозита):</b>")
        lines.extend(safety_notes[:4])
        lines.append("")

    reviews = data.get("skip_filter_reviews") or []
    keep = [
        r for r in reviews
        if isinstance(r, dict) and r.get("recommendation") == "keep_strict"
    ]
    if keep:
        top_keep = sorted(keep, key=lambda x: int(x.get("n_virtual_loss") or 0), reverse=True)[:3]
        lines.append("<b>Оставить строгими (спасают от SL):</b>")
        for r in top_keep:
            bucket = str(r.get("skip_bucket") or "")[:40]
            vl = r.get("n_virtual_loss", 0)
            lines.append(f"🛡 {bucket}: вирт.SL {vl}")
        lines.append("")

    rules = data.get("rules") or []
    if rules:
        lines.append("<b>Топ правила удачного TP:</b>")
        for r in rules[:4]:
            if isinstance(r, dict):
                lines.append(f"• {r.get('description_ru', r.get('rule_id', ''))}")
        lines.append("")

    impacts = data.get("filter_impacts") or []
    bad_soft = [
        f for f in impacts
        if isinstance(f, dict) and float(f.get("lift_pct") or 0) <= -8.0
    ]
    if bad_soft:
        lines.append("<b>Soft-rules с отрицательным lift (ослабить вес):</b>")
        for f in bad_soft[:4]:
            lines.append(
                f"• <code>{f.get('filter_id')}</code> WR {f.get('win_rate_pct')}% "
                f"(lift {f.get('lift_pct')}%)"
            )
        lines.append("")

    md = _load_live_md(root)
    zero = _extract_zero_one_from_md(md)
    if zero:
        z_kind = classify_weight_recommendation(zero)
        if z_kind == "safety":
            lines.append("⚠️ ZeroOne из HERMES_LIVE — <b>лимит безопасности</b>, игнорировать.")
        else:
            lines.append(f"<b>ZeroOne:</b> {zero[:200]}")

    lines.append("\n<i>Одна правка config за цикл. Прод — только после 5–7 дней AGENT-WORLD.</i>")
    return "\n".join(lines)
