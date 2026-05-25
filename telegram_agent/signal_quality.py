"""
Фильтр качества постов из Telegram: отсечь рекламу, бинарные опционы и «псевдосигналы».
"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# Реклама, бинарники, сервисные посты — не торговые сигналы Bybit linear
NOISE_SUBSTRINGS = (
    "pocket option",
    "pocketoption",
    "quotex",
    "binolla",
    "binary option",
    "бинарн",
    "опцион",
    "casino",
    "казино",
    "розыгрыш",
    "подпишись",
    "subscribe",
    "реклама",
    "промокод",
    "promo code",
    "affiliate",
    "реферал",
    "вебинар",
    "обучение бесплат",
    "course",
    "курс",
    "не является инвест",
    "not financial advice",
    "мем",
    "lol",
    "good morning",
    "доброе утро",
    "итоги дня",
    "результаты недели",
    "отчёт",
    "отчет за",
    "withdraw",
    "вывод средств",
    "депозит",
    "deposit bonus",
)

# Должны быть в тексте хотя бы намёк на сделку
TRADE_CUES = re.compile(
    r"\b(LONG|SHORT|BUY|SELL|ЛОНГ|ШОРТ|ВХОД|ENTRY|SL|TP|STOP|СТОП|ТЕЙК|TARGET|"
    r"ПЛЕЧ|LEVERAGE|USDT|ПОКУП|ПРОДА)\b|⛔|🛑|🎯|✅",
    re.I,
)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "", (text or "").lower())


def is_noise_post(text: str, extra_substrings: Tuple[str, ...] = ()) -> Tuple[bool, str]:
    raw = (text or "").strip()
    if len(raw) < 12:
        return True, "слишком короткий пост"
    compact = _compact(raw)
    for sub in NOISE_SUBSTRINGS + extra_substrings:
        if sub and _compact(sub) in compact:
            return True, f"шум: {sub}"
    if not TRADE_CUES.search(raw):
        return True, "нет признаков сделки (SL/TP/сторона)"
    # Только эмодзи/цифры без структуры
    letters = sum(1 for c in raw if c.isalpha())
    if letters < 8:
        return True, "мало текста"
    return False, ""


def signal_levels_plausible(
    parsed: Dict[str, Any],
    *,
    max_sl_pct: float = 0.35,
    max_tp_pct: float = 2.5,
) -> Tuple[bool, str]:
    """SL/TP должны быть по правильную сторону от entry и не «космические» (Cornix мусор)."""
    side = str(parsed.get("side") or "").upper()
    entry = float(parsed.get("entry") or 0)
    sl = float(parsed.get("stop_loss") or 0)
    tp = float(parsed.get("take_profit") or 0)
    if entry <= 0:
        return False, "нет entry"
    if sl <= 0 or tp <= 0:
        return False, "нет SL/TP"
    if side == "BUY":
        if sl >= entry:
            return False, f"SL {sl} не ниже entry {entry} (LONG)"
        if tp <= entry:
            return False, f"TP {tp} не выше entry {entry} (LONG)"
    elif side == "SELL":
        if sl <= entry:
            return False, f"SL {sl} не выше entry {entry} (SHORT)"
        if tp >= entry:
            return False, f"TP {tp} не ниже entry {entry} (SHORT)"
    else:
        return False, "нет стороны"
    sl_dist = abs(entry - sl) / entry
    tp_dist = abs(tp - entry) / entry
    if sl_dist > max_sl_pct:
        return False, f"SL слишком далеко от entry ({sl_dist:.0%} > {max_sl_pct:.0%})"
    if tp_dist > max_tp_pct:
        return False, f"TP слишком далеко от entry ({tp_dist:.0%} > {max_tp_pct:.0%})"
    return True, ""


def structure_score(parsed: Dict[str, Any]) -> int:
    """0–100: насколько пост похож на полноценный сигнал."""
    score = 0
    side = str(parsed.get("side") or "").upper()
    if side in {"BUY", "SELL"}:
        score += 25
    entry = float(parsed.get("entry") or 0)
    sl = float(parsed.get("stop_loss") or 0)
    tp = float(parsed.get("take_profit") or 0)
    if entry > 0:
        score += 15
    if sl > 0:
        score += 30
    if tp > 0:
        score += 25
    pc = int(parsed.get("parser_confidence") or 0)
    if pc > 0:
        score += min(15, pc // 7)
    return min(100, score)


def passes_quality_gate(
    parsed: Dict[str, Any],
    text: str,
    cfg: Dict[str, Any],
    *,
    trusted: bool = False,
) -> Tuple[bool, str]:
    if not cfg.get("enabled", True):
        return True, ""
    extra = tuple(str(x) for x in (cfg.get("noise_keywords") or []))
    if cfg.get("skip_noise_posts", True):
        noisy, why = is_noise_post(text, extra)
        if noisy:
            return False, why
    side = str(parsed.get("side") or "").upper()
    if side not in {"BUY", "SELL"}:
        return False, "нет направления LONG/SHORT"
    sl = float(parsed.get("stop_loss") or 0)
    tp = float(parsed.get("take_profit") or 0)
    if cfg.get("require_stop_loss", True) and sl <= 0 and not trusted:
        return False, "нет SL в посте"
    if cfg.get("require_take_profit", False) and tp <= 0:
        return False, "нет TP в посте"
    ok_lv, why_lv = signal_levels_plausible(parsed)
    if not ok_lv:
        return False, f"уровни: {why_lv}"
    min_struct = int(cfg.get("min_structure_score", 55))
    if not trusted:
        sc = structure_score(parsed)
        if sc < min_struct:
            return False, f"слабая структура сигнала ({sc} < {min_struct})"
    min_pc = int(cfg.get("min_parser_confidence", 0) or 0)
    if min_pc > 0:
        pc = int(parsed.get("parser_confidence") or 0)
        if pc > 0 and pc < min_pc:
            return False, f"уверенность в тексте {pc}% < {min_pc}%"
    return True, ""


def rule_based_review(parsed: Dict[str, Any], market_regime: str = "unknown") -> Dict[str, Any]:
    """Быстрая оценка без OpenRouter для явно структурных сигналов."""
    sc = structure_score(parsed)
    sl = float(parsed.get("stop_loss") or 0)
    tp = float(parsed.get("take_profit") or 0)
    side = str(parsed.get("side") or "").upper()
    ok_lv, _ = signal_levels_plausible(parsed)
    approve = sc >= 72 and sl > 0 and tp > 0 and side in {"BUY", "SELL"} and ok_lv
    if market_regime == "chop":
        approve = approve and sc >= 78
    return {
        "approve": approve,
        "confidence": sc,
        "reason": "rule_based_structure",
    }
