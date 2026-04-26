#!/usr/bin/env python3
"""Загрузка свечей для RL: synthetic / JSON file / Bybit (async)."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from core.config import BotConfig


def synthetic_klines(n: int = 2000, seed: int = 42) -> List[Dict[str, Any]]:
    rng = np.random.default_rng(seed)
    c = 100.0 + np.cumsum(rng.normal(0, 0.12, n))
    out: List[Dict[str, Any]] = []
    for i in range(n):
        out.append(
            {
                "timestamp": i * 60_000,
                "open": float(c[i]),
                "high": float(c[i] + abs(rng.normal(0, 0.04))),
                "low": float(c[i] - abs(rng.normal(0, 0.04))),
                "close": float(c[i]),
                "volume": float(rng.random() * 12 + 0.5),
            }
        )
    return out


def load_klines_from_json(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "klines" in data:
        return list(data["klines"])
    raise ValueError(f"Unsupported JSON structure in {path}")


async def fetch_bybit_klines(
    symbol: str,
    interval: str,
    limit: int,
    testnet: bool = False,
    category: str = "linear",
) -> List[Dict[str, Any]]:
    from core.security import SecureStore
    from exchange.bybit_client import BybitClient

    sec = SecureStore()
    key = sec.get_key("BYBIT_API_KEY")
    secret = sec.get_key("BYBIT_API_SECRET")
    client = BybitClient(key or "", secret or "", testnet=testnet, category=category)
    try:
        return await client.get_klines(symbol, interval, limit)
    finally:
        await client.close()


def load_klines_for_training(cfg: BotConfig, config_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Читает ``rl_sb3.klines`` из BotConfig."""
    section = cfg.get("rl_sb3", default={}) or {}
    kl = section.get("klines") or {}
    source = str(kl.get("source", "synthetic")).lower()
    limit = int(kl.get("limit", 2000))
    root = config_path.resolve().parent if config_path else Path.cwd()

    if source == "synthetic":
        return synthetic_klines(n=max(500, limit))

    if source == "file":
        raw_path = kl.get("file_path") or ""
        p = Path(str(raw_path))
        if not p.is_absolute():
            p = root / p
        if not p.exists():
            raise FileNotFoundError(f"rl_sb3.klines.file_path not found: {p}")
        return load_klines_from_json(p)

    if source == "bybit":
        symbol = str(kl.get("symbol", "BTCUSDT"))
        interval = str(kl.get("interval", cfg.get("bot", "candle_interval", default="5")))
        bybit_cfg = cfg.get("bybit", default={}) or {}
        testnet = bool(bybit_cfg.get("testnet", False))
        category = str(bybit_cfg.get("category", "linear"))
        return asyncio.run(
            fetch_bybit_klines(symbol, interval, limit, testnet=testnet, category=category)
        )

    raise ValueError(f"Unknown rl_sb3.klines.source: {source}")
