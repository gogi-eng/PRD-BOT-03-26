"""Enrich parsed Telegram signal dict with SL/TP/entry from non-standard templates (Fibonacci, numbered lists)."""
from __future__ import annotations

import re
from typing import Any

# Scientific / very small prices (SATS, etc.)
SCI_NUM_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я])\$?\s*(\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)\b",
    flags=re.IGNORECASE,
)

PRICE_TAIL_RE = re.compile(
    r"(?<![A-Za-zА-Яа-я0-9])\$?\s*(\d+(?:[.,]\d+)?)(?!\s*[%xX):])",
    flags=re.IGNORECASE,
)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def extract_numeric_prices(text: str) -> list[float]:
    raw = text or ""
    out: list[float] = []
    for rx in (PRICE_TAIL_RE, SCI_NUM_RE):
        for m in rx.finditer(raw):
            v = safe_float(m.group(1))
            if v > 0:
                out.append(v)
    seen: set[float] = set()
    uniq: list[float] = []
    for p in out:
        key = round(p, 14)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def pick_take_profit(prices: list[float], side: str, entry: float) -> float:
    if entry <= 0 or not prices:
        return 0.0
    side_u = str(side or "").upper()
    if side_u == "BUY":
        cands = [p for p in prices if p > entry * 1.0001]
        return min(cands) if cands else 0.0
    if side_u == "SELL":
        cands = [p for p in prices if p < entry * 0.9999]
        return max(cands) if cands else 0.0
    return 0.0


def extract_fib_style_stop(raw: str) -> float:
    """Match 'Стоп-лосс' / 'Уровень Фибоначчи' style blocks."""
    patterns = [
        r"⛔\s*Стоп-лосс:?\s*",
        r"Стоп-лосс:?\s*",
        r"\bСтоп\b[^\n:：%\d]{0,16}(?:ниже|выше|за|под)?\s*[:：]?\s*",
        r"Уровень\s+Фибоначчи\s*1:?\s*",
        r"\b(?:СТОП|СТОП-ЛОСС)\b[^\n:：]{0,20}[:：]?\s*",
        r"\bSL\s*[#\d.]?\s*[:：]\s*",
        r"Стоп:?\s*",
        r"(?:❌|🛑)\s*(?:Стоп|SL|STOP)",
    ]
    text = raw or ""
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        chunk = text[m.end() : m.end() + 220]
        nums = extract_numeric_prices(chunk)
        if nums:
            return float(nums[0])
    return 0.0


def extract_tp_from_numbered_lines(raw: str) -> list[float]:
    """Lines like '1) 0.88463' or lines mentioning тейк/tp."""
    text = raw or ""
    prices: list[float] = []
    for line in text.splitlines():
        if re.search(r"^\s*[\-*•◇▪●]\s*", line):
            prices.extend(extract_numeric_prices(line))
        if re.search(r"^\s*\d+[\)\]]", line) or re.search(r"^\s*\d+\s*[\)\]]", line):
            prices.extend(extract_numeric_prices(line))
        if re.search(r"\b(тейк|tp|take|target|цель)\b", line, flags=re.IGNORECASE):
            prices.extend(extract_numeric_prices(line))
    return prices


def extract_entry_extra(raw: str) -> float:
    patterns = [
        r"\b(?:Точка\s+входа|Точк[аи]\s+входа|Открытие|OPEN|OPENING)\b[^\n:：]{0,24}[:：]?\s*",
        r"\b(?:Вход|ENTRY|LIMIT|LIMITS)\s*[:：]\s*(?:рынок|market|now)?\s*",
        r"\b(?:ЗОНА\s+НАБОРА|НАБОР|НАЧАЛО\s+СДЕЛКИ|ORDER\s*(?:ZONE|AREA)?)\b[^\n:：]{0,32}[:：]?\s*",
        r"(?:📍|📌|🔹)?\s*(?:ЦЕНА|PRICE)?\s*[:：]\s*",
        r"лонг\b[^\n\d]{0,40}(\d+(?:[.,]\d+)?)",
        r"шорт\b[^\n\d]{0,40}(\d+(?:[.,]\d+)?)",
    ]
    text = raw or ""
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
        if not m:
            continue
        chunk = text[m.end() : m.end() + 120]
        nums = extract_numeric_prices(chunk)
        if nums:
            return float(nums[0])
    return 0.0


def enrich_parsed_signal_levels(parsed: dict[str, Any], raw: str) -> None:
    """Mutate parsed keys: entry, stop_loss, take_profit (only fill zeros)."""
    side = str(parsed.get("side", ""))
    entry = float(parsed.get("entry") or 0.0)
    sl = float(parsed.get("stop_loss") or 0.0)
    tp = float(parsed.get("take_profit") or 0.0)

    if entry <= 0:
        ex = extract_entry_extra(raw)
        if ex > 0:
            parsed["entry"] = ex
            entry = ex

    numbered = extract_tp_from_numbered_lines(raw)
    all_prices = extract_numeric_prices(raw)

    if sl <= 0:
        fib_sl = extract_fib_style_stop(raw)
        if fib_sl > 0:
            parsed["stop_loss"] = fib_sl
            sl = fib_sl

    if tp <= 0:
        pool = numbered if numbered else all_prices
        if not pool:
            pool = all_prices
        best = pick_take_profit(pool, side, entry)
        if best > 0:
            parsed["take_profit"] = best
            tp = best

    if sl <= 0 and all_prices and entry > 0:
        side_u = str(side or "").upper()
        rest = [p for p in all_prices if abs(p - entry) / entry > 1e-6]
        if side_u == "BUY":
            below = [p for p in rest if p < entry]
            if below:
                parsed["stop_loss"] = float(max(below))
        elif side_u == "SELL":
            above = [p for p in rest if p > entry]
            if above:
                parsed["stop_loss"] = float(min(above))
