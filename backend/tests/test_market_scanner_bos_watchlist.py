"""Тесты watchlist BOS для MARKET SCANNER."""
from __future__ import annotations

from dataclasses import asdict

from scripts.telegram_signal_agent import MarketSetup, TelegramSignalAgent


def _sample_setup(*, confirmed_bos: bool = False, score: int = 80) -> MarketSetup:
    return MarketSetup(
        checked_at_utc="2026-06-21T20:00:00+00:00",
        symbol="SANDUSDT",
        scenario="PUMP",
        score=score,
        price=0.05732,
        turnover_24h=15_200_000.0,
        range_low=0.05657,
        range_high=0.05751,
        consolidation_bars=36,
        range_pct=1.65,
        atr_pct=0.25,
        volume_ratio=3.86,
        bos_level=0.05751,
        fvg_low=0.05697,
        fvg_high=0.05705,
        invalidation=0.05737,
        target=0.058168,
        reasons=["test"],
        confirmed_bos=confirmed_bos,
    )


def _minimal_agent(**overrides) -> TelegramSignalAgent:
    agent = TelegramSignalAgent.__new__(TelegramSignalAgent)
    defaults = {
        "market_scanner_bos_watchlist_enabled": True,
        "market_scanner_execute_require_confirmed_bos": True,
        "market_scanner_execute_min_score": 75,
        "market_scanner_bos_buffer_pct": 0.12,
        "market_scanner_bos_watchlist_timeout_hours": 6.0,
        "market_scanner_bos_watchlist_max": 30,
        "state": {},
        "agent_cfg": {},
        "cfg": {},
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(agent, key, value)
    agent._effective_market_scanner_auto_execute = lambda: True  # type: ignore[method-assign]
    agent._effective_min_rr_for_exec = lambda: 2.0  # type: ignore[method-assign]
    return agent


def test_should_add_to_bos_watchlist_when_observation_only():
    agent = _minimal_agent()
    setup = _sample_setup(confirmed_bos=False, score=80)
    assert agent._should_add_to_bos_watchlist(setup)


def test_should_not_add_when_bos_already_confirmed():
    agent = _minimal_agent()
    setup = _sample_setup(confirmed_bos=True, score=80)
    assert not agent._should_add_to_bos_watchlist(setup)


def test_should_not_add_when_score_below_exec_min():
    agent = _minimal_agent()
    setup = _sample_setup(confirmed_bos=False, score=70)
    assert not agent._should_add_to_bos_watchlist(setup)


def test_add_to_bos_watchlist_dedup_by_symbol_scenario():
    agent = _minimal_agent()
    setup = _sample_setup()
    agent._add_to_bos_watchlist(setup)
    agent._add_to_bos_watchlist(setup)
    rows = agent.state.get("market_scanner_bos_watchlist", [])
    assert len(rows) == 1
    assert rows[0]["symbol"] == "SANDUSDT"


def test_refresh_setup_for_bos_entry_updates_price_and_confirmed():
    agent = _minimal_agent()
    setup = _sample_setup(confirmed_bos=False)
    refreshed = agent._refresh_setup_for_bos_entry(setup, 0.05760)
    assert refreshed.confirmed_bos is True
    assert refreshed.price == 0.05760
    assert refreshed.target > setup.target - 1e-9


def test_market_setup_from_dict_roundtrip():
    agent = _minimal_agent()
    setup = _sample_setup()
    restored = agent._market_setup_from_dict(asdict(setup))
    assert restored is not None
    assert restored.symbol == setup.symbol
    assert restored.confirmed_bos == setup.confirmed_bos
