#!/usr/bin/env python3
"""Export Bybit closed trades for the last N hours (default: 24)."""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
try:
    from dotenv import load_dotenv
except ImportError:  # Optional dependency for convenience only.
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


UTC = timezone.utc


@dataclass
class TradeSummary:
    total: int
    wins: int
    losses: int
    win_rate_pct: float
    total_pnl: float
    avg_pnl: float


class BybitClosedPnlExporter:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, recv_window: int = 20000):
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        self.base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

    def _sign(self, timestamp_ms: str, query: str) -> str:
        payload = f"{timestamp_ms}{self.api_key}{self.recv_window}{query}"
        return hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    async def fetch_closed_pnl(
        self,
        *,
        category: str,
        start_ms: int,
        end_ms: int,
        symbol: Optional[str] = None,
        limit: int = 100,
        max_pages: int = 30,
    ) -> List[Dict[str, Any]]:
        cursor = ""
        records: List[Dict[str, Any]] = []
        timeout = aiohttp.ClientTimeout(total=25)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for _ in range(max_pages):
                params: Dict[str, Any] = {
                    "category": category,
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": limit,
                }
                if symbol:
                    params["symbol"] = symbol.upper()
                if cursor:
                    params["cursor"] = cursor

                query = urlencode(params)
                ts = str(int(time.time() * 1000))
                headers = {
                    "X-BAPI-API-KEY": self.api_key,
                    "X-BAPI-SIGN": self._sign(ts, query),
                    "X-BAPI-SIGN-TYPE": "2",
                    "X-BAPI-TIMESTAMP": ts,
                    "X-BAPI-RECV-WINDOW": str(self.recv_window),
                }
                url = f"{self.base_url}/v5/position/closed-pnl?{query}"

                async with session.get(url, headers=headers) as response:
                    raw_text = await response.text()
                    if response.status != 200:
                        raise RuntimeError(f"HTTP {response.status}: {raw_text[:300]}")
                    data = json.loads(raw_text)

                if data.get("retCode") != 0:
                    raise RuntimeError(f"Bybit API error {data.get('retCode')}: {data.get('retMsg')}")

                result = data.get("result", {}) or {}
                batch = result.get("list", []) or []
                records.extend(batch)

                cursor = result.get("nextPageCursor") or ""
                if not cursor or not batch:
                    break

                # Keep polite pacing to reduce rate-limit probability.
                await asyncio.sleep(0.15)

        return records


def pick_close_ts_ms(row: Dict[str, Any]) -> int:
    for key in ("updatedTime", "createdTime", "execTime", "fillTime", "closeTime"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            continue
    return 0


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_trade(row: Dict[str, Any]) -> Dict[str, Any]:
    close_ts = pick_close_ts_ms(row)
    close_dt = datetime.fromtimestamp(close_ts / 1000, tz=UTC).isoformat() if close_ts else ""
    side = str(row.get("side", "")).upper()
    symbol = str(row.get("symbol", "")).upper()

    pnl = to_float(row.get("closedPnl"))
    qty = to_float(row.get("closedSize", row.get("qty", 0)))
    entry_price = to_float(row.get("avgEntryPrice", row.get("openAvgPrice", 0)))
    exit_price = to_float(row.get("avgExitPrice", row.get("closeAvgPrice", 0)))
    leverage = to_float(row.get("leverage", 0))

    return {
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "closed_pnl": pnl,
        "leverage": leverage,
        "close_time_ms": close_ts,
        "close_time_utc": close_dt,
        "order_id": row.get("orderId", ""),
        "exec_type": row.get("execType", ""),
    }


def summarize(trades: List[Dict[str, Any]]) -> TradeSummary:
    total = len(trades)
    wins = sum(1 for t in trades if t["closed_pnl"] > 0)
    losses = sum(1 for t in trades if t["closed_pnl"] < 0)
    total_pnl = round(sum(t["closed_pnl"] for t in trades), 8)
    avg_pnl = round(total_pnl / total, 8) if total else 0.0
    win_rate_pct = round((wins / total) * 100, 2) if total else 0.0
    return TradeSummary(
        total=total,
        wins=wins,
        losses=losses,
        win_rate_pct=win_rate_pct,
        total_pnl=total_pnl,
        avg_pnl=avg_pnl,
    )


def write_csv(path: Path, trades: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "symbol",
        "side",
        "qty",
        "entry_price",
        "exit_price",
        "closed_pnl",
        "leverage",
        "close_time_ms",
        "close_time_utc",
        "order_id",
        "exec_type",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(trades)


async def run(args: argparse.Namespace) -> int:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=script_dir / ".env", override=False)

    api_key = os.getenv("BYBIT_API_KEY", "").strip()
    api_secret = os.getenv("BYBIT_API_SECRET", "").strip()
    if not api_key or not api_secret:
        print("ERROR: BYBIT_API_KEY / BYBIT_API_SECRET not found in environment or bot/.env")
        return 2

    end_dt = datetime.now(tz=UTC)
    start_dt = end_dt - timedelta(hours=args.hours)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    exporter = BybitClosedPnlExporter(api_key=api_key, api_secret=api_secret, testnet=args.testnet)
    raw_rows = await exporter.fetch_closed_pnl(
        category=args.category,
        start_ms=start_ms,
        end_ms=end_ms,
        symbol=args.symbol,
        limit=args.limit,
        max_pages=args.max_pages,
    )

    normalized = [normalize_trade(row) for row in raw_rows]
    filtered = [t for t in normalized if start_ms <= t["close_time_ms"] <= end_ms]
    filtered.sort(key=lambda x: x["close_time_ms"])

    summary = summarize(filtered)

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    base = f"trade_history_last_{args.hours}h_{stamp}"
    json_path = out_dir / f"{base}.json"
    csv_path = out_dir / f"{base}.csv"

    payload = {
        "generated_at_utc": datetime.now(tz=UTC).isoformat(),
        "window_start_utc": start_dt.isoformat(),
        "window_end_utc": end_dt.isoformat(),
        "category": args.category,
        "symbol_filter": args.symbol.upper() if args.symbol else None,
        "summary": {
            "total": summary.total,
            "wins": summary.wins,
            "losses": summary.losses,
            "win_rate_pct": summary.win_rate_pct,
            "total_pnl": summary.total_pnl,
            "avg_pnl": summary.avg_pnl,
        },
        "trades": filtered,
    }

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    write_csv(csv_path, filtered)

    print(f"Window UTC: {start_dt.isoformat()} -> {end_dt.isoformat()}")
    print(
        f"Trades={summary.total} | Wins={summary.wins} | Losses={summary.losses} | "
        f"WinRate={summary.win_rate_pct:.2f}% | TotalPnL={summary.total_pnl:.4f}"
    )
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Bybit closed trade history for the last N hours.")
    parser.add_argument("--hours", type=int, default=24, help="Lookback window in hours (default: 24).")
    parser.add_argument("--symbol", type=str, default="", help="Optional symbol filter, e.g. BTCUSDT.")
    parser.add_argument("--category", type=str, default="linear", help="Bybit category (default: linear).")
    parser.add_argument("--testnet", action="store_true", help="Use Bybit testnet endpoint.")
    parser.add_argument("--limit", type=int, default=100, help="Page size for API requests (default: 100).")
    parser.add_argument("--max-pages", type=int, default=30, help="Max pagination pages to fetch (default: 30).")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(Path(__file__).resolve().parent / "reports"),
        help="Directory to save output JSON/CSV files.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.hours <= 0:
        print("ERROR: --hours must be > 0")
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
