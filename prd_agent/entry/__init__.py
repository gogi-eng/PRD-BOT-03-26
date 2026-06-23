"""Планирование входа по зонам SMC / BOS / ретест (мост к engine/entry_engine)."""

__all__ = [
    "EntryEngineBridge",
    "ZoneEntryPlan",
    "check_impulse_retest_confirmation",
]


def __getattr__(name: str):
    if name == "EntryEngineBridge":
        from prd_agent.entry.entry_engine_bridge import EntryEngineBridge

        return EntryEngineBridge
    if name == "ZoneEntryPlan":
        from prd_agent.entry.entry_engine_bridge import ZoneEntryPlan

        return ZoneEntryPlan
    if name == "check_impulse_retest_confirmation":
        from prd_agent.entry.impulse_retest import check_impulse_retest_confirmation

        return check_impulse_retest_confirmation
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
