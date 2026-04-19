#!/usr/bin/env python3
"""
Offline pairwise trainer for BPRLinearRanker weights.

Uses trade_history.json (list of trades with symbol, pnl_pct). Aggregates per-symbol
stats, builds random pairs where one symbol outperformed another on average, and
runs a few epochs of pairwise logistic updates (BPR-style).

Usage:
  python scripts/train_bpr_ranker.py [path/to/trade_history.json] [out/bpr_weights.json]
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FEATURE_DIM = 10


def _sigmoid(x: float) -> float:
    if x >= 30:
        return 1.0
    if x <= -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def symbol_feature_from_stats(mean_pnl: float, winrate: float, n: int) -> list[float]:
    """Proxy 10-d vector (not identical to live signal features; ranker still useful)."""
    mp = max(min(mean_pnl / 8.0 + 0.5, 1.0), 0.0)
    wr = max(min(winrate, 1.0), 0.0)
    cn = min(math.log(n + 1.0) / 6.0, 1.0)
    return [mp, wr, cn, 0.15, 0.15, 0.2, 0.5, 0.1, 0.1, 0.5]


def load_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else []


def aggregate(trades: list[dict]) -> dict[str, tuple[float, float, int]]:
    """symbol -> (sum_pnl_pct, wins, count)"""
    sums: dict[str, float] = defaultdict(float)
    wins: dict[str, int] = defaultdict(int)
    cnt: dict[str, int] = defaultdict(int)
    for t in trades:
        sym = str(t.get("symbol", "")).strip()
        if not sym:
            continue
        pnl_pct = float(t.get("pnl_pct", 0.0) or 0.0)
        sums[sym] += pnl_pct
        cnt[sym] += 1
        if pnl_pct > 0:
            wins[sym] += 1
    out: dict[str, tuple[float, float, int]] = {}
    for sym in cnt:
        n = cnt[sym]
        out[sym] = (sums[sym] / max(n, 1), wins[sym] / max(n, 1), n)
    return out


def train(
    stats: dict[str, tuple[float, float, int]],
    epochs: int = 400,
    lr: float = 0.08,
    pairs_per_epoch: int = 256,
) -> tuple[list[float], float]:
    rng = random.Random(42)
    syms = list(stats.keys())
    if len(syms) < 2:
        w = [0.42, 0.12, 0.18, 0.1, 0.12, 0.04, 0.02, 0.0, 0.0, 0.02]
        return w, 0.0

    w = [0.08] * FEATURE_DIM
    b = 0.0

    def vec(sym: str) -> list[float]:
        m, wr, n = stats[sym]
        return symbol_feature_from_stats(m, wr, n)

    for _ in range(epochs):
        for _ in range(pairs_per_epoch):
            a, c = rng.choice(syms), rng.choice(syms)
            if a == c:
                continue
            ma, _, na = stats[a]
            mc, _, nc = stats[c]
            if na < 2 or nc < 2:
                continue
            # Prefer label: higher mean pnl_pct is "better"
            if abs(ma - mc) < 0.05:
                continue
            if ma > mc:
                pos, neg = a, c
            else:
                pos, neg = c, a
            x = [vp - vn for vp, vn in zip(vec(pos), vec(neg))]
            z = b + sum(wi * xi for wi, xi in zip(w, x))
            g = _sigmoid(z) - 1.0  # want sigmoid->1
            for i in range(FEATURE_DIM):
                w[i] -= lr * g * x[i]
            b -= lr * g * 0.5
    return w, b


def main() -> None:
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "trade_history.json"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "bpr_weights.json"
    trades = load_trades(in_path)
    stats = aggregate(trades)
    w, b = train(stats)
    out_path.write_text(
        json.dumps(
            {"weights": w, "bias": b, "feature_dim": FEATURE_DIM, "symbols": len(stats)},
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {out_path} ({len(stats)} symbols from {len(trades)} trades)")


if __name__ == "__main__":
    main()
