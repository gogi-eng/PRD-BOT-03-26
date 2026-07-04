"""
Публичный Bybit API: какой % снимков стакана проходит пороги orderflow 1.18 / 1.20 / 1.24.
Аналог идеи hftbacktest OBI без установки Rust.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
THRESHOLDS = [1.18, 1.20, 1.24]
URL = "https://api.bybit.com/v5/market/orderbook?category=linear&symbol={sym}&limit=50"


def snapshot(sym: str) -> dict:
    with urllib.request.urlopen(URL.format(sym=sym), timeout=15) as resp:
        data = json.loads(resp.read().decode())
    book = data["result"]
    bids = book.get("b") or []
    asks = book.get("a") or []
    bid_vol = sum(float(x[1]) for x in bids[:25])
    ask_vol = sum(float(x[1]) for x in asks[:25])
    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread_pct = ((best_ask - best_bid) / mid * 100) if mid else 0.0
    imb = (bid_vol - ask_vol) / max(bid_vol + ask_vol, 1e-9)
    ratio = (bid_vol / ask_vol) if ask_vol > 0 else 999.0
    return {
        "symbol": sym,
        "bid_vol": bid_vol,
        "ask_vol": ask_vol,
        "imbalance": imb,
        "ratio": ratio,
        "spread_pct": spread_pct,
    }


def pass_long(s: dict, thr: float) -> bool:
    norm = (thr - 1.0) / (thr + 1.0)
    return s["imbalance"] >= norm


def pass_short(s: dict, thr: float) -> bool:
    norm = (thr - 1.0) / (thr + 1.0)
    return s["imbalance"] <= -norm


def main() -> None:
    snaps = [snapshot(s) for s in SYMBOLS]
    report = {"snapshots": snaps, "thresholds": {}}
    for thr in THRESHOLDS:
        long_ok = sum(1 for s in snaps if pass_long(s, thr))
        short_ok = sum(1 for s in snaps if pass_short(s, thr))
        either = sum(1 for s in snaps if pass_long(s, thr) or pass_short(s, thr))
        report["thresholds"][str(thr)] = {
            "long_pass": long_ok,
            "short_pass": short_ok,
            "either_direction": either,
            "of_n": len(snaps),
        }
    out = Path(__file__).resolve().parent / "orderflow_snapshot.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
