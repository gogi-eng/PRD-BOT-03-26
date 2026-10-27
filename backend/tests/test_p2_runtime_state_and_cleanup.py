"""Тесты Пакета 3 (P2): runtime_config_overrides.yaml, PortfolioProfitLock, BybitAdapter, scanner_core и гигиена."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

from prd_agent.config import (
    load_config,
    load_runtime_overrides,
    runtime_overrides_path,
)
from prd_agent.config_presets import apply_risk_preset
from prd_agent.config_validate import validate_runtime_overrides
from prd_agent.engine.orchestrator import UnifiedOrchestrator
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.exchange.bybit_adapter import BybitAdapter
from prd_agent.market.scanner_core import (
    execute_market_scan_once,
    execute_spike_scan_once,
    looks_like_usdt_trade_symbol,
)
from prd_agent.positions.portfolio_profit_lock import LockStatus, PortfolioProfitLock


def _write_sample_config(cfg_path: Path) -> str:
    content = (
        "# Read-only base config with important comments\n"
        "bybit:\n"
        "  api_key: ''\n"
        "  api_secret: ''\n"
        "  testnet: true\n"
        "  category: linear\n"
        "telegram:\n"
        "  bot_token: ''\n"
        "  chat_id: ''\n"
        "trading:\n"
        "  leverage: 10\n"
        "  loop_interval_sec: 60\n"
        "  max_positions: 5\n"
        "  risk_pct_per_trade: 0.35\n"
        "  min_signal_confidence: 0.85\n"
        "risk:\n"
        "  max_consecutive_losses: 4\n"
        "  max_daily_loss_pct: 5.0\n"
        "  max_daily_loss_usdt: 20.0\n"
        "  cooldown_after_loss_sec: 300\n"
        "quality_gate:\n"
        "  min_rr_ratio: 2.0\n"
        "  min_confidence: 0.85\n"
        "positions:\n"
        "  trailing_enabled: true\n"
        "  adopt_manual: true\n"
        "signals:\n"
        "  own_agents_enabled: false\n"
        "  telegram_inbox_enabled: false\n"
        "  whale_news_enabled: false\n"
        "  min_analysis_confidence: 0.88\n"
        "ta_scanner:\n"
        "  enabled: false\n"
        "external_sentiment:\n"
        "  enabled: false\n"
        "risk_presets:\n"
        "  conservative:\n"
        "    trading:\n"
        "      min_signal_confidence: 0.88\n"
        "      risk_pct_per_trade: 0.25\n"
        "    quality_gate:\n"
        "      min_rr_ratio: 2.2\n"
    )
    cfg_path.write_text(content, encoding="utf-8")
    return content


def test_p2_self_improver_presets_and_orchestrator_use_overrides_without_touching_config_yaml(tmp_path: Path):
    """Все авто-правки, пресеты и переключатели пишут в data/runtime_config_overrides.yaml, оставляя config.yaml нетронутым."""
    cfg_path = tmp_path / "config.yaml"
    original_content = _write_sample_config(cfg_path)
    cfg = load_config(cfg_path)

    # 1. SelfImprover low-risk tune + rollback
    imp = SelfImprover(cfg, tmp_path)
    applied = imp.apply_low_risk_proposal(
        {
            "risk": "low",
            "path": ["quality_gate", "min_rr_ratio"],
            "delta": -0.10,
            "summary": "loosen rr",
        },
        reload=False,
    )
    assert applied is True
    assert cfg_path.read_text(encoding="utf-8") == original_content, "config.yaml должен оставаться read-only"

    ov_path = runtime_overrides_path(cfg_path)
    assert ov_path.is_file()
    loaded = load_config(cfg_path)
    assert loaded["quality_gate"]["min_rr_ratio"] == pytest.approx(1.90)

    # Откат из sandbox-бэкапа возвращает предыдущий оверлей (2.0 в базе)
    rb = imp.rollback_last_config()
    assert rb is not None
    loaded_after_rb = load_config(cfg_path)
    assert loaded_after_rb["quality_gate"]["min_rr_ratio"] == pytest.approx(2.0)
    assert cfg_path.read_text(encoding="utf-8") == original_content

    # 2. apply_risk_preset
    changes, _ = apply_risk_preset(cfg_path, loaded_after_rb, "conservative")
    assert changes
    assert cfg_path.read_text(encoding="utf-8") == original_content
    loaded_preset = load_config(cfg_path)
    assert loaded_preset["trading"]["risk_pct_per_trade"] == pytest.approx(0.25)
    assert loaded_preset["risk_presets_meta"]["active"] == "conservative"

    # 3. Orchestrator set_trailing_enabled и set_adopt_manual
    orch = UnifiedOrchestrator(loaded_preset)
    orch.set_trailing_enabled(False)
    orch.set_adopt_manual(False)
    assert cfg_path.read_text(encoding="utf-8") == original_content
    loaded_final = load_config(cfg_path)
    assert loaded_final["positions"]["trailing_enabled"] is False
    assert loaded_final["positions"]["adopt_manual"] is False
    assert loaded_final["positions"]["sl_tp_guard"]["include_manual"] is False


def test_p2_validate_runtime_overrides_catches_typos_and_ranges(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    _write_sample_config(cfg_path)
    base = load_config(cfg_path)

    ok_good, errs_good = validate_runtime_overrides(
        {"trading": {"risk_pct_per_trade": 0.4}, "positions": {"trailing_enabled": False}},
        base_data=base,
    )
    assert ok_good is True, errs_good

    ok_typo, errs_typo = validate_runtime_overrides(
        {"trading": {"risk_per_trade": 0.4}},
        base_data=base,
    )
    assert ok_typo is False
    assert any("risk_pct_per_trade" in e for e in errs_typo)

    ok_range, errs_range = validate_runtime_overrides(
        {"quality_gate": {"min_rr_ratio": 9.9}},
        base_data=base,
    )
    assert ok_range is False
    assert any("quality_gate" in e for e in errs_range)


def test_p2_portfolio_profit_lock_filters_manual_and_arms_on_bot_positions():
    """PortfolioProfitLock._filter_positions корректно отсекает manual при skip_manual_positions=True и закрывает при спаде."""
    closed_calls: List[str] = []

    class _FakeClient:
        prices = {"BTCUSDT": 110.0, "ETHUSDT": 200.0}

        async def get_price(self, symbol: str) -> float:
            return self.prices.get(symbol, 100.0)

        async def close_position(self, symbol: str, side: str, qty: float) -> Dict[str, Any]:
            closed_calls.append(symbol)
            return {"success": True}

    client = _FakeClient()
    lock = PortfolioProfitLock(
        client=client,
        min_profit_pct=5.0,
        decline_threshold_pct=20.0,
        decline_duration_sec=0.0,
        skip_manual_positions=True,
        enabled=True,
    )
    lock.set_initial_balance(100.0)
    lock.set_bot_symbols({"BTCUSDT"})

    # Позиции в формате биржи (list of dicts): BTCUSDT (bot, +$10 = +10%) и ETHUSDT (manual, +$50)
    positions = [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "1.0", "avgPrice": "100.0"},
        {"symbol": "ETHUSDT", "side": "Buy", "size": "1.0", "avgPrice": "150.0"},
    ]

    filtered = lock._filter_positions(positions)
    assert "BTCUSDT" in filtered
    assert "ETHUSDT" not in filtered, "Ручная позиция должна быть отфильтрована"

    # Шаг 1: Активация защиты (INACTIVE -> ARMED, PnL = +$10 = 10% >= 5%)
    res1 = asyncio.run(lock.check(positions))
    assert res1 is None
    assert lock.state.status == LockStatus.ARMED
    assert lock.state.max_profit_usdt == pytest.approx(10.0)

    # Шаг 2: Откат цены BTC до 107 (прибыль +$7, снижение 30% >= 20% порога) -> переход в DECLINING
    client.prices["BTCUSDT"] = 107.0
    res2 = asyncio.run(lock.check(positions))
    assert res2 is None
    assert lock.state.status == LockStatus.DECLINING

    # Шаг 3: Таймер decline_duration_sec=0 истёк -> закрытие всех bot-позиций
    res3 = asyncio.run(lock.check(positions))
    assert res3 == ["BTCUSDT"]
    assert closed_calls == ["BTCUSDT"]
    assert lock.state.status == LockStatus.COOLDOWN


def test_p2_bybit_adapter_require_prd_client_raises_when_missing(tmp_path: Path, monkeypatch):
    import prd_agent.exchange.bybit_adapter as adapter_mod

    monkeypatch.setattr(adapter_mod, "_import_local_client", lambda _root: None)
    with pytest.raises(RuntimeError, match="require_prd_client"):
        BybitAdapter(
            {
                "_root": str(tmp_path),
                "bybit": {
                    "api_key": "",
                    "api_secret": "",
                    "testnet": True,
                    "require_prd_client": True,
                },
            }
        )


def test_p2_scanner_core_executes_spike_and_market_scans():
    assert looks_like_usdt_trade_symbol("BTCUSDT") is True
    assert looks_like_usdt_trade_symbol("BTCUSDC") is False

    @dataclass
    class _FakeSetup:
        symbol: str
        scenario: str
        score: int
        range_pct: float
        price: float = 100.0
        target: float = 105.0
        invalidation: float = 98.0

    class _FakeBybit:
        async def get_tickers(self):
            return [{"symbol": "SOLUSDT", "turnover24h": "25000000"}]

    class _FakeAgent:
        def __init__(self) -> None:
            self.spike_scalp_enabled = True
            self.market_scanner_enabled = True
            self.market_scanner_learning_enabled = False
            self.bybit = _FakeBybit()
            self.market_scanner_blacklist: set[str] = set()
            self.market_scanner_min_24h_volume_usdt = 10_000_000
            self.market_scanner_max_symbols = 10
            self.market_scanner_top_n = 1
            self.market_scanner_symbol_cooldown_sec = 600
            self.market_scanner_min_score_to_notify = 60
            self.market_scanner_min_volume_ratio = 1.2
            self.market_scanner_max_range_pct = 5.0
            self.market_scanner_max_atr_pct = 3.0
            self.spike_scalp_top_n = 1
            self.spike_scalp_auto_execute = False
            self._spike_scalp_cfg = type("Cfg", (), {"min_24h_volume_usdt": 10_000_000, "max_symbols": 10})()
            self.state: Dict[str, Any] = {}
            self.executed: List[str] = []

        @contextmanager
        def _market_scan_cross_process_lock(self, **_kwargs) -> Iterator[bool]:
            yield True

        async def _ensure_execution(self) -> None:
            pass

        async def _timesfm_resolve_pending(self) -> None:
            pass

        async def _evaluate_spike_pullback_watchlist(self) -> None:
            pass

        async def _evaluate_pending_market_setups(self) -> None:
            pass

        async def _evaluate_bos_watchlist(self) -> None:
            pass

        def _reload_market_scanner_notified_from_disk(self) -> None:
            pass

        async def _get_valid_symbols(self) -> set[str]:
            return {"SOLUSDT"}

        def _ticker_turnover_usdt(self, ticker: Dict[str, Any]) -> float:
            return float(ticker.get("turnover24h", 0))

        async def _analyze_spike_setup(self, ticker: Dict[str, Any]) -> Any:
            return _FakeSetup(symbol="SOLUSDT", scenario="PUMP", score=85, range_pct=4.5)

        async def _analyze_market_setup(self, ticker: Dict[str, Any]) -> Any:
            return _FakeSetup(symbol="SOLUSDT", scenario="PUMP", score=80, range_pct=2.5)

        def _spike_scanner_on_cooldown(self, symbol: str) -> bool:
            return False

        def _market_scanner_on_cooldown(self, symbol: str, scenario: str) -> bool:
            return False

        def _mark_spike_scanner_notified(self, setup: Any) -> None:
            pass

        def _mark_market_scanner_notified(self, setup: Any) -> None:
            pass

        def _save_state(self) -> None:
            pass

        def _notify_spike_setup(self, setup: Any) -> None:
            pass

        def _notify_market_setup(self, setup: Any) -> None:
            pass

        def _append_market_setup(self, setup: Any) -> None:
            pass

        def _track_market_setup_for_learning(self, setup: Any) -> None:
            pass

        def _should_add_to_bos_watchlist(self, setup: Any) -> bool:
            return False

        def _add_to_bos_watchlist(self, setup: Any) -> None:
            pass

        async def _try_execute_market_setup(self, setup: Any) -> None:
            self.executed.append(setup.symbol)

        async def _check_scanner_reversals_against_open_positions(self, setups: List[Any]) -> None:
            pass

        def _effective_market_scanner_auto_execute(self) -> bool:
            return True

        def _runtime_controls_dict(self) -> Dict[str, Any]:
            return {"pause_all_execution": False}

    agent = _FakeAgent()
    spike_res = asyncio.run(execute_spike_scan_once(agent))
    market_res = asyncio.run(execute_market_scan_once(agent))
    assert len(spike_res) == 1 and spike_res[0].symbol == "SOLUSDT"
    assert len(market_res) == 1 and market_res[0].symbol == "SOLUSDT"
    assert agent.executed == ["SOLUSDT", "SOLUSDT"]


def test_p2_readme_and_gitignore_hygiene():
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "Etherscan API KEY" not in readme
    assert "runtime_config_overrides.yaml" in readme
    assert (root / "docs" / "sessions.md").is_file()

    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".wt-prd", ".wt-world", "*.session", "data/runtime_config_overrides.yaml"):
        assert pattern in gitignore
