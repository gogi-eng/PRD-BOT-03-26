"""HTML-таблица статуса для Telegram."""
from __future__ import annotations

from typing import Any, Dict, List


def format_status_table(
    *,
    balance: float,
    available: float,
    positions: List[Dict],
    watch_symbols: List[str],
    risk_snapshot: Dict[str, Any],
    block_reason: str = "",
    mode: str = "LIVE",
    trailing_enabled: bool | None = None,
) -> str:
    open_map: Dict[str, Dict] = {}
    for p in positions:
        sym = str(p.get("symbol", "")).upper()
        if sym:
            open_map[sym] = p

    rows: List[str] = [
        "Символ   | Позиция        | uPnL",
        "---------|----------------|--------",
    ]
    for sym in watch_symbols:
        p = open_map.get(sym.upper())
        if p:
            side = p.get("side", "?")
            size = p.get("size", 0)
            upnl = float(p.get("unrealisedPnl", 0) or 0)
            rows.append(f"{sym:8} | {side} {size!s:<10} | {upnl:+.2f}")
        else:
            rows.append(f"{sym:8} | —              | —")

    table = "\n".join(rows)
    lines = [
        "<b>📊 PRD Unified — панель</b>",
        f"Режим: <code>{mode}</code>",
        f"<pre>{table}</pre>",
        f"Баланс: <b>{balance:.2f}</b> USDT | Свободно: <b>{available:.2f}</b>",
        f"Риск: <code>{risk_snapshot.get('status', '?')}</code> | "
        f"PnL сегодня (UTC): {risk_snapshot.get('pnl_today_usdt', 0):+.2f} USDT",
    ]
    max_pos = int(risk_snapshot.get("max_positions", 0) or 0)
    open_pos = int(risk_snapshot.get("open_positions", len(positions)) or 0)
    if max_pos > 0:
        lines.append(f"Позиции: <code>{open_pos}/{max_pos}</code>")
    if trailing_enabled is not None:
        lines.append(
            f"Трейлинг SL: <code>{'ВКЛ' if trailing_enabled else 'ВЫКЛ'}</code>"
        )
    reset_min = int(risk_snapshot.get("reset_utc_in_min", 0) or 0)
    if reset_min > 0 and (block_reason or risk_snapshot.get("blocked")):
        rh, rm = divmod(reset_min, 60)
        lines.append(f"<i>Сброс дневного счётчика: 00:00 UTC (~{rh}ч {rm}м)</i>")
    if block_reason:
        lines.append(f"⚠️ <b>{block_reason}</b>")
    elif risk_snapshot.get("blocked"):
        lines.append(f"⚠️ {risk_snapshot.get('block_reason', 'торговля заблокирована')}")
    lines.append(f"<i>Символов в скане: {len(watch_symbols)}</i>")
    return "\n".join(lines)
