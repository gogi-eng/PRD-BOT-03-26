#!/usr/bin/env python3
"""
Почасовой отчёт по ликвидным USDT-linear парам Bybit (публичный API, без ключей).

Фильтр: turnover24h >= 10M USDT. Теханализ топ-15 по обороту: тренд 1h/4h, RSI, HTF align.

Запуск:
  python scripts/hourly_liquid_pairs_report.py
  python scripts/hourly_liquid_pairs_report.py --top 10 --min-turnover 15000000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
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


def _build_report(
    pairs: List[PairAnalysis],
    *,
    min_turnover: float,
    top_n: int,
    liquid_count: int,
) -> dict:
    now_local = _local_now()
    return {
        "generated_at_local": now_local.isoformat(),
        "timezone_offset_hours": TIMEZONE_OFFSET_HOURS,
        "min_turnover_usdt": min_turnover,
        "top_n": top_n,
        "liquid_pairs_total": liquid_count,
        "analyzed_pairs": len(pairs),
        "pairs": [asdict(p) for p in pairs],
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
    lines.append("_Публичный API Bybit, без торговых рекомендаций._")
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


def run_report(*, min_turnover: float, top_n: int, out_dir: Path) -> dict:
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

    report = _build_report(
        analyzed,
        min_turnover=min_turnover,
        top_n=top_n,
        liquid_count=len(liquid),
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
    args = ap.parse_args()

    if args.top < 1:
        print("ERROR: --top должен быть >= 1", file=sys.stderr)
        return 2
    if args.min_turnover <= 0:
        print("ERROR: --min-turnover должен быть > 0", file=sys.stderr)
        return 2

    try:
        run_report(
            min_turnover=float(args.min_turnover),
            top_n=int(args.top),
            out_dir=Path(args.out_dir),
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
