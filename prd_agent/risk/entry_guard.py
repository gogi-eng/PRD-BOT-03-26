"""
Проверки входа до расчёта плеча и размера: price drift, market vs limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from prd_agent.signals.pump_dump_mode import entry_drift_limits
from prd_agent.signals.types import UnifiedSignal


@dataclass(frozen=True)
class EntryExecutionPlan:
    allowed: bool
    reason: str
    order_type: str = "Market"
    limit_price: float = 0.0
    market_price: float = 0.0
    drift_pct: float = 0.0


class EntryGuard:
    def __init__(self, cfg: Dict[str, Any]):
        eg = cfg.get("entry_guard", {})
        if not isinstance(eg, dict):
            eg = {}
        self.enabled = bool(eg.get("enabled", True))
        self.max_market_drift_pct = float(eg.get("max_market_drift_pct", 0.004))
        self.max_limit_drift_pct = float(eg.get("max_limit_drift_pct", 0.008))
        self.max_skip_drift_pct = float(eg.get("max_skip_drift_pct", 0.012))
        self.telegram_limit_entry = bool(eg.get("telegram_limit_entry", True))
        tg_src = eg.get("telegram_sources", ["telegram", "tg_inbox", "tg"])
        self.telegram_sources = tuple(str(s).lower() for s in tg_src if s)

    @staticmethod
    def _drift_pct(plan_entry: float, market_price: float) -> float:
        if plan_entry <= 0 or market_price <= 0:
            return 0.0
        return abs(market_price - plan_entry) / plan_entry

    def _is_telegram_source(self, source: str) -> bool:
        src = (source or "").lower()
        return any(tag in src for tag in self.telegram_sources) or src.startswith("tg")

    def _has_signal_levels(self, sig: UnifiedSignal) -> bool:
        return (
            float(sig.entry or 0) > 0
            and float(sig.stop_loss or 0) > 0
            and float(sig.take_profit or 0) > 0
        )

    def plan_execution(
        self,
        sig: UnifiedSignal,
        *,
        plan_entry: float,
        market_price: float,
    ) -> EntryExecutionPlan:
        if not self.enabled:
            return EntryExecutionPlan(
                allowed=True,
                reason="entry_guard_disabled",
                order_type="Market",
                market_price=market_price,
            )

        drift = self._drift_pct(plan_entry, market_price)
        if drift <= self.max_market_drift_pct:
            return EntryExecutionPlan(
                allowed=True,
                reason=f"entry_guard: market ok drift={drift:.3%}",
                order_type="Market",
                market_price=market_price,
                drift_pct=drift,
            )

        is_tg = self._is_telegram_source(sig.source)
        if (
            is_tg
            and self.telegram_limit_entry
            and self._has_signal_levels(sig)
            and drift <= self.max_limit_drift_pct
        ):
            return EntryExecutionPlan(
                allowed=True,
                reason=(
                    f"entry_guard: limit @ {plan_entry:.6g} "
                    f"(drift={drift:.3%}, market={market_price:.6g})"
                ),
                order_type="Limit",
                limit_price=plan_entry,
                market_price=market_price,
                drift_pct=drift,
            )

        if drift > self.max_skip_drift_pct:
            return EntryExecutionPlan(
                allowed=False,
                reason=(
                    f"entry_guard: цена ушла от плана {drift:.2%} > "
                    f"{self.max_skip_drift_pct:.2%} (план={plan_entry:.6g} рынок={market_price:.6g})"
                ),
                market_price=market_price,
                drift_pct=drift,
            )

        return EntryExecutionPlan(
            allowed=False,
            reason=(
                f"entry_guard: drift {drift:.2%} — между лимитом и market, пропуск "
                f"(порог market {self.max_market_drift_pct:.2%})"
            ),
            market_price=market_price,
            drift_pct=drift,
        )


async def build_entry_execution_plan(
    sig: UnifiedSignal,
    *,
    plan_entry: float,
    exchange,
    cfg: Dict[str, Any],
) -> EntryExecutionPlan:
    guard = EntryGuard(cfg)
    pd_limits = entry_drift_limits(cfg, sig)
    if pd_limits:
        guard = EntryGuard(
            {
                **cfg,
                "entry_guard": {
                    **(cfg.get("entry_guard") or {}),
                    **pd_limits,
                    "telegram_limit_entry": False,
                },
            }
        )
    market_price = float(await exchange.get_price(sig.symbol))
    if plan_entry <= 0:
        plan_entry = market_price
    plan = guard.plan_execution(sig, plan_entry=plan_entry, market_price=market_price)
    if pd_limits and plan.allowed:
        return EntryExecutionPlan(
            allowed=plan.allowed,
            reason=f"pump_dump fast entry | {plan.reason}",
            order_type="Market",
            market_price=plan.market_price,
            drift_pct=plan.drift_pct,
        )
    return plan
