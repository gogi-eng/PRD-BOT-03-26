"""Установка плеча на Bybit с проверкой фактического значения."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LeverageApplyResult:
    requested: int
    target: int
    applied: int
    max_instrument: int
    ok: bool
    error: str = ""

    @property
    def mismatch(self) -> bool:
        return self.applied > 0 and self.applied != self.requested


async def apply_trade_leverage(client: Any, symbol: str, requested: int) -> LeverageApplyResult:
    """Запросить плечо и прочитать реальное с биржи (не доверять ответу API слепо)."""
    req = max(1, int(requested))
    sym = symbol.upper()

    if hasattr(client, "set_leverage_verified"):
        raw = await client.set_leverage_verified(sym, req)
        if isinstance(raw, dict):
            applied = int(raw.get("applied", 0) or 0)
            return LeverageApplyResult(
                requested=int(raw.get("requested", req)),
                target=int(raw.get("target", req)),
                applied=applied,
                max_instrument=int(raw.get("max_instrument", 100)),
                ok=bool(raw.get("success")) and applied > 0,
                error=str(raw.get("error", "") or ""),
            )

    max_inst = 100
    if hasattr(client, "get_max_leverage"):
        try:
            max_inst = max(1, int(await client.get_max_leverage(sym)))
        except Exception:
            max_inst = 100

    target = min(req, max_inst)
    applied = 0
    err = ""

    if hasattr(client, "set_leverage"):
        raw = await client.set_leverage(sym, target)
        if isinstance(raw, dict):
            applied = int(raw.get("applied", 0) or 0)
            err = str(raw.get("error", "") or "")
            ok = bool(raw.get("success"))
            if applied <= 0 and hasattr(client, "get_symbol_leverage"):
                applied = int(await client.get_symbol_leverage(sym) or 0)
            return LeverageApplyResult(
                requested=req,
                target=target,
                applied=applied or target,
                max_instrument=max_inst,
                ok=ok and applied > 0,
                error=err,
            )
        elif raw:
            applied = target
            if hasattr(client, "get_symbol_leverage"):
                read_back = int(await client.get_symbol_leverage(sym) or 0)
                if read_back > 0:
                    applied = read_back
            return LeverageApplyResult(
                requested=req,
                target=target,
                applied=applied,
                max_instrument=max_inst,
                ok=True,
                error="",
            )
        err = err or "set_leverage вернул False"

    return LeverageApplyResult(
        requested=req,
        target=target,
        applied=applied,
        max_instrument=max_inst,
        ok=False,
        error=err or "set_leverage недоступен",
    )
