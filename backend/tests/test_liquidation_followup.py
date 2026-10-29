"""Крупная ликвидация: порог 10000 USDT и запись цены через 1, 5 и 15 минут.

Ордер из этого журнала не ставится.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from exchange.bybit_client import BybitClient
from exchange.liquidation_followup import (
    LARGE_LIQUIDATION_USDT,
    LiquidationFollowupJournal,
    is_large_liquidation,
    summary_counts,
)

_PRINT_MS = 1_700_000_000_000


def _no_order(*_args, **_kwargs):
    raise AssertionError("из разбора ликвидации ордер ставить нельзя")


def _row(symbol: str, side: str, price: float, size: float, ts_ms: int = _PRINT_MS) -> dict:
    return {
        "symbol": symbol,
        "S": side,
        "v": size,
        "p": price,
        "T": ts_ms,
        "notional": price * size,
    }


def _bar_start(ts_ms: int) -> int:
    return ts_ms - (ts_ms % 60_000)


def test_threshold_is_inclusive_10000_usdt():
    assert LARGE_LIQUIDATION_USDT == 10_000.0
    assert is_large_liquidation(10_000) is True
    assert is_large_liquidation(10_000.0) is True
    assert is_large_liquidation(10_000.01) is True
    assert is_large_liquidation(9_999.99) is False
    assert is_large_liquidation(0) is False
    assert is_large_liquidation(-10_000) is False
    assert is_large_liquidation(None) is False
    assert is_large_liquidation(float("nan")) is False
    assert is_large_liquidation(float("inf")) is False


def test_followup_module_does_not_place_orders():
    text = (Path(__file__).resolve().parents[2] / "exchange" / "liquidation_followup.py").read_text(
        encoding="utf-8"
    )
    assert "place_order" not in text
    assert "trailingStop" not in text
    assert "channel_trade_search" not in text


def test_below_threshold_does_not_open_followup(tmp_path: Path):
    journal = LiquidationFollowupJournal.beside(tmp_path / "liquidations.jsonl")
    assert journal.note_print(_row("BTCUSDT", "Buy", 9_999.99, 1.0)) is None
    assert not journal.path.exists()
    assert not journal.summary_path.exists()


def test_followup_record_direction_percent_and_summary(tmp_path: Path):
    journal = LiquidationFollowupJournal.beside(tmp_path / "liquidations.jsonl")
    buy = journal.note_print(_row("BTCUSDT", "Buy", 100.0, 100.0))
    sell = journal.note_print(_row("ETHUSDT", "Sell", 200.0, 50.0))
    assert buy is not None and sell is not None
    assert journal.note_print(_row("BTCUSDT", "Buy", 100.0, 100.0)) is None

    buy_id = buy["id"]
    sell_id = sell["id"]
    assert journal.fill(buy_id, "1m", 90.0, _PRINT_MS + 60_000 - 1, source="last") is False
    assert journal.fill(buy_id, "1m", 90.0, _PRINT_MS + 60_000, source="last") is True
    assert journal.fill(buy_id, "1m", 50.0, _PRINT_MS + 120_000, source="last") is False
    assert journal.fill(buy_id, "5m", 110.0, _PRINT_MS + 300_000, source="last") is True
    assert journal.fill(buy_id, "15m", 100.0, _PRINT_MS + 900_000, source="last") is True
    assert journal.fill(sell_id, "1m", 220.0, _PRINT_MS + 60_000, source="last") is True
    assert journal.fill(sell_id, "5m", 180.0, _PRINT_MS + 300_000, source="last") is True

    reloaded = LiquidationFollowupJournal(journal.path, journal.summary_path)
    rows = [json.loads(line) for line in journal.path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    saved = {row["symbol"]: row for row in rows}
    buy_1m = saved["BTCUSDT"]["horizons"]["1m"]
    assert saved["BTCUSDT"]["price_then"] == 100.0
    assert saved["BTCUSDT"]["notional"] == 10_000.0
    assert saved["BTCUSDT"]["note"] == "Журнал только для разбора. Из этой строки ордер не ставится."
    assert buy_1m["status"] == "filled"
    assert buy_1m["price"] == 90.0
    assert buy_1m["direction"] == "down"
    assert buy_1m["pct"] == -10.0
    assert buy_1m["vs_print"] == "continued"
    assert "продолжение" in buy_1m["reading"]
    assert saved["BTCUSDT"]["horizons"]["5m"]["direction"] == "up"
    assert saved["BTCUSDT"]["horizons"]["5m"]["vs_print"] == "reversed"
    assert saved["BTCUSDT"]["horizons"]["5m"]["pct"] == 10.0
    assert saved["BTCUSDT"]["horizons"]["15m"]["direction"] == "flat"
    assert saved["BTCUSDT"]["horizons"]["15m"]["vs_print"] == "flat"
    assert saved["ETHUSDT"]["horizons"]["1m"]["vs_print"] == "continued"
    assert saved["ETHUSDT"]["horizons"]["1m"]["direction"] == "up"
    assert saved["ETHUSDT"]["horizons"]["1m"]["pct"] == 10.0
    assert saved["ETHUSDT"]["horizons"]["5m"]["vs_print"] == "reversed"
    assert saved["ETHUSDT"]["horizons"]["5m"]["pct"] == -10.0
    assert saved["ETHUSDT"]["horizons"]["15m"]["status"] == "wait"

    counts = summary_counts(rows)
    assert counts["n"] == 2
    assert counts["sides"]["Buy"]["1m"]["continued"] == 1
    assert counts["sides"]["Buy"]["5m"]["reversed"] == 1
    assert counts["sides"]["Buy"]["15m"]["flat"] == 1
    assert counts["sides"]["Sell"]["1m"]["continued"] == 1
    assert counts["sides"]["Sell"]["5m"]["reversed"] == 1
    assert counts["sides"]["Sell"]["15m"]["wait"] == 1
    text = reloaded.summary_text()
    assert "Из этой строки ордер не ставится." in text
    assert "Всего записей: 2" in text
    assert "1 мин: продолжение 1, разворот 0, без хода 0, ещё ждём 0, нет цены 0" in text
    assert "15 мин: продолжение 0, разворот 0, без хода 0, ещё ждём 1, нет цены 0" in text
    assert text == journal.summary_path.read_text(encoding="utf-8")


def test_client_threshold_keeps_small_print_out_of_followup(tmp_path: Path):
    path = tmp_path / "liquidations.jsonl"
    client = BybitClient("k", "s", share_rate_gate=False, liquidation_log_path=str(path))
    client.place_order = _no_order  # type: ignore[method-assign]
    client._store_liquidation_event(
        {"s": "ETHUSDT", "S": "Sell", "v": "2", "p": "10", "T": 1_700_000_000_000}
    )
    client._store_liquidation_event(
        {"s": "BTCUSDT", "S": "Buy", "v": "125", "p": "80", "T": 1_700_000_000_100}
    )
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2
    small = json.loads(raw_lines[0])
    large = json.loads(raw_lines[1])
    assert set(small) == {"symbol", "S", "v", "p", "T", "notional"}
    assert small["notional"] == 20.0
    assert large["notional"] == 10_000.0
    assert large["S"] == "Buy"
    follow_path = tmp_path / "liquidation_followups.jsonl"
    rows = [json.loads(line) for line in follow_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTCUSDT"
    assert rows[0]["price_then"] == 80.0
    assert rows[0]["notional"] == 10_000.0
    assert rows[0]["horizons"]["1m"]["status"] == "wait"
    assert rows[0]["horizons"]["5m"]["status"] == "wait"
    assert rows[0]["horizons"]["15m"]["status"] == "wait"
    assert rows[0]["note"] == "Журнал только для разбора. Из этой строки ордер не ставится."
    summary = (tmp_path / "liquidation_followup_summary.txt").read_text(encoding="utf-8")
    assert "Всего записей: 1" in summary
    assert "ещё ждём 1" in summary


def _client(tmp_path: Path) -> BybitClient:
    client = BybitClient(
        "k",
        "s",
        share_rate_gate=False,
        liquidation_log_path=str(tmp_path / "liquidations.jsonl"),
    )
    client.place_order = _no_order  # type: ignore[method-assign]
    return client


@pytest.mark.asyncio
async def test_due_window_fills_from_fresh_ticker_without_order(tmp_path: Path):
    client = _client(tmp_path)
    calls = {"tickers": 0, "price": 0, "klines": 0}

    async def _tickers():
        calls["tickers"] += 1
        raise AssertionError("свежий список тикеров уже есть, новый запрос не нужен")

    async def _price(_symbol):
        calls["price"] += 1
        raise AssertionError("отдельная цена не нужна")

    async def _klines(*_args, **_kwargs):
        calls["klines"] += 1
        raise AssertionError("для свежего окна свечи не нужны")

    client.get_tickers = _tickers  # type: ignore[method-assign]
    client.get_price = _price  # type: ignore[method-assign]
    client.get_klines = _klines  # type: ignore[method-assign]
    client._tickers_snapshot = [{"symbol": "BTCUSDT", "lastPrice": "90"}]
    client._tickers_snapshot_at = time.monotonic()
    client._store_liquidation_event(
        {"s": "BTCUSDT", "S": "Buy", "v": "100", "p": "100", "T": _PRINT_MS}
    )
    await client._fill_due_followups(_PRINT_MS + 60_000)
    row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
    assert row["horizons"]["1m"]["status"] == "filled"
    assert row["horizons"]["1m"]["price"] == 90.0
    assert row["horizons"]["1m"]["direction"] == "down"
    assert row["horizons"]["1m"]["pct"] == -10.0
    assert row["horizons"]["1m"]["vs_print"] == "continued"
    assert row["horizons"]["1m"]["source"] == "last"
    assert row["horizons"]["5m"]["status"] == "wait"
    assert row["horizons"]["15m"]["status"] == "wait"
    assert calls == {"tickers": 0, "price": 0, "klines": 0}


@pytest.mark.asyncio
async def test_late_window_uses_minute_close_without_order(tmp_path: Path):
    client = _client(tmp_path)
    calls = {"tickers": 0, "price": 0}

    async def _tickers():
        calls["tickers"] += 1
        raise AssertionError("позднее окно не берёт текущую цену")

    async def _price(_symbol):
        calls["price"] += 1
        raise AssertionError("позднее окно не берёт текущую цену")

    print_ms = _PRINT_MS
    now_ms = print_ms + 10 * 60_000
    due_1m = print_ms + 60_000
    due_5m = print_ms + 5 * 60_000

    async def _klines(_symbol, interval="15", limit=200):
        assert interval == "1"
        return [
            {"timestamp": _bar_start(due_1m), "close": 80.0},
            {"timestamp": _bar_start(due_5m), "close": 120.0},
        ]

    client.get_tickers = _tickers  # type: ignore[method-assign]
    client.get_price = _price  # type: ignore[method-assign]
    client.get_klines = _klines  # type: ignore[method-assign]
    client._store_liquidation_event(
        {"s": "BTCUSDT", "S": "Buy", "v": "100", "p": "100", "T": print_ms}
    )
    await client._fill_due_followups(now_ms)
    row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
    assert row["horizons"]["1m"]["source"] == "kline_1m"
    assert row["horizons"]["1m"]["price"] == 80.0
    assert row["horizons"]["1m"]["vs_print"] == "continued"
    assert row["horizons"]["1m"]["pct"] == -20.0
    assert row["horizons"]["5m"]["source"] == "kline_1m"
    assert row["horizons"]["5m"]["price"] == 120.0
    assert row["horizons"]["5m"]["vs_print"] == "reversed"
    assert row["horizons"]["5m"]["pct"] == 20.0
    assert row["horizons"]["15m"]["status"] == "wait"
    assert calls == {"tickers": 0, "price": 0}


@pytest.mark.asyncio
async def test_kline_error_is_retried_then_closed_without_order(tmp_path: Path):
    client = _client(tmp_path)

    async def _klines(*_args, **_kwargs):
        raise RuntimeError("биржа не ответила")

    client.get_klines = _klines  # type: ignore[method-assign]
    client._store_liquidation_event(
        {"s": "ETHUSDT", "S": "Sell", "v": "50", "p": "200", "T": _PRINT_MS}
    )
    now_ms = _PRINT_MS + 10 * 60_000
    await client._fill_due_followups(now_ms)
    row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
    assert row["horizons"]["1m"]["status"] == "wait"
    assert row["horizons"]["1m"]["attempts"] == 1
    for _ in range(3):
        await client._fill_due_followups(now_ms)
    row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
    assert row["horizons"]["1m"]["status"] == "missed"
    assert row["S"] == "Sell"


@pytest.mark.asyncio
async def test_missing_late_price_is_marked_after_retries(tmp_path: Path):
    client = _client(tmp_path)

    async def _klines(*_args, **_kwargs):
        return []

    client.get_klines = _klines  # type: ignore[method-assign]
    client._store_liquidation_event(
        {"s": "BTCUSDT", "S": "Sell", "v": "100", "p": "100", "T": _PRINT_MS}
    )
    now_ms = _PRINT_MS + 10 * 60_000
    for _ in range(3):
        await client._fill_due_followups(now_ms)
        row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
        assert row["horizons"]["1m"]["status"] == "wait"
    await client._fill_due_followups(now_ms)
    row = json.loads((tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8"))
    assert row["horizons"]["1m"]["status"] == "missed"
    assert row["horizons"]["5m"]["status"] == "missed"
    assert row["horizons"]["15m"]["status"] == "wait"
    text = (tmp_path / "liquidation_followup_summary.txt").read_text(encoding="utf-8")
    assert "нет цены 1" in text


@pytest.mark.asyncio
async def test_pending_file_is_filled_on_startup_without_order(tmp_path: Path):
    path = tmp_path / "liquidations.jsonl"
    journal = LiquidationFollowupJournal.beside(path)
    now_ms = int(time.time() * 1000)
    assert journal.note_print(_row("BTCUSDT", "Buy", 100.0, 100.0, now_ms - 70_000)) is not None
    client = BybitClient("k", "s", share_rate_gate=False, liquidation_log_path=str(path))
    client.place_order = _no_order  # type: ignore[method-assign]

    async def _no_stream(_symbols):
        await asyncio.sleep(3600)

    async def _tickers():
        return [{"symbol": "BTCUSDT", "lastPrice": "101"}]

    async def _price(_symbol):
        raise AssertionError("список тикеров уже дал цену")

    client._liquidation_worker = _no_stream  # type: ignore[method-assign]
    client.get_tickers = _tickers  # type: ignore[method-assign]
    client.get_price = _price  # type: ignore[method-assign]
    try:
        await client.set_liquidation_symbols(["BTCUSDT"])
        filled = ""
        for _ in range(50):
            filled = (tmp_path / "liquidation_followups.jsonl").read_text(encoding="utf-8")
            if '"status": "filled"' in filled:
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError(filled)
        row = json.loads(filled)
        assert row["horizons"]["1m"]["price"] == 101.0
        assert row["horizons"]["1m"]["direction"] == "up"
        assert row["horizons"]["1m"]["vs_print"] == "reversed"
        assert row["horizons"]["5m"]["status"] == "wait"
    finally:
        await client.close()
