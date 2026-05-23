"""
Агент: волатильные пары + теханализ → UnifiedSignal для оркестратора.
"""
from __future__ import annotations

from typing import Any, Dict, List

from prd_agent.analysis.volatility_ta import VolatilityTAEngine
from prd_agent.signals.router import UnifiedSignal


class TAVolatilityAgent:
    source = "ta_volatility"

    def __init__(self, cfg: Dict[str, Any]):
        self.engine = VolatilityTAEngine(cfg)
        self._last_scan: List[str] = []

    @property
    def last_volatile_symbols(self) -> List[str]:
        return list(self._last_scan)

    async def collect(self, exchange) -> List[UnifiedSignal]:
        results, volatile = await self.engine.collect_signals(exchange)
        self._last_scan = [v.symbol for v in volatile]
        out: List[UnifiedSignal] = []
        for r in results:
            out.append(
                UnifiedSignal(
                    symbol=r.symbol,
                    side=r.side,
                    confidence=r.confidence,
                    source=self.source,
                    entry=r.entry,
                    stop_loss=r.stop_loss,
                    take_profit=r.take_profit,
                    reason=r.reason,
                    raw={
                        "change_24h_pct": r.change_24h_pct,
                        "indicators": r.indicators,
                    },
                )
            )
        return out
