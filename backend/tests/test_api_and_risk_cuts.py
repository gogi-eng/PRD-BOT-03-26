"""Правки 1–6: журнал ликвидаций, одна свеча 15m, REST, funding/OI, стакан, дневной замок."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from analysis.funding_filter import FundingFilter
from analysis.orderflow_analyzer import book_entry_block_reason, ws_book_entry_block
from exchange.bybit_client import BybitClient, limit_reset_sleep_sec
from prd_agent.exchange.api_stats import ApiCallFanout, ApiCallJournal
from prd_agent.market.scanner_core import begin_scanner_rest_pass, end_scanner_rest_pass
from prd_agent.signals.external_sentiment_agent import ExternalSentimentAgent
from prd_agent.signals.router import SignalRouter
from prd_agent.signals.whale_news_agent import WhaleNewsAgent


def _bars(n: int = 120) -> list:
    rows = []
    for i in range(n):
        px = 100.0 + i * 0.01
        rows.append(
            {
                "timestamp": 1_700_000_000_000 + i * 900_000,
                "open": px,
                "high": px + 1,
                "low": px - 1,
                "close": px,
                "volume": 1000.0 if i == n - 1 else 10.0,
            }
        )
    return rows


def _router_cfg(tmp_path: Path, **extra) -> dict:
    cfg = {
        "_root": str(tmp_path),
        "trading": {
            "min_signal_confidence": 0.9,
            "min_own_agent_confidence": 0.9,
            "min_24h_volume_usdt": 1,
        },
        "signals": {
            "own_agents_enabled": False,
            "telegram_inbox_enabled": False,
            "whale_news_enabled": False,
        },
        "ta_scanner": {"enabled": False},
        "external_sentiment": {"enabled": False},
        "zone_entry": {"enabled": False, "sources": ["own_multi_agent", "ta_volatility", "hybrid"]},
    }
    cfg.update(extra)
    return cfg


class _KlineExchange:
    def __init__(self, bars: list | None = None):
        self.calls: list[tuple] = []
        self._bars = bars if bars is not None else _bars()

    async def get_klines(self, symbol, interval="15", limit=200):
        self.calls.append((str(symbol).upper(), str(interval), int(limit)))
        return list(self._bars)

    async def get_price(self, symbol):
        return 100.0

    async def get_tickers(self):
        self.calls.append(("tickers", "", 0))
        return [
            {
                "symbol": "BTCUSDT",
                "turnover24h": "9000000000",
                "price24hPcnt": "0.05",
                "lastPrice": "100",
            }
        ]


def test_liquidation_journal_writes_notional_and_rotates(tmp_path):
    path = tmp_path / "liquidations.jsonl"
    client = BybitClient(
        "k",
        "s",
        share_rate_gate=False,
        liquidation_log_path=str(path),
        liquidation_log_max_bytes=1,
    )

    def _no_order(*_a, **_k):
        raise AssertionError("из журнала ликвидации ордер ставить нельзя")

    client.place_order = _no_order  # type: ignore[method-assign]
    client._store_liquidation_event({"s": "BTCUSDT", "S": "Buy", "v": "2", "p": "10", "T": 123})
    client._store_liquidation_event(
        {"s": "ETHUSDT", "S": "Sell", "v": "3", "p": "20", "T": 456}
    )
    first = json.loads((tmp_path / "liquidations.jsonl.1").read_text(encoding="utf-8").strip())
    assert first["symbol"] == "BTCUSDT"
    assert first["S"] == "Buy"
    assert first["v"] == 2.0
    assert first["p"] == 10.0
    assert first["T"] == 123
    assert first["notional"] == 20.0
    second = json.loads(path.read_text(encoding="utf-8").strip())
    assert second["symbol"] == "ETHUSDT"
    assert second["notional"] == 60.0


@pytest.mark.asyncio
async def test_liquidation_subscribe_does_not_reconnect_until_set_grows(monkeypatch):
    client = BybitClient("k", "s", share_rate_gate=False)
    created: list = []

    def fake_create_task(coro):
        coro.close()

        async def _noop():
            await asyncio.sleep(3600)

        task = asyncio.get_running_loop().create_task(_noop())
        created.append(task)
        return task

    monkeypatch.setattr("exchange.bybit_client.asyncio.create_task", fake_create_task)
    await client.set_liquidation_symbols(["btcusdt", "ETHUSDT"])
    assert client._liq_symbols == ("BTCUSDT", "ETHUSDT")
    assert len(created) == 1
    await client.set_liquidation_symbols(["BTCUSDT"])
    assert len(created) == 1
    await client.set_liquidation_symbols(["SOLUSDT"])
    assert "SOLUSDT" in client._liq_symbols
    assert "BTCUSDT" in client._liq_symbols
    assert len(created) == 2
    await client.stop_liquidation_stream()


@pytest.mark.asyncio
async def test_whale_and_liquidity_do_not_resubscribe_short_lists():
    class Ex:
        def __init__(self):
            self.sets: list = []
            self.history: list = []

        async def set_liquidation_symbols(self, symbols):
            self.sets.append(list(symbols))

        async def get_recent_liquidations(self, sym, limit=30):
            if sym == "ETHUSDT":
                return []
            return [{"price": 100.0, "size": 10000.0, "side": "Buy"}]

        async def get_tickers(self):
            return [
                {"symbol": "BTCUSDT", "fundingRate": "0.01", "singleOpenInterest": "5"},
                {"symbol": "ETHUSDT", "fundingRate": "0.0001", "singleOpenInterest": "5"},
            ]

        async def get_open_interest_history(self, sym, interval="1h", limit=5):
            self.history.append(sym)
            return [
                {"singleOpenInterest": "110"},
                {"singleOpenInterest": "100"},
            ]

        async def get_orderbook(self, *args, **kwargs):
            return {"bids": [["1", "1"]], "asks": [["1.01", "1"]]}

    ex = Ex()
    agent = WhaleNewsAgent(
        {
            "whale_news": {"liquidation_usd_threshold": 500_000, "oi_change_pct_threshold": 3.0},
            "derivatives_entry_guard": {"extreme_funding_threshold": 0.001},
        }
    )
    out = await agent.scan_whales(ex, ["BTCUSDT", "ETHUSDT"])
    assert ex.sets == []
    assert ex.history == ["BTCUSDT"]
    assert any(s.source == "whale_oi" for s in out)

    import prd_agent.market.liquidity_alerts as alerts

    async def _fast_sleep(_sec):
        return None

    original = alerts.asyncio.sleep
    alerts.asyncio.sleep = _fast_sleep
    state = {"n": 0}

    def _running():
        state["n"] += 1
        return state["n"] == 1

    try:
        await alerts.run_liquidity_alert_loop(
            ex,
            SimpleNamespace(send=_fast_sleep),
            lambda: ["BTCUSDT"],
            {},
            _running,
        )
    finally:
        alerts.asyncio.sleep = original
    assert ex.sets == []


@pytest.mark.asyncio
async def test_collect_all_one_15m_candle_and_4h_only_when_zone_on(tmp_path):
    ex = _KlineExchange()
    cfg = _router_cfg(
        tmp_path,
        external_sentiment={
            "enabled": True,
            "cache_sec": 0,
            "coinugget_style": {"enabled": True, "klines_limit": 60, "max_symbols": 20},
        },
    )
    router = SignalRouter(cfg, tmp_path / "signals")
    seen: list[int] = []
    real = router._external_sentiment._bars_for_sentiment

    def _spy(sym, klines_15m):
        rows = real(sym, klines_15m)
        if rows is not None:
            seen.append(len(rows))
        return rows

    router._external_sentiment._bars_for_sentiment = _spy
    await router.collect_all(ex, ["BTCUSDT", "ETHUSDT"])
    assert ex.calls.count(("BTCUSDT", "15", 120)) == 1
    assert ex.calls.count(("ETHUSDT", "15", 120)) == 1
    assert all(limit != 60 for _sym, _iv, limit in ex.calls if _iv)
    assert all(iv != "240" for _sym, iv, _limit in ex.calls if iv)
    assert seen == [60, 60]

    ex_zone = _KlineExchange()
    cfg_zone = _router_cfg(
        tmp_path,
        ta_scanner={
            "enabled": True,
            "min_24h_change_pct": 1,
            "min_24h_volume_usdt": 1,
            "min_confidence": 0.99,
            "interval": "15",
            "kline_limit": 120,
        },
        zone_entry={"enabled": True, "sources": ["own_multi_agent", "ta_volatility", "hybrid"]},
    )
    router_zone = SignalRouter(cfg_zone, tmp_path / "signals-zone")
    await router_zone.collect_all(ex_zone, ["BTCUSDT"])
    assert ex_zone.calls.count(("BTCUSDT", "15", 120)) == 1
    assert ex_zone.calls.count(("BTCUSDT", "240", 120)) == 1


def test_limit_reset_uses_header_only_for_10006():
    now = 1_700_000_000_000
    headers = {"X-Bapi-Limit-Reset-Timestamp": str(now + 2500)}
    assert limit_reset_sleep_sec(headers, ret_code=10006, now_ms=now, fallback_sec=2.0) == 2.5
    assert limit_reset_sleep_sec(headers, ret_code=10016, now_ms=now, fallback_sec=2.0) == 2.0
    assert limit_reset_sleep_sec({}, ret_code=10006, now_ms=now, fallback_sec=4.0) == 4.0


@pytest.mark.asyncio
async def test_request_records_path_and_sleeps_until_limit_reset(monkeypatch):
    slept: list[float] = []

    async def _sleep(sec):
        slept.append(float(sec))

    monkeypatch.setattr("exchange.bybit_client.asyncio.sleep", _sleep)
    now_ms = 1_700_000_000_000
    monkeypatch.setattr("exchange.bybit_client.time.time", lambda: now_ms / 1000.0)

    class _Resp:
        def __init__(self, payload, headers):
            self.status = 200
            self.headers = headers
            self._payload = payload

        async def json(self):
            return self._payload

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Sess:
        def __init__(self):
            self.closed = False
            self._i = 0

        def get(self, *_args, **_kwargs):
            self._i += 1
            if self._i == 1:
                return _Resp(
                    {"retCode": 10006, "retMsg": "too many", "result": {}},
                    {"X-Bapi-Limit-Reset-Timestamp": str(now_ms + 2500)},
                )
            return _Resp({"retCode": 0, "retMsg": "OK", "result": {"list": []}}, {})

        async def close(self):
            self.closed = True

    client = BybitClient("k", "s", share_rate_gate=False)
    client.public_min_interval = 0
    client.private_min_interval = 0
    client._session = _Sess()
    journal = ApiCallJournal()
    journal.begin_cycle(1)
    client.api_journal = journal
    result = await client._request("GET", "/v5/market/kline", {"symbol": "BTCUSDT"})
    assert result == {"list": []}
    assert journal._cycle_calls["/v5/market/kline"] == 2
    assert slept and abs(slept[0] - 2.5) < 0.05


def test_scanner_rest_line_and_shared_journal(caplog):
    caplog.set_level(logging.INFO)
    local = ApiCallJournal()
    shared = ApiCallJournal()
    shared.begin_cycle(7)
    fan = ApiCallFanout(local, shared)
    agent = SimpleNamespace(_scanner_rest_journal=local)
    begin_scanner_rest_pass(agent)
    fan.record("/v5/market/tickers")
    fan.record("/v5/position/list")
    end_scanner_rest_pass(agent)
    assert "REST сканера за проход: 2" in caplog.text
    assert "/v5/market/tickers=1" in caplog.text
    assert shared._cycle_calls["/v5/position/list"] == 1
    assert shared._cycle_calls["/v5/market/tickers"] == 1


@pytest.mark.asyncio
async def test_scanner_close_does_not_close_orchestrator_session():
    class _Sess:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    orch = BybitClient("k", "s", share_rate_gate=True)
    scan = BybitClient("k", "s", share_rate_gate=True)
    orch._session = _Sess()
    scan._session = _Sess()
    assert orch._rate_gate() is scan._rate_gate()
    await scan.close()
    assert scan._session.closed
    assert orch._session.closed is False
    await orch.close()


@pytest.mark.asyncio
async def test_funding_and_oi_from_fresh_tickers_without_history_http():
    client = BybitClient("k", "s", share_rate_gate=False)

    async def _no_http(*_args, **_kwargs):
        raise AssertionError("свежий список тикеров не должен порождать HTTP")

    client._request = _no_http  # type: ignore[method-assign]
    client._remember_tickers(
        [
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0002",
                "openInterest": "999",
                "singleOpenInterest": "100",
                "singleOpenInterestValue": "5000",
            }
        ]
    )
    got = await client.get_funding_rate("BTCUSDT")
    assert got is not None
    assert got["funding_rate"] == 0.0002
    assert got["open_interest"] == 100.0
    assert got["open_interest_value"] == 5000.0

    class _Ex:
        def __init__(self):
            self.history = 0

        async def get_funding_rate(self, symbol):
            return {"funding_rate": 0.0002, "open_interest": 100.0}

        async def get_open_interest_history(self, symbol, interval="1h", limit=25):
            self.history += 1
            return []

    ex = _Ex()
    signal = await FundingFilter().analyze(ex, "BTCUSDT")
    assert signal.open_interest == 100.0
    assert ex.history == 0
    ex2 = _Ex()

    async def _extreme(symbol):
        return {"funding_rate": 0.002, "open_interest": 100.0}

    ex2.get_funding_rate = _extreme  # type: ignore[method-assign]
    await FundingFilter().analyze(ex2, "BTCUSDT")
    assert ex2.history == 1


def test_book_imbalance_blocks_only_clear_skew_and_wide_spread():
    flat = {"bids": [["100", "10"]], "asks": [["100.01", "10"]]}
    assert book_entry_block_reason(flat, "Buy") == ""
    assert book_entry_block_reason(flat, "Sell") == ""
    mild = {"bids": [["100", "10"]], "asks": [["100.01", "12"]]}
    assert book_entry_block_reason(mild, "Buy") == ""
    skewed = {"bids": [["100", "1"]], "asks": [["100.02", "20"]]}
    buy_block = book_entry_block_reason(skewed, "Buy")
    assert "против Buy" in buy_block
    assert book_entry_block_reason(skewed, "Sell") == ""
    wide = {"bids": [["100", "10"]], "asks": [["101", "10"]]}
    assert "спред" in book_entry_block_reason(wide, "Sell")

    class _Ex:
        def __init__(self, book):
            self.book = book
            self.rest = 0

        def peek_orderbook(self, symbol, limit=50):
            return self.book

        async def get_orderbook(self, *args, **kwargs):
            self.rest += 1
            raise AssertionError("без книги REST стакана не нужен")

    empty = _Ex(None)
    assert ws_book_entry_block(empty, "BTCUSDT", "Buy", {}) == ""
    assert empty.rest == 0
    blocked = _Ex(skewed)
    assert "против Buy" in ws_book_entry_block(blocked, "BTCUSDT", "Buy", {})
    assert blocked.rest == 0
    assert ws_book_entry_block(SimpleNamespace(), "BTCUSDT", "Buy", {}) == ""


def test_sentiment_tail_is_last_60_bars():
    agent = ExternalSentimentAgent(
        {"external_sentiment": {"coinugget_style": {"klines_limit": 60}}}
    )
    rows = agent._bars_for_sentiment("BTCUSDT", {"BTCUSDT": _bars(120)})
    assert rows is not None
    assert len(rows) == 60
    assert rows[0]["timestamp"] == _bars(120)[-60]["timestamp"]
