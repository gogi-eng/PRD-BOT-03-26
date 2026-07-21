"""Расчёт номинала ордера для telegram_signal_agent / SPIKE / MARKET SCANNER."""
from __future__ import annotations

from typing import Tuple


def plan_signal_notional(
    *,
    leverage: int,
    margin_usdt: float,
    max_notional_usdt: float,
    max_notional_balance_pct: float,
    wallet_balance: float,
    available_balance: float,
    reserve_pct: float = 18.0,
) -> Tuple[float, str]:
    """Возвращает (notional_usdt, reason).

    Если max_notional_balance_pct > 0 — потолок = pct% от баланса на бирже
    (и это целевой размер, без старого потолка margin×leverage / max_notional_usdt).
    Иначе — классика: min(margin_usdt × leverage, max_notional_usdt).

    В обоих режимах режем по свободной марже с учётом reserve_pct.
    """
    lev = max(1, int(leverage or 1))
    reserve = max(0.0, min(90.0, float(reserve_pct or 0.0))) / 100.0
    avail = max(0.0, float(available_balance or 0.0))
    wallet = max(0.0, float(wallet_balance or 0.0))
    usable = avail * (1.0 - reserve)
    max_from_wallet = usable * float(lev)

    pct = float(max_notional_balance_pct or 0.0)
    if pct > 0:
        pct = max(1.0, min(100.0, pct))
        base = wallet * (pct / 100.0)
        reason = f"balance_pct={pct:g}% wallet={wallet:.4f}"
    else:
        base = min(float(margin_usdt) * float(lev), float(max_notional_usdt))
        reason = f"margin×lev / max_notional fixed"

    notional = max(0.0, float(base))
    if max_from_wallet > 0 and max_from_wallet + 1e-9 < notional:
        notional = max_from_wallet
        reason = f"{reason}; capped_by_available usable={usable:.4f}"
    return notional, reason
