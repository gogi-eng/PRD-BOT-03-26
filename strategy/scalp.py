"""
Session-aware SCALP strategy for pump/dump bursts.

The strategy targets short-lived momentum impulses around configured
"hot hours" in local timezone (UTC+offset).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional


class ScalpSessionStrategy:
    """Detects fast pump/dump opportunities in configured session hours."""

    def __init__(self, config: Optional[Dict] = None, debug: bool = False):
        cfg = config or {}
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.timezone_offset: int = int(cfg.get("timezone_offset", 3))

        # Includes requested hours 03/04/05 UTC+3 + empirically hot hours.
        default_pump_hours = [3, 4, 5, 7, 11, 14, 18]
        default_dump_hours = [3, 4, 5, 13, 19, 20, 21]
        self.pump_hours_local = set(int(h) for h in cfg.get("pump_hours_local", default_pump_hours))
        self.dump_hours_local = set(int(h) for h in cfg.get("dump_hours_local", default_dump_hours))

        self.min_impulse_pct: float = float(cfg.get("min_impulse_pct", 0.45))
        self.min_confirm_move_pct: float = float(cfg.get("min_confirm_move_pct", 0.75))
        self.min_volume_ratio: float = float(cfg.get("min_volume_ratio", 1.6))
        self.confirm_bars: int = int(cfg.get("confirm_bars", 3))
        self.cooldown_bars: int = int(cfg.get("cooldown_bars", 6))
        self._debug = debug
        self._last_signal_idx: Dict[str, int] = {}

    @staticmethod
    def _safe_ratio(num: float, den: float) -> float:
        return num / den if den > 0 else 0.0

    def _local_hour_from_kline(self, kline_time: int) -> int:
        dt_utc = datetime.fromtimestamp(kline_time / 1000, tz=timezone.utc)
        return (dt_utc.hour + self.timezone_offset) % 24

    def _in_cooldown(self, symbol: str, current_idx: int) -> bool:
        last_idx = self._last_signal_idx.get(symbol)
        if last_idx is None:
            return False
        return (current_idx - last_idx) < self.cooldown_bars

    def analyze(self, symbol: str, klines: List[Dict]) -> Optional[Dict]:
        """
        Returns:
            dict with keys: signal, reason, confidence
            or None when no valid impulse is detected.
        """
        if not self.enabled or len(klines) < max(25, self.confirm_bars + 2):
            return None

        current_idx = len(klines) - 1
        if self._in_cooldown(symbol, current_idx):
            return None

        latest = klines[-1]
        local_hour = self._local_hour_from_kline(int(latest.get("time", 0)))

        open_p = float(latest.get("open", 0))
        close_p = float(latest.get("close", 0))
        volume = float(latest.get("volume", 0))
        if open_p <= 0:
            return None

        impulse_pct = (close_p - open_p) / open_p * 100.0

        recent = klines[-(self.confirm_bars + 1):]
        first_close = float(recent[0].get("close", 0))
        last_close = float(recent[-1].get("close", 0))
        confirm_move_pct = 0.0
        if first_close > 0:
            confirm_move_pct = (last_close - first_close) / first_close * 100.0

        recent_volumes = [float(k.get("volume", 0)) for k in klines[-21:-1]]
        avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0.0
        vol_ratio = self._safe_ratio(volume, avg_volume)

        if self._debug and abs(impulse_pct) >= self.min_impulse_pct * 0.7:
            print(
                f"   [SCALP] {symbol} h={local_hour:02d} impulse={impulse_pct:+.2f}% "
                f"confirm={confirm_move_pct:+.2f}% vol={vol_ratio:.2f}x"
            )

        # Pump condition
        if (
            local_hour in self.pump_hours_local
            and impulse_pct >= self.min_impulse_pct
            and confirm_move_pct >= self.min_confirm_move_pct
            and vol_ratio >= self.min_volume_ratio
        ):
            self._last_signal_idx[symbol] = current_idx
            confidence = min(0.95, 0.45 + (impulse_pct / 3.0) + ((vol_ratio - 1.0) / 4.0))
            return {
                "signal": "BUY",
                "confidence": round(confidence, 2),
                "reason": (
                    f"SCALP PUMP: hour={local_hour:02d} UTC+{self.timezone_offset}, "
                    f"impulse={impulse_pct:+.2f}%, confirm={confirm_move_pct:+.2f}%, vol={vol_ratio:.2f}x"
                ),
            }

        # Dump condition
        if (
            local_hour in self.dump_hours_local
            and impulse_pct <= -self.min_impulse_pct
            and confirm_move_pct <= -self.min_confirm_move_pct
            and vol_ratio >= self.min_volume_ratio
        ):
            self._last_signal_idx[symbol] = current_idx
            confidence = min(0.95, 0.45 + (abs(impulse_pct) / 3.0) + ((vol_ratio - 1.0) / 4.0))
            return {
                "signal": "SELL",
                "confidence": round(confidence, 2),
                "reason": (
                    f"SCALP DUMP: hour={local_hour:02d} UTC+{self.timezone_offset}, "
                    f"impulse={impulse_pct:+.2f}%, confirm={confirm_move_pct:+.2f}%, vol={vol_ratio:.2f}x"
                ),
            }

        return None
