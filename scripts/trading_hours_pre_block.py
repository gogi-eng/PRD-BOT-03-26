#!/usr/bin/env python3
"""Перед неторговым окном: закрыть убыточные, прибыльные с трендом оставить."""
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
from prd_agent.positions.block_hours_loser_close import (  # noqa: E402
    PreBlockCloseConfig,
    closes_from_klines,
    position_size,
    should_close_before_block,
    _normalize_side,
)


async def close_losers_before_block(
    cfg_path: Path,
    *,
    reason: str = "trading_hours_pre_block",
    dry_run: bool = False,
) -> Tuple[int, int, int]:
    """Возвращает (закрыто, оставлено, ошибок)."""
    cfg = load_config(cfg_path)
    pre_cfg = PreBlockCloseConfig.from_cfg(cfg)
    if not pre_cfg.enabled:
        print(f"[pre_block] disabled in config ({reason})")
        return 0, 0, 0

    exchange = BybitAdapter(cfg)
    closed = 0
    kept = 0
    failed = 0
    try:
        rows = await exchange.get_positions()
        if not rows:
            print(f"[pre_block] нет открытых позиций ({reason})")
            return 0, 0, 0

        print(f"[pre_block] позиций={len(rows)} reason={reason}")
        for row in rows:
            sym = str(row.get("symbol", "")).upper()
            qty = position_size(row)
            if not sym or qty <= 0:
                continue
            side = _normalize_side(row.get("side"))
            idx = int(row.get("positionIdx", 0) or 0)
            closes: List[float] = []
            try:
                klines = await exchange.get_klines(
                    sym,
                    interval=pre_cfg.trend_kline_interval,
                    limit=max(6, pre_cfg.trend_lookback_bars + 2),
                )
                closes = closes_from_klines(klines or [])
            except Exception as exc:
                print(f"[pre_block] warn klines {sym}: {exc}")

            do_close, why = should_close_before_block(row, cfg=pre_cfg, closes=closes)
            label = f"{sym} {side} qty={qty}"
            if not do_close:
                kept += 1
                print(f"[pre_block] keep {label}: {why}")
                continue

            if dry_run:
                closed += 1
                print(f"[pre_block] dry-run close {label}: {why}")
                continue

            if not hasattr(exchange, "close_position"):
                failed += 1
                print(f"[pre_block] error: close_position unsupported ({label})")
                continue

            res = await exchange.close_position(sym, side, qty=qty, position_idx=idx)
            if res.get("success") or res.get("orderId"):
                closed += 1
                print(f"[pre_block] closed {label}: {why} order={res.get('orderId', '')}")
            else:
                failed += 1
                print(f"[pre_block] failed {label}: {res.get('error', '')}")
    finally:
        await exchange.close()
    return closed, kept, failed


def main() -> int:
    ap = argparse.ArgumentParser(description="Close losing positions before blocked hours")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--reason", default="trading_hours_pre_block")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    closed, kept, failed = asyncio.run(
        close_losers_before_block(args.config, reason=args.reason, dry_run=args.dry_run)
    )
    print(f"[pre_block] done closed={closed} kept={kept} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
