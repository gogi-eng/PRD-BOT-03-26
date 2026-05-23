"""Типы сигналов (без импорта router — избегаем circular import)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict


@dataclass
class UnifiedSignal:
    symbol: str
    side: str
    confidence: float
    source: str
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "confidence": self.confidence,
            "source": self.source,
            "entry": self.entry,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
            "created_at": self.created_at,
        }
