"""Порог уверенности и антиспам повторов по symbol+side."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple


def normalize_confidence(conf: float) -> float:
    """0..1; значения >1 трактуются как проценты 0..100."""
    c = float(conf or 0)
    if c > 1.0:
        return min(1.0, c / 100.0)
    return max(0.0, c)


def meets_threshold(conf: float, threshold: float) -> bool:
    return normalize_confidence(conf) >= float(threshold)


def load_min_analysis_confidence(cfg: Dict[str, Any]) -> float:
    sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    rep = cfg.get("reporter", {}) if isinstance(cfg.get("reporter"), dict) else {}
    return float(
        sig.get(
            "min_analysis_confidence",
            rep.get("high_confidence_threshold", 0.80),
        )
    )


def filter_signal_dicts(signals: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    return [
        s
        for s in signals
        if meets_threshold(float(s.get("confidence", 0) or 0), threshold)
    ]


def load_signal_notify_cooldown_sec(cfg: Dict[str, Any]) -> float:
    sig = cfg.get("signals", {}) if isinstance(cfg.get("signals"), dict) else {}
    return float(sig.get("signal_notify_cooldown_sec", 900))


class PerSymbolSignalCooldown:
    """Не чаще одного раза на пару symbol+side за cooldown_sec (Telegram и цикл)."""

    def __init__(self, cooldown_sec: float = 900.0):
        self.cooldown_sec = max(60.0, float(cooldown_sec))
        self._last_at: Dict[Tuple[str, str], float] = {}

    def is_on_cooldown(self, symbol: str, side: str) -> bool:
        key = (symbol.upper(), side.strip())
        last = self._last_at.get(key, 0.0)
        return (time.time() - last) < self.cooldown_sec

    def mark_handled(self, symbol: str, side: str) -> None:
        self._last_at[(symbol.upper(), side.strip())] = time.time()

    def remaining_sec(self, symbol: str, side: str) -> int:
        key = (symbol.upper(), side.strip())
        last = self._last_at.get(key, 0.0)
        left = self.cooldown_sec - (time.time() - last)
        return max(0, int(left))
