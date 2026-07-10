#!/usr/bin/env python3
"""Закрыть все открытые позиции на Bybit (перед остановкой бота в неторговое окно)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prd_agent.config import load_config  # noqa: E402
from prd_agent.exchange.bybit_adapter import BybitAdapter  # noqa: E402


def _position_size(row: Dict[str, Any]) -> float:
    for key in ("size", "qty", "positionQty"):
        val = float(row.get(key, 0) or 0)
        if val > 0:
            return val
    avg = float(row.get("avgPrice", 0) or row.get("entryPrice", 0) or 0)
    pval = float(row.get("positionValue", 0) or 0)
    if pval > 0 and avg > 0:
        return pval / avg
    return 0.0


def _normalize_side(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if text in ("buy", "long"):
        return "Buy"
    if text in ("sell", "short"):
        return "Sell"
    return str(raw or "Buy")


async def close_all_positions(
    cfg_path: Path,
    *,
    reason: str = "trading_hours_stop",
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Возвращает (закрыто, ошибок)."""
    cfg = load_config(cfg_path)
    exchange = BybitAdapter(cfg)
    closed = 0
    failed = 0
    try:
        rows = await exchange.get_positions()
        if not rows:
            print(f"[close_all] нет открытых позиций ({reason})")
            return 0, 0

        print(f"[close_all] найдено {len(rows)} позиций, reason={reason}")
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            qty = _position_size(row)
            if not sym or qty <= 0:
                continue
            side = _normalize_side(row.get("side"))
            idx = int(row.get("positionIdx", 0) or 0)
            label = f"{sym} {side} qty={qty} idx={idx}"
            if dry_run:
                print(f"[close_all] dry-run: {label}")
                closed += 1
                continue

            if not hasattr(exchange, "close_position"):
                print(f"[close_all] error: close_position не поддерживается ({label})")
                failed += 1
                continue

            res = await exchange.close_position(sym, side, qty=qty, position_idx=idx)
            if res.get("success") or res.get("orderId"):
                print(f"[close_all] closed {label} order={res.get('orderId', '')}")
                closed += 1
            else:
                err = str(res.get("error", "unknown"))[:160]
                print(f"[close_all] failed {label}: {err}")
                failed += 1

        if not dry_run and failed == 0:
            await asyncio.sleep(1.0)
            left = await exchange.get_positions()
            if left:
                print(f"[close_all] warn: после закрытия осталось {len(left)} позиций — повтор")
                for row in left:
                    sym = str(row.get("symbol", "")).upper()
                    qty = _position_size(row)
                    if not sym or qty <= 0:
                        continue
                    side = _normalize_side(row.get("side"))
                    idx = int(row.get("positionIdx", 0) or 0)
                    res = await exchange.close_position(sym, side, qty=qty, position_idx=idx)
                    if res.get("success") or res.get("orderId"):
                        closed += 1
                        print(f"[close_all] retry ok {sym}")
                    else:
                        failed += 1
                        print(f"[close_all] retry failed {sym}: {res.get('error', '')}")
    finally:
        await exchange.close()
    return closed, failed


def main() -> int:
    ap = argparse.ArgumentParser(description="Close all open Bybit positions")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--reason", default="trading_hours_stop")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    closed, failed = asyncio.run(
        close_all_positions(args.config, reason=args.reason, dry_run=args.dry_run)
    )
    print(f"[close_all] done closed={closed} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
