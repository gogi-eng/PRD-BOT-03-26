#!/usr/bin/env python3
"""
Проверка: видит ли API позиции Copy Trading Classic на основном аккаунте.

Запуск на сервере (из корня PRD-BOT-ALL):
  ./venv/bin/python3 scripts/mirror_copy_probe.py

Переменные (.env или export):
  BYBIT_API_KEY / BYBIT_API_SECRET — ключ основного аккаунта (461368408)
  BYBIT_MIRROR_TARGET_KEY / BYBIT_MIRROR_TARGET_SECRET — опционально, субаккаунт 536308614

Если «Позиций linear: 0», но в приложении Bybit Copy Trading есть открытые сделки —
нужен отдельный API-ключ, созданный в разделе Copy Trading (см. вывод скрипта).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exchange.bybit_client import BybitClient

MAIN_UID = os.environ.get("BYBIT_MAIN_UID", "461368408")
SUB_UID = os.environ.get("BYBIT_SUB_UID", "536308614")

# Типы кошельков Bybit v5 (пробуем все — что ответит без ошибки)
WALLET_ACCOUNT_TYPES = (
    "UNIFIED",
    "CONTRACT",
    "FUND",
    "SPOT",
    "OPTION",
    "INVESTMENT",
    "COPY_TRADING",
    "COPYTRADING",
)


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


async def _wallet_usdt(client: BybitClient, account_type: str) -> Optional[Dict[str, float]]:
    result = await client._request(
        "GET",
        "/v5/account/wallet-balance",
        {"accountType": account_type},
        private=True,
    )
    if not result or result.get("_error"):
        return None
    lst = result.get("list") or []
    if not lst:
        return None
    acc = lst[0]
    out: Dict[str, float] = {
        "total_equity": float(acc.get("totalEquity", 0) or 0),
        "total_wallet": float(acc.get("totalWalletBalance", 0) or 0),
        "usdt_wallet": 0.0,
    }
    for coin in acc.get("coin", []):
        if str(coin.get("coin", "")).upper() == "USDT":
            out["usdt_wallet"] = float(coin.get("walletBalance", 0) or 0)
            break
    return out


def _fmt_positions(rows: List[Dict]) -> None:
    if not rows:
        print("    (нет открытых позиций)")
        return
    for p in rows:
        print(
            f"    {p.get('symbol')} {p.get('side')} size={p.get('size')} "
            f"entry={p.get('avgPrice')} SL={p.get('stopLoss')} TP={p.get('takeProfit')}"
        )


async def probe_client(label: str, key: str, secret: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    if not key or not secret:
        print("  Пропуск: нет API key/secret")
        return

    client = BybitClient(key, secret)
    try:
        pos = await client.get_positions()
        print(f"\n  GET /v5/position/list (linear, USDT): {len(pos)} поз.")
        _fmt_positions(pos)

        print("\n  Балансы по типам кошелька:")
        for at in WALLET_ACCOUNT_TYPES:
            snap = await _wallet_usdt(client, at)
            if snap is None:
                continue
            if snap["total_equity"] > 0 or snap["usdt_wallet"] > 0:
                print(
                    f"    {at}: equity={snap['total_equity']:.4f} "
                    f"USDT wallet={snap['usdt_wallet']:.4f}"
                )

        # Инструменты с copyTrading=1 (публично, без ключа)
        pub = BybitClient("", "")
        try:
            info = await pub._request(
                "GET",
                "/v5/market/instruments-info",
                {"category": "linear", "symbol": "BTCUSDT"},
                private=False,
            )
            ct = "?"
            if info and info.get("list"):
                ct = info["list"][0].get("copyTrading", "?")
            print(f"\n  BTCUSDT copyTrading (справка): {ct}")
        finally:
            await pub.close()
    finally:
        await client.close()


async def main() -> None:
    _load_dotenv()
    uta_key = os.environ.get("BYBIT_API_KEY", "")
    uta_sec = os.environ.get("BYBIT_API_SECRET", "")
    copy_key = os.environ.get("BYBIT_MIRROR_SOURCE_KEY", "")
    copy_sec = os.environ.get("BYBIT_MIRROR_SOURCE_SECRET", "")
    sub_key = os.environ.get("BYBIT_MIRROR_TARGET_KEY", "") or os.environ.get(
        "BYBIT_SUB_API_KEY", ""
    )
    sub_sec = os.environ.get("BYBIT_MIRROR_TARGET_SECRET", "") or os.environ.get(
        "BYBIT_SUB_API_SECRET", ""
    )

    print("PRD-BOT — проверка Copy Trading / зеркало")
    print(f"  Основной UID (копитрейд): {MAIN_UID}")
    print(f"  Субаккаунт UID (PRD-TELEGRAM-AGENT): {SUB_UID}")

    await probe_client(
        "Unified / обычный API (BYBIT_API_KEY) — часто 0 поз. при копитрейде",
        uta_key,
        uta_sec,
    )
    await probe_client(
        "Copy Trading API (BYBIT_MIRROR_SOURCE_KEY) — должен видеть копи-позиции",
        copy_key,
        copy_sec,
    )
    await probe_client(
        "Субаккаунт 536308614 (BYBIT_MIRROR_TARGET_KEY или BYBIT_SUB_API_KEY)",
        sub_key,
        sub_sec,
    )

    print("\n" + "=" * 60)
    print("  КАК ИНТЕРПРЕТИРОВАТЬ")
    print("=" * 60)
    print(
        """
  • Позиций 0 на основном ключе, но в приложении Copy Trading → My Trades
    есть сделки — ваш ключ смотрит Unified/обычный кошелёк, НЕ Copy Trading Account.

  • Решение для зеркала (источник = копитрейд):
    1) Bybit → Tools → Copy Trading → API Management (или API в настройках Copy Trading)
    2) Создать ключ с правами Contract: Order + Position (БЕЗ вывода)
    3) Добавить в .env на сервере:
         BYBIT_MIRROR_SOURCE_KEY=...
         BYBIT_MIRROR_SOURCE_SECRET=...
    4) Снова: ./venv/bin/python3 scripts/mirror_copy_probe.py
       (смотрите блок «Copy Trading API»)

  • Цель зеркала — субаккаунт UID 536308614:
    Отдельный API-ключ на субаккаунте → BYBIT_MIRROR_TARGET_KEY в .env

  • UID 461368408 — только для переводов USDT (Universal Transfer), если нужно
    пополнить субаккаунт; позиции копитрейда читаются ключом Copy Trading API.
"""
    )


if __name__ == "__main__":
    asyncio.run(main())
