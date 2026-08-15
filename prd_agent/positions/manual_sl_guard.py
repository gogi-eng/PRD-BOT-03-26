"""
Защитный SL для ручных позиций без стопа на бирже.

Отличие от откатанного manage_sl_tp_manual:false:
- тот патч ОТКЛЮЧАЛ trailing/BE+ для manual и запрещал перезапись SL/TP;
- этот модуль только ОДИН РАЗ ставит защитный SL, если на Bybit его нет,
  а trailing / BE+ / steward продолжают работать как обычно.

Не трогает уже выставленный пользователем/биржей SL.
Не отменяет желание trailing/BE+ на manual.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Set

logger = logging.getLogger("prd_agent.positions.manual_sl_guard")

LOG_MARKER = "Manual SL guard"
MISSING_MARKER = "Manual SL missing"


@dataclass
class ManualSlGuardConfig:
    enabled: bool = False
    default_sl_pct: float = 1.0
    interval_sec: float = 30.0
    # один раз на символ за жизнь позиции (пока size>0); после закрытия ключ чистится
    once_per_position: bool = True

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> "ManualSlGuardConfig":
        positions = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
        raw = (
            positions.get("manual_sl_guard")
            if isinstance(positions.get("manual_sl_guard"), dict)
            else {}
        )
        return cls(
            enabled=bool(raw.get("enabled", False)),
            default_sl_pct=max(0.05, float(raw.get("default_sl_pct", 1.0) or 1.0)),
            interval_sec=max(5.0, float(raw.get("interval_sec", 30) or 30)),
            once_per_position=bool(raw.get("once_per_position", True)),
        )


def _f(val: Any) -> float:
    try:
        return float(val or 0)
    except (TypeError, ValueError):
        return 0.0


def compute_protective_sl(*, side: str, entry: float, default_sl_pct: float) -> float:
    e = _f(entry)
    if e <= 0:
        return 0.0
    side_u = str(side or "").strip().lower()
    is_buy = side_u in ("buy", "long")
    pct = max(0.05, float(default_sl_pct))
    if is_buy:
        return e * (1.0 - pct / 100.0)
    return e * (1.0 + pct / 100.0)


def exchange_has_sl(row: Mapping[str, Any]) -> bool:
    return _f(row.get("stopLoss") or row.get("stop_loss")) > 0


class ManualSlGuard:
    """Ставит защитный SL на manual без стопа; не мешает trailing/BE+."""

    def __init__(self) -> None:
        self.cfg = ManualSlGuardConfig()
        self._last_attempt: Dict[str, float] = {}
        self._done_keys: Set[str] = set()

    def apply_config(self, cfg: ManualSlGuardConfig) -> None:
        self.cfg = cfg

    def forget_symbol(self, symbol: str) -> None:
        sym = str(symbol or "").upper()
        self._done_keys = {k for k in self._done_keys if not k.startswith(f"{sym}|")}
        self._last_attempt.pop(sym, None)

    def _due(self, symbol: str, now: float) -> bool:
        last = self._last_attempt.get(symbol, 0.0)
        return (now - last) >= float(self.cfg.interval_sec)

    async def ensure(
        self,
        exchange: Any,
        positions: List[Mapping[str, Any]],
        *,
        bot_symbols: Optional[Set[str]] = None,
        origin_of: Optional[Any] = None,
    ) -> List[str]:
        notes: List[str] = []
        cfg = self.cfg
        if not cfg.enabled or not positions:
            return notes

        bot_symbols = bot_symbols or set()
        client = getattr(exchange, "_client", None) or exchange
        now = time.time()
        live_keys: Set[str] = set()

        for row in positions:
            sym = str(row.get("symbol", "") or "").upper()
            size = _f(row.get("size") or row.get("qty"))
            if not sym or size <= 0:
                continue
            side_raw = str(row.get("side", "") or "")
            side = "Buy" if side_raw.lower() in ("buy", "long") else "Sell"
            key = f"{sym}|{side}"
            live_keys.add(key)

            origin = "bot" if sym in bot_symbols else "manual"
            if callable(origin_of):
                try:
                    origin = str(origin_of(sym) or origin)
                except Exception:
                    pass
            if str(origin).lower() != "manual":
                continue
            if exchange_has_sl(row):
                # стоп уже есть — помечаем done, чтобы не дёргать снова
                self._done_keys.add(key)
                continue
            if cfg.once_per_position and key in self._done_keys:
                continue
            if not self._due(sym, now):
                continue
            self._last_attempt[sym] = now

            entry = _f(row.get("avgPrice") or row.get("entryPrice") or row.get("markPrice"))
            target_sl = compute_protective_sl(
                side=side, entry=entry, default_sl_pct=cfg.default_sl_pct
            )
            if target_sl <= 0:
                continue

            logger.warning(
                "%s: %s %s entry=%.6f → protective SL=%.6f (pct=%.3f)",
                MISSING_MARKER,
                sym,
                side,
                entry,
                target_sl,
                cfg.default_sl_pct,
            )

            if not hasattr(client, "update_stop_loss"):
                logger.error("%s: client has no update_stop_loss", LOG_MARKER)
                continue

            position_idx = int(row.get("positionIdx", 0) or 0)
            try:
                res = await client.update_stop_loss(
                    sym, target_sl, position_idx=position_idx
                )
            except Exception as exc:
                logger.error("%s: failed %s: %s", LOG_MARKER, sym, exc)
                notes.append(f"⚠️ {LOG_MARKER}: не удалось поставить SL на {sym}: {exc}")
                continue

            ok = isinstance(res, dict) and res.get("success")
            if ok:
                self._done_keys.add(key)
                msg = f"🛡 {LOG_MARKER}: поставлен защитный SL={target_sl:.6g} на {sym}"
                notes.append(msg)
                logger.info(
                    "%s: set SL=%.6g on %s %s (manual, trailing/BE+ remain ON)",
                    LOG_MARKER,
                    target_sl,
                    sym,
                    side,
                )
            else:
                err = res.get("error") if isinstance(res, dict) else "update_stop_loss failed"
                logger.error("%s: failed %s: %s", LOG_MARKER, sym, err)
                notes.append(f"⚠️ {LOG_MARKER}: не удалось поставить SL на {sym}: {err}")

        # очистка done для закрытых
        self._done_keys = {k for k in self._done_keys if k in live_keys}
        return notes
