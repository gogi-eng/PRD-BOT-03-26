#!/usr/bin/env python3
"""
Append a compact Bybit USDT-linear tickers snapshot (public API) for later monthly stats.

No API keys required. Default output: reports/bybit_tickers_snapshots.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _fetch(category: str) -> dict:
    url = f"https://api.bybit.com/v5/market/tickers?category={category}"
    req = urllib.request.Request(url, headers={"User-Agent": "PRD-SCALP-snapshot/1.0"})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot Bybit tickers to JSONL (gainers/losers summary).")
    ap.add_argument(
        "--out",
        default="reports/bybit_tickers_snapshots.jsonl",
        help="Output JSONL path (appends one line per run).",
    )
    ap.add_argument("--category", default="linear", help="Bybit category (default: linear).")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = _fetch(args.category)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
        return 1

    lst = (data.get("result") or {}).get("list") or []
    parsed: list[tuple[float, str, float, float]] = []
    for x in lst:
        sym = str(x.get("symbol") or "")
        try:
            p = float(x.get("price24hPcnt") or 0)
        except (TypeError, ValueError):
            p = 0.0
        try:
            fr = float(x.get("fundingRate") or 0)
        except (TypeError, ValueError):
            fr = 0.0
        try:
            vol = float(x.get("turnover24h") or 0)
        except (TypeError, ValueError):
            vol = 0.0
        parsed.append((p, sym, fr, vol))

    parsed.sort(key=lambda t: t[0])
    losers = parsed[:50]
    gainers = parsed[-50:][::-1]

    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "category": args.category,
        "retCode": data.get("retCode"),
        "symbol_count": len(parsed),
        "losers_50": [{"s": s, "p24": p, "fr": fr, "turnover24h": v} for p, s, fr, v in losers],
        "gainers_50": [{"s": s, "p24": p, "fr": fr, "turnover24h": v} for p, s, fr, v in gainers],
    }

    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"OK appended -> {out_path.resolve()}  symbols={len(parsed)}  ts={row['ts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
