"""
Ретест как состояние: BOS/пробой → WAIT_RETEST → CONFIRMED (окно свечей, не только 3).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from prd_agent.entry.impulse_retest import check_impulse_retest_confirmation

logger = logging.getLogger("prd_agent.retest_watch")


@dataclass
class RetestWatchEntry:
    symbol: str
    side: str
    phase: str  # WAIT_RETEST | CONFIRMED
    bos_level: float = 0.0
    zone_low: float = 0.0
    zone_high: float = 0.0
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0


def _watch_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ze = cfg.get("zone_entry", {}) if isinstance(cfg.get("zone_entry"), dict) else {}
    rw = ze.get("retest_watchlist", {})
    return rw if isinstance(rw, dict) else {}


def _normalize_side(side: str) -> str:
    s = str(side or "").strip().upper()
    if s in ("LONG", "BUY"):
        return "BUY"
    if s in ("SHORT", "SELL"):
        return "SELL"
    return s


class RetestWatchlist:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._entries: Dict[str, RetestWatchEntry] = {}

    def _key(self, symbol: str, side: str) -> str:
        return f"{symbol.upper()}:{_normalize_side(side)}"

    def enabled(self) -> bool:
        wc = _watch_cfg(self.cfg)
        ze = self.cfg.get("zone_entry", {}) if isinstance(self.cfg.get("zone_entry"), dict) else {}
        return bool(wc.get("enabled", ze.get("retest_watchlist_enabled", True)))

    def register_breakout(
        self,
        symbol: str,
        side: str,
        *,
        bos_level: float = 0.0,
        zone_low: float = 0.0,
        zone_high: float = 0.0,
    ) -> None:
        if not self.enabled():
            return
        wc = _watch_cfg(self.cfg)
        ttl_min = float(wc.get("ttl_minutes", 90))
        key = self._key(symbol, side)
        now = time.time()
        prev = self._entries.get(key)
        if prev and prev.phase == "CONFIRMED":
            return
        entry = RetestWatchEntry(
            symbol=symbol.upper(),
            side=_normalize_side(side),
            phase="WAIT_RETEST",
            bos_level=float(bos_level or 0),
            zone_low=float(zone_low or 0),
            zone_high=float(zone_high or 0),
            created_at=now,
            expires_at=now + ttl_min * 60,
        )
        self._entries[key] = entry
        logger.info(
            "retest_watch: %s %s WAIT_RETEST bos=%.6g zone=[%.6g, %.6g] ttl=%.0fm",
            entry.symbol,
            entry.side,
            entry.bos_level,
            entry.zone_low,
            entry.zone_high,
            ttl_min,
        )

    def prune_expired(self) -> int:
        now = time.time()
        expired = [k for k, e in self._entries.items() if e.expires_at > 0 and now > e.expires_at]
        for k in expired:
            e = self._entries.pop(k, None)
            if e:
                logger.info("retest_watch: %s %s EXPIRED", e.symbol, e.side)
        return len(expired)

    def get_phase(self, symbol: str, side: str) -> Optional[str]:
        e = self._entries.get(self._key(symbol, side))
        return e.phase if e else None

    def _scan_window(self, klines: List[Dict]) -> int:
        wc = _watch_cfg(self.cfg)
        return max(5, int(wc.get("scan_candles", 12)))

    def evaluate(
        self,
        symbol: str,
        side: str,
        klines: List[Dict],
        atr_value: float,
        confidence: float = 0.0,
    ) -> Tuple[bool, str]:
        """
        True = можно входить.
        False + reason = ждём ретест или нет регистрации.
        """
        if not self.enabled():
            return True, ""

        self.prune_expired()
        key = self._key(symbol, side)
        entry = self._entries.get(key)

        if not entry:
            ok, reason = check_impulse_retest_confirmation(
                side=side,
                klines=klines,
                atr_value=atr_value,
                confidence=confidence,
                cfg=self.cfg,
            )
            return ok, reason

        if entry.phase == "CONFIRMED":
            return True, "retest_watch: CONFIRMED"

        window = self._scan_window(klines)
        if len(klines) < 3:
            return False, "retest_watch: WAIT — мало свечей"

        for end in range(len(klines), max(2, len(klines) - window), -1):
            slice_k = klines[max(0, end - window) : end]
            if len(slice_k) < 3:
                continue
            ok, reason = check_impulse_retest_confirmation(
                side=side,
                klines=slice_k,
                atr_value=atr_value,
                confidence=confidence,
                cfg=self.cfg,
            )
            if ok:
                entry.phase = "CONFIRMED"
                logger.info(
                    "retest_watch: %s %s WAIT → CONFIRMED (%s)",
                    entry.symbol,
                    entry.side,
                    reason[:60],
                )
                return True, f"retest_watch: CONFIRMED ({reason})"

        return False, "retest_watch: WAIT — ретест не подтверждён"
