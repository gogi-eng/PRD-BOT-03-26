"""Проверка: вход через 10 или 15 минут против ликвидации Bybit.

Берёт публичные ликвидации и минутные свечи. Ордера не ставит.
Запуск: python scripts/backtest_liquidation_fade.py
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.market.liquidity_alerts import (  # noqa: E402
    FEE_ROUNDTRIP_PCT,
    LiqPrint,
    simulate_fade,
    summarize_fade,
)

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
MIN_USD = 20_000.0
DELAYS = (10, 15)
HOLDS = (15, 30, 60)
DAY_MS = 24 * 60 * 60 * 1000


def _get(path: str, params: dict) -> dict:
    query = urllib.parse.urlencode(params)
    url = f"https://api.bybit.com{path}?{query}"
    req = urllib.request.Request(url, headers={"User-Agent": "prd-bot-fade-check"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if int(payload.get("retCode", -1)) != 0:
        raise RuntimeError(f"{path} retCode={payload.get('retCode')} {payload.get('retMsg')}")
    return payload.get("result") or {}


def fetch_liquidations(symbol: str) -> List[LiqPrint]:
    """Публичной архивной ленты у Bybit нет: запрос ниже часто отвечает 404.

    Живые ликвидации бот видит только по потоку, пока сам работает.
    """
    result = _get(
        "/v5/market/recent-liquidation",
        {"category": "linear", "symbol": symbol, "limit": 500},
    )
    rows = result.get("list") or result.get("data") or []
    events: List[LiqPrint] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        side = str(row.get("side") or row.get("S") or "")
        try:
            price = float(row.get("price") or row.get("p") or 0)
            size = float(row.get("size") or row.get("v") or row.get("qty") or 0)
            ts = int(row.get("updatedTime") or row.get("time") or row.get("T") or 0)
        except (TypeError, ValueError):
            continue
        if ts and ts < 10**12:
            ts *= 1000
        usd = price * size
        if side and ts and usd > 0:
            events.append(LiqPrint(ts_ms=ts, symbol=symbol, position_side=side, usd=usd))
    return events


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> List[Tuple[int, float]]:
    candles: Dict[int, float] = {}
    cursor = end_ms
    guard = 0
    while cursor > start_ms and guard < 12:
        guard += 1
        result = _get(
            "/v5/market/kline",
            {
                "category": "linear",
                "symbol": symbol,
                "interval": "1",
                "end": cursor,
                "limit": 1000,
            },
        )
        rows = result.get("list") or []
        if not rows:
            break
        oldest = None
        for row in rows:
            start = int(row[0])
            close = float(row[4])
            candles[start] = close
            oldest = start if oldest is None else min(oldest, start)
        if oldest is None or oldest >= cursor:
            break
        cursor = oldest - 1
    return sorted(candles.items())


def _line(title: str, summary: dict) -> str:
    n = int(summary["n"])
    if n == 0:
        return f"{title}: сделок 0"
    return (
        f"{title}: сделок {n}, плюсов {int(summary['wins'])} "
        f"({summary['win_rate'] * 100:.0f}%), средняя {summary['avg_net_pct']:+.3f}% "
        f"после комиссии, сумма {summary['sum_net_pct']:+.2f}%"
    )


def main() -> int:
    print("Проверка входа против ликвидации. Комиссия туда-обратно {:.2f}%.".format(FEE_ROUNDTRIP_PCT))
    print("Buy в ленте Bybit = ликвидировали лонги, вход через паузу = покупка.")
    any_events = False
    for symbol in SYMBOLS:
        try:
            events = fetch_liquidations(symbol)
        except Exception as exc:
            print(f"{symbol}: ленту ликвидаций скачать не удалось ({exc})")
            continue
        big = [item for item in events if item.usd >= MIN_USD]
        print(f"{symbol}: записей {len(events)}, от {MIN_USD:.0f} USDT — {len(big)}")
        if not big:
            continue
        any_events = True
        start = min(item.ts_ms for item in big) - 5 * 60_000
        end = max(item.ts_ms for item in big) + 90 * 60_000
        candles = fetch_klines(symbol, start, end)
        print(f"{symbol}: свечей {len(candles)}")
        series = {symbol: candles}
        for delay in DELAYS:
            for hold in HOLDS:
                trades = simulate_fade(
                    big,
                    series,
                    delay_min=delay,
                    hold_min=hold,
                    min_usd=MIN_USD,
                )
                print("  " + _line(f"пауза {delay} мин, держать {hold} мин", summarize_fade(trades)))
    if not any_events:
        print("Крупных ликвидаций в публичной ленте нет — по прибыли вывод сделать нельзя.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
