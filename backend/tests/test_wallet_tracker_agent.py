"""Tests for WalletFlowAgent (advisory wallet tracker v1)."""
from __future__ import annotations

from pathlib import Path

from prd_agent.analysis.wallet_flow_agent import (
    StubSwapProvider,
    SwapEvent,
    WalletFlowAgent,
    map_token_to_bybit_symbol,
    wallet_tracker_enabled,
)


def test_map_token_to_bybit_symbol_known() -> None:
    assert map_token_to_bybit_symbol("PEPE") == "1000PEPEUSDT"
    assert map_token_to_bybit_symbol("pepe") == "1000PEPEUSDT"
    assert map_token_to_bybit_symbol("WETH") == "ETHUSDT"
    assert map_token_to_bybit_symbol("BTC") == "BTCUSDT"
    assert map_token_to_bybit_symbol("USDT") is None
    assert map_token_to_bybit_symbol("DAI") is None


def test_map_token_to_bybit_symbol_heuristic() -> None:
    assert map_token_to_bybit_symbol("LINK") == "LINKUSDT"
    assert map_token_to_bybit_symbol("NEWCOIN") == "NEWCOINUSDT"


def test_filter_min_usd() -> None:
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "min_swap_usd": 5000,
            "watches": [],
        }
    }
    agent = WalletFlowAgent(cfg, Path("."), provider=StubSwapProvider())
    events = [
        SwapEvent("0x1", "eth", "PEPE", "0xa", "buy", 1000.0, 1.0, "h1"),
        SwapEvent("0x1", "eth", "PEPE", "0xa", "buy", 6000.0, 2.0, "h2"),
    ]
    got = agent.filter_min_usd(events)
    assert len(got) == 1
    assert got[0].usd_value == 6000.0


def test_build_recommendations_from_fake_swaps(tmp_path: Path) -> None:
    clock = {"t": 1_000_000.0}

    def now() -> float:
        return clock["t"]

    w1 = "0xabc0000000000000000000000000000000000001"
    w2 = "0xdef0000000000000000000000000000000000002"
    provider = StubSwapProvider(
        [
            SwapEvent(w1, "eth", "PEPE", "0xp", "buy", 12_000.0, 999_000.0, "tx1", "whale1"),
            SwapEvent(w1, "eth", "PEPE", "0xp", "buy", 8_000.0, 999_100.0, "tx2", "whale1"),
            SwapEvent(w2, "eth", "PEPE", "0xp", "sell", 1_000.0, 999_200.0, "tx3", "whale2"),
        ]
    )
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "min_swap_usd": 5000,
            "symbol_cooldown_sec": 1800,
            "recommendation_ttl_sec": 3600,
            "watches": [
                {"address": w1, "label": "whale1"},
                {"address": w2, "label": "whale2"},
            ],
        }
    }
    agent = WalletFlowAgent(cfg, tmp_path, provider=provider, time_fn=now)
    events = agent.collect_swaps()
    # sell 1000 filtered by min_usd; buys remain
    recs = agent.build_recommendations(events)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.symbol == "1000PEPEUSDT"
    assert rec.bias == "long"
    assert rec.advisory is True
    assert rec.source == "wallet_flow"
    assert rec.confidence > 0.3


def test_symbol_cooldown(tmp_path: Path) -> None:
    clock = {"t": 1_000_000.0}

    def now() -> float:
        return clock["t"]

    w1 = "0x1111111111111111111111111111111111111111"
    events = [
        SwapEvent(w1, "eth", "LINK", "0xl", "buy", 20_000.0, 1.0, "a"),
    ]
    provider = StubSwapProvider(events)
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "min_swap_usd": 5000,
            "symbol_cooldown_sec": 1800,
            "watches": [{"address": w1, "label": "w"}],
        }
    }
    agent = WalletFlowAgent(cfg, tmp_path, provider=provider, time_fn=now)
    r1 = agent.build_recommendations(events)
    assert len(r1) == 1
    r2 = agent.build_recommendations(events)
    assert r2 == []
    clock["t"] += 1801
    r3 = agent.build_recommendations(events)
    assert len(r3) == 1


def test_disabled_without_key_no_crash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEBANK_ACCESS_KEY", raising=False)
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "watches": [{"address": "0x1234567890abcdef1234567890abcdef12345678", "label": "x"}],
        }
    }
    agent = WalletFlowAgent(cfg, tmp_path)
    assert agent.enabled is True
    assert agent.active is False
    assert agent.disable_reason == "no API key"
    assert agent.should_run_loop() is False
    assert agent.build_recommendations([]) == []
    assert "Wallet tracker" in agent.build_report()


def test_enabled_empty_watches_no_crash(tmp_path: Path) -> None:
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "min_swap_usd": 5000,
            "watches": [
                {"address": "0x...", "label": "example_whale"},  # placeholder — skip
            ],
        }
    }
    agent = WalletFlowAgent(cfg, tmp_path, provider=StubSwapProvider())
    assert agent.active is True
    assert agent.watches == []
    assert agent.collect_swaps() == []
    assert agent.build_recommendations([]) == []


def test_wallet_tracker_enabled_flag() -> None:
    assert wallet_tracker_enabled({"wallet_tracker": {"enabled": True}}) is True
    assert wallet_tracker_enabled({"wallet_tracker": {"enabled": False}}) is False
    assert wallet_tracker_enabled({}) is False


def test_ignore_unmapped_trash(tmp_path: Path) -> None:
    clock = {"t": 50.0}
    w1 = "0x2222222222222222222222222222222222222222"
    events = [
        SwapEvent(w1, "eth", "USDT", "0xu", "buy", 50_000.0, 1.0, "t1"),
        SwapEvent(w1, "eth", "DAI", "0xd", "buy", 50_000.0, 1.0, "t2"),
    ]
    cfg = {
        "wallet_tracker": {
            "enabled": True,
            "min_swap_usd": 1000,
            "watches": [{"address": w1}],
        }
    }
    agent = WalletFlowAgent(
        cfg, tmp_path, provider=StubSwapProvider(events), time_fn=lambda: clock["t"]
    )
    assert agent.build_recommendations(events) == []
