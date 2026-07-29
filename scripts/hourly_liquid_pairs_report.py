#!/usr/bin/env python3
"""
Почасовой отчёт по ликвидным USDT-linear парам Bybit (публичный API, без ключей).

Фильтр: turnover24h >= 10M USDT. Теханализ топ-15 по обороту: тренд 1h/4h, RSI, HTF align.
В конце — лайт-вывод: один условный сигнал ИЛИ понятное «почему без сигнала».
Опционально: отправка текста в Telegram (Bot API, credentials из .env).

Запуск:
  python scripts/hourly_liquid_pairs_report.py
  python scripts/hourly_liquid_pairs_report.py --top 10 --min-turnover 15000000
  python scripts/hourly_liquid_pairs_report.py --telegram
  set LIQUID_PAIRS_TELEGRAM=1  (Windows) / export LIQUID_PAIRS_TELEGRAM=1
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API_BASE = "https://api.bybit.com"
USER_AGENT = "PRD-hourly-liquid-pairs/1.0"
DEFAULT_MIN_TURNOVER = 10_000_000.0
DEFAULT_TOP = 15
TIMEZONE_OFFSET_HOURS = 3
KLINE_LIMIT = 100
KLINE_PAUSE_SEC = 0.08

# Лайт-правила сигнала (условный совет, не ордер бота).
RSI_LONG_MAX = 70.0  # не лонговать в перекупленности (ужесточено с 75)
RSI_SHORT_MIN = 25.0  # не шортить в крайней перепроданности (chase)
EXTREME_CHANGE_ABS_PCT = 12.0  # |изм24ч| выше — памп/дамп-экстремум (было 15)
ALT_EXTREME_CHANGE_ABS_PCT = 8.0  # для альтов (не majors) порог жёстче
LONG_MIN_CHANGE_24H_PCT = -5.0  # LONG запрещён при сильном суточном дампе (отскок)
MAJORS = frozenset({"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"})
SL_PCT = 0.015  # −1.5% от входа
TP_PCT = 0.030  # +3.0% от входа (RR ≈ 2)
TG_DISCLAIMER = (
    "⚠️ УСЛОВНЫЙ СОВЕТ — НЕ автоторговля. Бот ордер НЕ ставит. "
    "Альты = высокий риск. Ручной вход только со своим SL."
)


@dataclass
class PairAnalysis:
    symbol: str
    last_price: float
    change_24h_pct: float
    turnover_24h: float
    trend_1h: str
    trend_4h: str
    rsi_1h: float
    htf_align: str
    note: str = ""


@dataclass
class SignalDecision:
    """Результат лайт-логики: сигнал или обоснование отсутствия."""

    has_signal: bool
    symbol: str = ""
    side: str = ""  # LONG | SHORT
    entry: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    reason: str = ""
    reject_notes: List[str] = field(default_factory=list)

    @property
    def md_heading(self) -> str:
        return "## Сигнал" if self.has_signal else "## Почему без сигнала"


def _local_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS)))


def _api_get(path: str, params: Optional[Dict[str, Any]] = None) -> dict:
    query = urllib.parse.urlencode(params or {})
    url = f"{API_BASE}{path}"
    if query:
        url = f"{url}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(
            f"Bybit API error retCode={payload.get('retCode')} retMsg={payload.get('retMsg')}"
        )
    return payload


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    if len(values) < period:
        return float(values[-1])
    multiplier = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    for val in values[period:]:
        ema_val = (val - ema_val) * multiplier + ema_val
    return float(ema_val)


def _rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _trend_label(closes: List[float]) -> str:
    if len(closes) < 25:
        return "нет данных"
    price = closes[-1]
    ema9 = _ema(closes, 9)
    ema21 = _ema(closes, 21)
    if price > ema21 and ema9 > ema21:
        return "бычий"
    if price < ema21 and ema9 < ema21:
        return "медвежий"
    return "боковик"


def _htf_align(trend_1h: str, trend_4h: str) -> str:
    bull = "бычий"
    bear = "медвежий"
    if trend_1h == bull and trend_4h == bull:
        return "совпадает ↑"
    if trend_1h == bear and trend_4h == bear:
        return "совпадает ↓"
    if trend_1h in (bull, bear) and trend_4h in (bull, bear) and trend_1h != trend_4h:
        return "конфликт"
    return "смешанный"


def _fetch_tickers() -> List[dict]:
    data = _api_get("/v5/market/tickers", {"category": "linear"})
    return list((data.get("result") or {}).get("list") or [])


def _fetch_klines(symbol: str, interval: str) -> List[float]:
    data = _api_get(
        "/v5/market/kline",
        {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": KLINE_LIMIT,
        },
    )
    rows = list((data.get("result") or {}).get("list") or [])
    # Bybit returns newest first.
    rows.reverse()
    closes: List[float] = []
    for row in rows:
        try:
            closes.append(float(row[4]))
        except (TypeError, ValueError, IndexError):
            continue
    return closes


def _select_liquid_pairs(
    tickers: List[dict], *, min_turnover: float, top_n: int
) -> List[dict]:
    selected: List[Tuple[float, dict]] = []
    for row in tickers:
        sym = str(row.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        try:
            turnover = float(row.get("turnover24h") or 0)
        except (TypeError, ValueError):
            continue
        if turnover < min_turnover:
            continue
        selected.append((turnover, row))
    selected.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in selected[:top_n]]


def _analyze_pair(row: dict) -> PairAnalysis:
    symbol = str(row.get("symbol") or "").upper()
    try:
        last_price = float(row.get("lastPrice") or row.get("markPrice") or 0)
    except (TypeError, ValueError):
        last_price = 0.0
    try:
        change_24h_pct = float(row.get("price24hPcnt") or 0) * 100.0
    except (TypeError, ValueError):
        change_24h_pct = 0.0
    try:
        turnover_24h = float(row.get("turnover24h") or 0)
    except (TypeError, ValueError):
        turnover_24h = 0.0

    try:
        closes_1h = _fetch_klines(symbol, "60")
        time.sleep(KLINE_PAUSE_SEC)
        closes_4h = _fetch_klines(symbol, "240")
        time.sleep(KLINE_PAUSE_SEC)
    except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
        return PairAnalysis(
            symbol=symbol,
            last_price=last_price,
            change_24h_pct=change_24h_pct,
            turnover_24h=turnover_24h,
            trend_1h="ошибка",
            trend_4h="ошибка",
            rsi_1h=0.0,
            htf_align="—",
            note=str(exc),
        )

    trend_1h = _trend_label(closes_1h)
    trend_4h = _trend_label(closes_4h)
    rsi_1h = _rsi(closes_1h, 14) if closes_1h else 50.0
    align = _htf_align(trend_1h, trend_4h)

    note_parts: List[str] = []
    if rsi_1h >= 70:
        note_parts.append("RSI перекуплен")
    elif rsi_1h <= 30:
        note_parts.append("RSI перепродан")

    return PairAnalysis(
        symbol=symbol,
        last_price=last_price,
        change_24h_pct=change_24h_pct,
        turnover_24h=turnover_24h,
        trend_1h=trend_1h,
        trend_4h=trend_4h,
        rsi_1h=round(rsi_1h, 1),
        htf_align=align,
        note="; ".join(note_parts),
    )


def _pair_from_mapping(raw: Any) -> Optional[PairAnalysis]:
    """Принять PairAnalysis или dict (из JSON) — для тестов и повторного чтения."""
    if isinstance(raw, PairAnalysis):
        return raw
    if not isinstance(raw, dict):
        return None
    try:
        return PairAnalysis(
            symbol=str(raw.get("symbol") or "").upper(),
            last_price=float(raw.get("last_price") or 0),
            change_24h_pct=float(raw.get("change_24h_pct") or 0),
            turnover_24h=float(raw.get("turnover_24h") or 0),
            trend_1h=str(raw.get("trend_1h") or ""),
            trend_4h=str(raw.get("trend_4h") or ""),
            rsi_1h=float(raw.get("rsi_1h") or 0),
            htf_align=str(raw.get("htf_align") or ""),
            note=str(raw.get("note") or ""),
        )
    except (TypeError, ValueError):
        return None


def _levels_for_side(price: float, side: str) -> Tuple[float, float, float]:
    entry = float(price)
    if side == "LONG":
        sl = entry * (1.0 - SL_PCT)
        tp = entry * (1.0 + TP_PCT)
    else:
        sl = entry * (1.0 + SL_PCT)
        tp = entry * (1.0 - TP_PCT)
    return entry, sl, tp


def _candidate_score(pair: PairAnalysis, side: str) -> float:
    """Выше = лучше. Предпочитаем majors, умеренный RSI, не экстремальный ход, оборот."""
    rsi = float(pair.rsi_1h)
    chg = abs(float(pair.change_24h_pct))
    turnover = max(float(pair.turnover_24h), 1.0)
    if side == "LONG":
        rsi_room = RSI_LONG_MAX - rsi
    else:
        rsi_room = rsi - RSI_SHORT_MIN
    major_bonus = 8.0 if pair.symbol in MAJORS else 0.0
    return rsi_room * 2.0 - chg * 0.5 + math.log10(turnover) + major_bonus


def _find_pair(pairs: List[PairAnalysis], symbol: str) -> Optional[PairAnalysis]:
    for p in pairs:
        if p.symbol == symbol:
            return p
    return None


def _market_blocks_long(pairs: List[PairAnalysis]) -> Optional[str]:
    """
    Контекст BTC/ETH: не рекомендовать LONG по альтам, если рынок сверху давит.
    BTC «совпадает ↓» или 4h медвежий → блок LONG (кроме самого BTC при его HTF↑ — редко).
    """
    btc = _find_pair(pairs, "BTCUSDT")
    eth = _find_pair(pairs, "ETHUSDT")
    if btc is not None:
        if btc.htf_align == "совпадает ↓":
            return "BTC: тренды 1h/4h вниз — LONG по альтам против рынка"
        if btc.trend_4h == "медвежий":
            return "BTC 4h медвежий — LONG по альтам против старшего тренда"
    if eth is not None and eth.htf_align == "совпадает ↓":
        if btc is None or btc.trend_4h != "бычий":
            return "ETH: тренды вниз при слабом BTC — LONG по альтам рискован"
    return None


def _extreme_threshold(symbol: str) -> float:
    if symbol in MAJORS:
        return EXTREME_CHANGE_ABS_PCT
    return ALT_EXTREME_CHANGE_ABS_PCT


def decide_liquid_pairs_signal(pairs: List[Any]) -> SignalDecision:
    """
    Чистая логика без сети: выбрать 1 лучший условный сигнал или объяснить «почему нет».

    Правила:
    - HTF совпадение ↑ → LONG, ↓ → SHORT
    - RSI не в крайней зоне (LONG: RSI <= 70; SHORT: RSI >= 25)
    - не памп/дамп-экстремум (|изм24ч|: majors <12%, альты <8%)
    - LONG запрещён при суточном дампе (изм24ч < −5%) — отскок в падении
    - LONG по альтам блокируется, если BTC/ETH HTF↓ или BTC 4h медвежий
    - цена > 0, тренды не «ошибка»
    """
    normalized: List[PairAnalysis] = []
    for item in pairs:
        p = _pair_from_mapping(item)
        if p is not None and p.symbol:
            normalized.append(p)

    if not normalized:
        return SignalDecision(
            has_signal=False,
            reason=(
                "Нет данных по парам для анализа. "
                "Дождитесь следующего часа или проверьте доступ к Bybit API."
            ),
        )

    candidates: List[Tuple[float, PairAnalysis, str]] = []
    reject_notes: List[str] = []
    n_no_htf = 0
    n_rsi_extreme = 0
    n_pump_dump = 0
    n_dump_bounce = 0
    n_market_ctx = 0
    n_htf_ok_but_blocked = 0

    market_long_block = _market_blocks_long(normalized)

    for p in normalized:
        if p.trend_1h == "ошибка" or p.trend_4h == "ошибка" or p.last_price <= 0:
            reject_notes.append(f"{p.symbol}: нет корректных данных")
            continue

        align = p.htf_align
        if align == "совпадает ↑":
            side = "LONG"
        elif align == "совпадает ↓":
            side = "SHORT"
        else:
            n_no_htf += 1
            continue

        # HTF есть — проверяем RSI, экстремум, дамп-отскок, контекст рынка.
        blocked = False
        if side == "LONG" and p.rsi_1h > RSI_LONG_MAX:
            n_rsi_extreme += 1
            n_htf_ok_but_blocked += 1
            reject_notes.append(
                f"{p.symbol}: тренды вверх совпадают, но RSI={p.rsi_1h:.0f} "
                f"(перекуплен, риск входа у верха)"
            )
            blocked = True
        elif side == "SHORT" and p.rsi_1h < RSI_SHORT_MIN:
            n_rsi_extreme += 1
            n_htf_ok_but_blocked += 1
            reject_notes.append(
                f"{p.symbol}: тренды вниз совпадают, но RSI={p.rsi_1h:.0f} "
                f"(перепродан, опасно шортить на дне)"
            )
            blocked = True

        thr = _extreme_threshold(p.symbol)
        if abs(p.change_24h_pct) >= thr:
            n_pump_dump += 1
            n_htf_ok_but_blocked += 1
            direction = "памп" if p.change_24h_pct > 0 else "дамп"
            reject_notes.append(
                f"{p.symbol}: сильный {direction} за сутки "
                f"({p.change_24h_pct:+.1f}%), экстремум — пропускаем"
            )
            blocked = True

        if side == "LONG" and p.change_24h_pct < LONG_MIN_CHANGE_24H_PCT:
            n_dump_bounce += 1
            n_htf_ok_but_blocked += 1
            reject_notes.append(
                f"{p.symbol}: суточный ход {p.change_24h_pct:+.1f}% — "
                f"LONG на отскоке после дампа запрещён"
            )
            blocked = True

        if (
            side == "LONG"
            and market_long_block
            and p.symbol not in ("BTCUSDT", "ETHUSDT")
        ):
            n_market_ctx += 1
            n_htf_ok_but_blocked += 1
            reject_notes.append(f"{p.symbol}: {market_long_block}")
            blocked = True

        if blocked:
            continue

        score = _candidate_score(p, side)
        candidates.append((score, p, side))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        _score, best, side = candidates[0]
        entry, sl, tp = _levels_for_side(best.last_price, side)
        risk_note = ""
        if best.symbol not in MAJORS:
            risk_note = " Высокий риск альта — не автоторговля."
        if side == "LONG":
            why = (
                f"Тренды 1h и 4h вверх совпадают, RSI={best.rsi_1h:.0f} "
                f"не в зоне перекупленности, суточный ход {best.change_24h_pct:+.1f}% "
                f"без пампа. Условные уровни: SL −{SL_PCT*100:.1f}%, TP +{TP_PCT*100:.1f}%."
                f"{risk_note}"
            )
        else:
            why = (
                f"Тренды 1h и 4h вниз совпадают, RSI={best.rsi_1h:.0f} "
                f"не в зоне перепроданности, суточный ход {best.change_24h_pct:+.1f}% "
                f"без дампа-экстремума. Условные уровни: SL +{SL_PCT*100:.1f}%, "
                f"TP −{TP_PCT*100:.1f}%."
                f"{risk_note}"
            )
        return SignalDecision(
            has_signal=True,
            symbol=best.symbol,
            side=side,
            entry=entry,
            sl=sl,
            tp=tp,
            reason=why,
            reject_notes=reject_notes[:8],
        )

    # Нет подходящего сигнала — простое обоснование.
    parts: List[str] = []
    total = len(normalized)
    if n_no_htf >= max(1, total // 2):
        parts.append(
            "у большинства пар нет совпадения трендов 1h и 4h "
            "(конфликт или боковик) — нет ясного направления"
        )
    elif n_no_htf > 0:
        parts.append("мало пар с совпадением трендов 1h/4h")

    if n_rsi_extreme > 0:
        parts.append(
            "где тренды совпадают, RSI в крайней зоне "
            "(перекуплен для лонга / перепродан для шорта)"
        )
    if n_pump_dump > 0:
        parts.append(
            "есть сильные пампы/дампы за сутки — вход у экстремума рискован"
        )
    if n_dump_bounce > 0:
        parts.append("LONG на отскоке после суточного дампа отклонён")
    if n_market_ctx > 0:
        parts.append(
            "контекст BTC/ETH не поддерживает LONG по альтам"
        )
    if n_htf_ok_but_blocked == 0 and n_no_htf == total:
        parts.append("лучше подождать более спокойной и согласованной картины")

    if not parts:
        parts.append(
            "сейчас нет пары, где одновременно совпадают старшие тренды, "
            "RSI не в крайней зоне и суточный ход умеренный"
        )

    reason = "Сейчас без сигнала: " + "; ".join(parts) + "."
    return SignalDecision(
        has_signal=False,
        reason=reason,
        reject_notes=reject_notes[:10],
    )


def _format_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}".replace(",", " ")
    if value >= 1:
        return f"{value:.4g}"
    return f"{value:.6g}"


def format_signal_markdown(decision: SignalDecision) -> str:
    lines = [decision.md_heading, ""]
    if decision.has_signal:
        lines.append(
            f"**{decision.symbol} {decision.side}** — условный совет, "
            f"**НЕ** ордер бота, **НЕ** автоторговля"
        )
        lines.append("")
        lines.append(f"- Вход: `{_format_price(decision.entry)}`")
        lines.append(f"- SL: `{_format_price(decision.sl)}`")
        lines.append(f"- TP: `{_format_price(decision.tp)}`")
        lines.append("")
        lines.append(decision.reason)
        if decision.symbol and decision.symbol not in MAJORS:
            lines.append("")
            lines.append("⚠️ Высокий риск альта — вход только вручную и со своим SL.")
    else:
        lines.append(decision.reason)
        if decision.reject_notes:
            lines.append("")
            lines.append("Кратко по отклонённым:")
            for note in decision.reject_notes[:5]:
                lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def format_telegram_message(report: dict, decision: SignalDecision) -> str:
    """Текст для Telegram: либо сигнал, либо «почему нет»."""
    ts = str(report.get("generated_at_local") or "")[:16].replace("T", " ")
    offset = report.get("timezone_offset_hours", TIMEZONE_OFFSET_HOURS)
    head = f"📊 Ликвидные пары (UTC+{offset}) {ts}"
    lines = [head, ""]

    if decision.has_signal:
        lines.append("📋 Условный совет (НЕ ордер бота)")
        lines.append(TG_DISCLAIMER)
        lines.append("")
        lines.append(f"{decision.symbol} {decision.side}")
        lines.append(f"Вход: {_format_price(decision.entry)}")
        lines.append(f"SL: {_format_price(decision.sl)}")
        lines.append(f"TP: {_format_price(decision.tp)}")
        lines.append("")
        lines.append(decision.reason)
        if decision.symbol and decision.symbol not in MAJORS:
            lines.append("")
            lines.append("⚠️ Альт — высокий риск, не копируйте без своего SL.")
    else:
        lines.append("⏸ Без сигнала сейчас")
        lines.append("")
        lines.append(decision.reason)

    lines.append("")
    lines.append(
        f"Ликвидных: {report.get('liquid_pairs_total', '?')} | "
        f"в анализе: {report.get('analyzed_pairs', '?')}"
    )
    text = "\n".join(lines)
    return text[:4000]


def _decision_to_dict(decision: SignalDecision) -> dict:
    return {
        "has_signal": decision.has_signal,
        "symbol": decision.symbol,
        "side": decision.side,
        "entry": decision.entry,
        "sl": decision.sl,
        "tp": decision.tp,
        "reason": decision.reason,
        "reject_notes": list(decision.reject_notes),
        "heading": decision.md_heading,
    }


def _build_report(
    pairs: List[PairAnalysis],
    *,
    min_turnover: float,
    top_n: int,
    liquid_count: int,
    decision: Optional[SignalDecision] = None,
) -> dict:
    now_local = _local_now()
    if decision is None:
        decision = decide_liquid_pairs_signal(pairs)
    return {
        "generated_at_local": now_local.isoformat(),
        "timezone_offset_hours": TIMEZONE_OFFSET_HOURS,
        "min_turnover_usdt": min_turnover,
        "top_n": top_n,
        "liquid_pairs_total": liquid_count,
        "analyzed_pairs": len(pairs),
        "pairs": [asdict(p) for p in pairs],
        "signal": _decision_to_dict(decision),
    }


def _format_console(report: dict) -> str:
    lines = [
        "=== Ликвидные пары Bybit (linear, turnover >= {:.1f}M USDT) ===".format(
            report["min_turnover_usdt"] / 1_000_000
        ),
        f"Время (UTC+{report['timezone_offset_hours']}): {report['generated_at_local']}",
        f"Всего ликвидных пар: {report['liquid_pairs_total']} | анализ топ-{report['top_n']}: {report['analyzed_pairs']}",
        "",
    ]
    for idx, p in enumerate(report["pairs"], start=1):
        lines.append(
            f"{idx:2}. {p['symbol']:<12}  цена={p['last_price']:.6g}  "
            f"изм24ч={p['change_24h_pct']:+.2f}%  оборот={p['turnover_24h']/1e6:.1f}M"
        )
        lines.append(
            f"    1h={p['trend_1h']:<9} 4h={p['trend_4h']:<9} "
            f"RSI(1h)={p['rsi_1h']:.1f}  HTF={p['htf_align']}"
            + (f"  ({p['note']})" if p.get("note") else "")
        )
    sig = report.get("signal") or {}
    lines.append("")
    if sig.get("has_signal"):
        lines.append(
            f"СИГНАЛ: {sig.get('symbol')} {sig.get('side')} "
            f"вход={sig.get('entry')} SL={sig.get('sl')} TP={sig.get('tp')}"
        )
        lines.append(f"  {sig.get('reason')}")
    else:
        lines.append(f"БЕЗ СИГНАЛА: {sig.get('reason')}")
    return "\n".join(lines)


def _format_markdown(report: dict) -> str:
    lines = [
        "# Ликвидные пары Bybit",
        "",
        f"- **Время (UTC+{report['timezone_offset_hours']})**: {report['generated_at_local']}",
        f"- **Фильтр оборота**: >= {report['min_turnover_usdt']:,.0f} USDT",
        f"- **Ликвидных пар всего**: {report['liquid_pairs_total']}",
        f"- **Проанализировано (топ по обороту)**: {report['analyzed_pairs']}",
        "",
        "| # | Символ | Цена | изм24ч | Оборот 24h | 1h | 4h | RSI 1h | HTF |",
        "|---:|---|---:|---:|---:|---|---|---:|---|",
    ]
    for idx, p in enumerate(report["pairs"], start=1):
        note = f" ({p['note']})" if p.get("note") else ""
        lines.append(
            f"| {idx} | {p['symbol']} | {p['last_price']:.6g} | "
            f"{p['change_24h_pct']:+.2f}% | {p['turnover_24h']/1e6:.1f}M | "
            f"{p['trend_1h']} | {p['trend_4h']} | {p['rsi_1h']:.1f} | "
            f"{p['htf_align']}{note} |"
        )
    lines.append("")
    sig = report.get("signal") or {}
    decision = SignalDecision(
        has_signal=bool(sig.get("has_signal")),
        symbol=str(sig.get("symbol") or ""),
        side=str(sig.get("side") or ""),
        entry=float(sig.get("entry") or 0),
        sl=float(sig.get("sl") or 0),
        tp=float(sig.get("tp") or 0),
        reason=str(sig.get("reason") or ""),
        reject_notes=list(sig.get("reject_notes") or []),
    )
    lines.append(format_signal_markdown(decision).rstrip())
    lines.append("")
    lines.append(
        "_Публичный API Bybit. Блок «Сигнал» — условный лайт-совет, "
        "НЕ автоматический ордер бота. Альты = высокий риск._"
    )
    return "\n".join(lines)


def _save_reports(report: dict, out_dir: Path) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _local_now().strftime("%Y%m%d_%H")
    json_path = out_dir / f"liquid_pairs_{ts}.json"
    md_latest = out_dir / "liquid_pairs_latest.md"
    json_latest = out_dir / "liquid_pairs_latest.json"

    json_text = json.dumps(report, ensure_ascii=False, indent=2)
    md_text = _format_markdown(report)

    json_path.write_text(json_text, encoding="utf-8")
    json_latest.write_text(json_text, encoding="utf-8")
    md_latest.write_text(md_text, encoding="utf-8")
    return json_path, md_latest


def _load_dotenv_file(path: Path) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, val)


def _ensure_telegram_env(repo_root: Path) -> None:
    """Читает .env из корня проекта и, при наличии, AGENT-WORLD/.env (setdefault)."""
    _load_dotenv_file(repo_root / ".env")
    sibling = repo_root.parent / "AGENT-WORLD" / ".env"
    if sibling.is_file():
        _load_dotenv_file(sibling)
    local_aw = repo_root / "AGENT-WORLD" / ".env"
    if local_aw.is_file():
        _load_dotenv_file(local_aw)


def _telegram_credentials(repo_root: Path) -> Tuple[str, str]:
    _ensure_telegram_env(repo_root)
    token = (
        os.environ.get("TELEGRAM_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN")
        or ""
    ).strip()
    chat_id = (
        os.environ.get("TELEGRAM_CHAT_ID")
        or os.environ.get("TELEGRAM_CHANNEL_ID")
        or ""
    ).strip()
    return token, chat_id


def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Синхронная отправка через Bot API sendMessage. Секреты не логирует."""
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": text[:4096],
            "disable_web_page_preview": True,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"telegram send failed: {exc}", file=sys.stderr)
        return False


def maybe_send_telegram(
    report: dict,
    decision: SignalDecision,
    *,
    repo_root: Path,
    enabled: bool,
) -> str:
    """
    Отправить сообщение в Telegram при enabled=True.
    Возвращает статус-строку для лога (без секретов).
    """
    if not enabled:
        return "telegram skip: disabled"
    token, chat_id = _telegram_credentials(repo_root)
    if not token or not chat_id:
        return "telegram skip: no credentials"
    text = format_telegram_message(report, decision)
    ok = send_telegram_message(token, chat_id, text)
    return "telegram ok" if ok else "telegram failed"


def _env_flag_true(name: str) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "да")


def run_report(
    *,
    min_turnover: float,
    top_n: int,
    out_dir: Path,
    telegram: bool = False,
    repo_root: Optional[Path] = None,
) -> dict:
    root = repo_root or Path(__file__).resolve().parents[1]
    tickers = _fetch_tickers()
    liquid = [
        row
        for row in tickers
        if str(row.get("symbol") or "").upper().endswith("USDT")
        and float(row.get("turnover24h") or 0) >= min_turnover
    ]
    top_rows = _select_liquid_pairs(tickers, min_turnover=min_turnover, top_n=top_n)

    analyzed: List[PairAnalysis] = []
    for row in top_rows:
        analyzed.append(_analyze_pair(row))

    decision = decide_liquid_pairs_signal(analyzed)
    report = _build_report(
        analyzed,
        min_turnover=min_turnover,
        top_n=top_n,
        liquid_count=len(liquid),
        decision=decision,
    )
    text = _format_console(report)
    try:
        print(text)
    except UnicodeEncodeError:
        # Windows-консоль cp1251: безопасный вывод кириллицы.
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    json_path, md_path = _save_reports(report, out_dir)
    print("")
    print(f"JSON: {json_path.resolve()}")
    print(f"MD latest: {md_path.resolve()}")

    tg_status = maybe_send_telegram(
        report, decision, repo_root=root, enabled=telegram
    )
    print(tg_status)
    return report


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(
        description="Почасовой отчёт по ликвидным USDT-linear парам Bybit (публичный API)."
    )
    ap.add_argument(
        "--min-turnover",
        type=float,
        default=DEFAULT_MIN_TURNOVER,
        help=f"Мин. оборот 24ч в USDT (по умолчанию {DEFAULT_MIN_TURNOVER:,.0f}).",
    )
    ap.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        help=f"Сколько пар анализировать (топ по обороту, по умолчанию {DEFAULT_TOP}).",
    )
    ap.add_argument(
        "--out-dir",
        default=str(root / "data" / "reports"),
        help="Папка для JSON/MD отчётов (по умолчанию data/reports).",
    )
    ap.add_argument(
        "--telegram",
        action="store_true",
        help="Отправить итог (сигнал или «почему нет») в Telegram после отчёта.",
    )
    ap.add_argument(
        "--no-telegram",
        action="store_true",
        help="Явно не слать в Telegram (даже если LIQUID_PAIRS_TELEGRAM=1).",
    )
    args = ap.parse_args()

    if args.top < 1:
        print("ERROR: --top должен быть >= 1", file=sys.stderr)
        return 2
    if args.min_turnover <= 0:
        print("ERROR: --min-turnover должен быть > 0", file=sys.stderr)
        return 2

    want_telegram = False
    if args.no_telegram:
        want_telegram = False
    elif args.telegram or _env_flag_true("LIQUID_PAIRS_TELEGRAM"):
        want_telegram = True

    try:
        run_report(
            min_turnover=float(args.min_turnover),
            top_n=int(args.top),
            out_dir=Path(args.out_dir),
            telegram=want_telegram,
            repo_root=root,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
