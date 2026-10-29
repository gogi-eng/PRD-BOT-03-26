#!/usr/bin/env python3
"""Unified Bybit v5 client with public market data and execution helpers."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

import aiohttp

from analysis.funding_filter import ticker_derivatives

try:
    from exchange.circuit_breaker import ApiCircuitBreaker
except ImportError:
    from circuit_breaker import ApiCircuitBreaker  # type: ignore

try:
    from exchange.liquidation_followup import LiquidationFollowupJournal, close_at, positive_price
except ImportError:
    from liquidation_followup import LiquidationFollowupJournal, close_at, positive_price  # type: ignore

logger = logging.getLogger("prd_agent.bybit")

_LIQUIDATION_LOG_MAX_BYTES = 8 * 1024 * 1024


class _RateGate:
    """Общая пауза между REST. Сессия aiohttp у каждого клиента своя."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.last_public = 0.0
        self.last_private = 0.0


_SHARED_RATE_GATE = _RateGate()


def limit_reset_sleep_sec(
    headers: Any,
    *,
    ret_code: int,
    now_ms: int,
    fallback_sec: float,
    max_sec: float = 120.0,
) -> float:
    """На retCode 10006 спать до X-Bapi-Limit-Reset-Timestamp, как в pybit 5.15."""
    if int(ret_code) != 10006:
        return float(fallback_sec)
    raw = None
    getter = getattr(headers, "get", None)
    if callable(getter):
        raw = getter("X-Bapi-Limit-Reset-Timestamp")
        if raw in (None, ""):
            raw = getter("x-bapi-limit-reset-timestamp")
    if raw in (None, ""):
        return float(fallback_sec)
    try:
        reset_ms = int(float(raw))
    except (TypeError, ValueError):
        return float(fallback_sec)
    delay = (reset_ms - int(now_ms)) / 1000.0
    if delay <= 0:
        return float(fallback_sec)
    return min(delay, float(max_sec))


def merge_liquidation_symbols(current: Tuple[str, ...], incoming: List[str], *, cap: int = 30) -> Tuple[str, ...]:
    """Старые символы остаются. Новый список только добавляет, пока не набран потолок."""
    merged: List[str] = []
    seen = set()
    for raw in list(current) + list(incoming or []):
        sym = str(raw or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(sym)
        if len(merged) >= cap:
            break
    return tuple(sorted(merged))


def _apply_orderbook_levels(side_map: Dict[str, str], updates: List[List[str]]) -> None:
    for item in updates or []:
        if not item or len(item) < 2:
            continue
        price = str(item[0])
        size = str(item[1])
        try:
            if float(size) == 0.0:
                side_map.pop(price, None)
            else:
                side_map[price] = size
        except (TypeError, ValueError):
            continue


def _orderbook_levels_sorted(side_map: Dict[str, str], *, bids: bool, limit: int) -> List[List[str]]:
    pairs = list(side_map.items())
    pairs.sort(key=lambda x: float(x[0]), reverse=bids)
    return [[p, s] for p, s in pairs[: max(1, limit)]]


class BybitClient:
    """Async Bybit v5 API Client."""

    BASE_URL = "https://api.bybit.com"
    TESTNET_URL = "https://api-testnet.bybit.com"
    WS_PUBLIC_URL = "wss://stream.bybit.com/v5/public/linear"
    WS_TESTNET_URL = "wss://stream-testnet.bybit.com/v5/public/linear"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        category: str = "linear",
        *,
        share_rate_gate: bool = True,
        liquidation_log_path: Optional[str] = None,
        liquidation_log_max_bytes: int = _LIQUIDATION_LOG_MAX_BYTES,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        self.ws_public_url = self.WS_TESTNET_URL if testnet else self.WS_PUBLIC_URL
        self.category = category
        self.recv_window = 20000
        self._session: Optional[aiohttp.ClientSession] = None
        self._liq_task: Optional[asyncio.Task] = None
        self._liq_symbols: Tuple[str, ...] = ()
        self._liquidation_cache: Dict[str, List[Dict]] = {}
        self._liquidation_log_path = Path(liquidation_log_path) if liquidation_log_path else Path("data") / "liquidations.jsonl"
        self._liquidation_log_max_bytes = max(1, int(liquidation_log_max_bytes))
        self._liquidation_log_lock = threading.Lock()
        self._liquidation_followups: Optional[LiquidationFollowupJournal] = None
        self._followup_task: Optional[asyncio.Task] = None
        self._share_rate_gate = bool(share_rate_gate)
        self._own_rate_gate = _RateGate()
        self.api_journal: Any = None
        self._tickers_snapshot: List[Dict] = []
        self._tickers_snapshot_at = 0.0
        self.tickers_ttl_sec = 30.0
        self._ob_task: Optional[asyncio.Task] = None
        self._ob_symbols: Tuple[str, ...] = ()
        self._ob_depth: int = 50
        self._ob_enabled: bool = True
        self._ob_max_age_sec: float = 5.0
        self._ob_books: Dict[str, Dict[str, Any]] = {}
        self.public_min_interval = 0.22
        self.private_min_interval = 0.35
        self.last_rate_limit_at_monotonic = 0.0
        self.circuit_breaker = ApiCircuitBreaker(failure_threshold=5, open_sec=45.0)
        self._retryable_http = frozenset({429, 500, 502, 503, 504})
        self._retryable_ret = frozenset({10006, 10016, 10018})

    def _rate_gate(self) -> _RateGate:
        if self._share_rate_gate:
            return _SHARED_RATE_GATE
        return self._own_rate_gate

    async def _respect_rate_limit(self, is_private: bool):
        gate = self._rate_gate()
        async with gate.lock:
            now = time.monotonic()
            min_interval = self.private_min_interval if is_private else self.public_min_interval
            last_at = gate.last_private if is_private else gate.last_public
            wait_for = min_interval - (now - last_at)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            updated = time.monotonic()
            if is_private:
                gate.last_private = updated
            else:
                gate.last_public = updated

    async def _backoff_shared(self, seconds: float) -> None:
        """Отодвигает общий лимитер, чтобы второй клиент не долбил API в ту же паузу."""
        delay = max(0.0, float(seconds))
        gate = self._rate_gate()
        async with gate.lock:
            horizon = time.monotonic() + delay
            gate.last_public = max(gate.last_public, horizon)
            gate.last_private = max(gate.last_private, horizon)
        if delay > 0:
            await asyncio.sleep(delay)

    def _record_rest(self, endpoint: str) -> None:
        journal = self.api_journal
        if journal is not None and hasattr(journal, "record"):
            journal.record(str(endpoint or "unknown"), cached=False)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self):
        await self.stop_liquidation_stream()
        await self.stop_orderbook_stream()
        await self._stop_followup_worker()
        if self._session and not self._session.closed:
            await self._session.close()

    def configure_orderbook_ws(
        self,
        *,
        enabled: bool = True,
        depth: int = 50,
        max_age_sec: float = 5.0,
    ) -> None:
        depth = max(1, min(int(depth), 200))
        self._ob_enabled = bool(enabled)
        self._ob_depth = depth
        self._ob_max_age_sec = max(0.5, float(max_age_sec))

    async def stop_liquidation_stream(self):
        if self._liq_task and not self._liq_task.done():
            self._liq_task.cancel()
            try:
                await self._liq_task
            except asyncio.CancelledError:
                pass
        self._liq_task = None
        self._liq_symbols = ()

    async def set_liquidation_symbols(self, symbols: List[str]):
        """Добавляет символы к уже открытой подписке. Короткий список сокет не рвёт."""
        self._ensure_followup_worker()
        normalized = merge_liquidation_symbols(self._liq_symbols, list(symbols or []))
        if set(normalized) <= set(self._liq_symbols):
            return
        await self.stop_liquidation_stream()
        if not normalized:
            return
        self._liq_symbols = normalized
        self._liq_task = asyncio.create_task(self._liquidation_worker(list(normalized)))

    async def _liquidation_worker(self, symbols: List[str]):
        args = [f"allLiquidation.{symbol}" for symbol in symbols]
        while tuple(sorted(symbols)) == self._liq_symbols:
            try:
                session = await self._get_session()
                async with session.ws_connect(self.ws_public_url, heartbeat=20, autoping=True) as ws:
                    await ws.send_json({"op": "subscribe", "args": args})
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(message.data)
                            if payload.get("topic", "").startswith("allLiquidation."):
                                for item in payload.get("data", []):
                                    self._store_liquidation_event(item)
                        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[BYBIT] liquidation stream error: {exc}")
                await asyncio.sleep(5)

    def _store_liquidation_event(self, payload: Dict):
        symbol = payload.get("s")
        if not symbol:
            return
        price = float(payload.get("p", 0.0) or 0.0)
        size = float(payload.get("v", 0.0) or 0.0)
        side = payload.get("S", "")
        ts = int(payload.get("T", int(time.time() * 1000)) or 0)
        event = {
            "price": price,
            "size": size,
            "side": side,
            "timestamp": ts,
        }
        bucket = self._liquidation_cache.setdefault(symbol, [])
        bucket.append(event)
        if len(bucket) > 400:
            del bucket[:-400]
        # Журнал только для разбора. Из этой строки ордер не ставится.
        journal_row = {
            "symbol": str(symbol),
            "S": side,
            "v": size,
            "p": price,
            "T": ts,
            "notional": price * size,
        }
        self._append_liquidation_journal(journal_row)
        self._note_large_liquidation_followup(journal_row)

    def _rotate_liquidation_log(self, path: Path) -> None:
        try:
            if not path.is_file():
                return
            if path.stat().st_size < self._liquidation_log_max_bytes:
                return
            backup = path.with_name(path.name + ".1")
            if backup.exists():
                backup.unlink()
            path.replace(backup)
        except OSError as exc:
            logger.warning("liquidation journal rotate: %s", exc)

    def _append_liquidation_journal(self, row: Dict[str, Any]) -> None:
        path = self._liquidation_log_path
        try:
            line = json.dumps(row, ensure_ascii=False) + "\n"
            with self._liquidation_log_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_liquidation_log(path)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except OSError as exc:
            logger.warning("liquidation journal: %s", exc)

    def _followups(self) -> LiquidationFollowupJournal:
        journal = self._liquidation_followups
        if journal is None:
            journal = LiquidationFollowupJournal.beside(self._liquidation_log_path)
            self._liquidation_followups = journal
        return journal

    def _note_large_liquidation_followup(self, row: Dict[str, Any]) -> None:
        """Крупная печать уходит в отдельный разбор цены. Ордер отсюда не ставится."""
        try:
            opened = self._followups().note_print(row)
        except Exception as exc:
            logger.warning("liquidation follow-up: %s", exc)
            return
        if opened:
            self._ensure_followup_worker()

    def _ensure_followup_worker(self) -> None:
        task = self._followup_task
        if task is not None and not task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        try:
            pending = self._followups().has_pending()
        except Exception as exc:
            logger.warning("liquidation follow-up: %s", exc)
            return
        if not pending:
            return
        self._followup_task = loop.create_task(self._followup_worker())

    async def _stop_followup_worker(self) -> None:
        task = self._followup_task
        self._followup_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _followup_worker(self) -> None:
        """Дописывает цену через 1, 5 и 15 минут. Новых сделок не открывает."""
        try:
            while True:
                try:
                    journal = self._followups()
                    now_ms = int(time.time() * 1000)
                    if journal.due_slots(now_ms):
                        await self._fill_due_followups()
                    now_ms = int(time.time() * 1000)
                    if not journal.has_pending():
                        await asyncio.sleep(30.0)
                        continue
                    wait = journal.seconds_until_next(now_ms)
                    if wait is None:
                        await asyncio.sleep(30.0)
                        continue
                    if journal.due_slots(now_ms):
                        wait = 15.0
                    await asyncio.sleep(min(max(float(wait), 1.0), 30.0))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("liquidation follow-up")
                    await asyncio.sleep(15.0)
        except asyncio.CancelledError:
            raise

    def _recent_ticker_rows(self, max_age_sec: float = 5.0) -> Optional[List[Dict]]:
        if not self._tickers_snapshot:
            return None
        age = time.monotonic() - self._tickers_snapshot_at
        if age > float(max_age_sec):
            return None
        return list(self._tickers_snapshot)

    async def _followup_last_prices(self, symbols: List[str]) -> Dict[str, float]:
        wanted: List[str] = []
        seen = set()
        for raw in symbols:
            sym = str(raw or "").upper()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            wanted.append(sym)
        found: Dict[str, float] = {}
        rows = self._recent_ticker_rows()
        if rows is None:
            try:
                rows = await self.get_tickers()
            except Exception as exc:
                logger.warning("liquidation follow-up tickers: %s", exc)
                rows = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol", "") or "").upper()
            if sym not in seen:
                continue
            price = positive_price(row.get("lastPrice"))
            if price is not None:
                found[sym] = price
        for sym in wanted:
            if sym in found:
                continue
            try:
                price = positive_price(await self.get_price(sym))
            except Exception as exc:
                logger.warning("liquidation follow-up price %s: %s", sym, exc)
                price = None
            if price is not None:
                found[sym] = price
        return found

    async def _followup_minute_closes(self, symbol: str, limit: int) -> Optional[List[Tuple[int, float]]]:
        try:
            rows = await self.get_klines(symbol, interval="1", limit=limit)
        except Exception as exc:
            logger.warning("liquidation follow-up kline %s: %s", symbol, exc)
            return None
        candles: List[Tuple[int, float]] = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                start = int(row.get("timestamp") or 0)
            except (TypeError, ValueError):
                continue
            close = positive_price(row.get("close"))
            if start > 0 and close is not None:
                candles.append((start, close))
        return candles

    async def _fill_due_followups(self, now_ms: Optional[int] = None) -> None:
        journal = self._followups()
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        slots = journal.due_slots(now_ms)
        if not slots:
            return
        live_symbols = [str(slot["symbol"]) for slot in slots if not slot["late"]]
        prices: Dict[str, float] = {}
        if live_symbols:
            prices = await self._followup_last_prices(live_symbols)
        late_due: Dict[str, int] = {}
        for slot in slots:
            if not slot["late"]:
                continue
            sym = str(slot["symbol"])
            due = int(slot["due_ms"])
            previous = late_due.get(sym)
            if previous is None or due < previous:
                late_due[sym] = due
        candles_by_symbol: Dict[str, Optional[List[Tuple[int, float]]]] = {}
        for sym, due in late_due.items():
            span = max(0, now_ms - due)
            limit = min(1000, max(20, span // 60_000 + 3))
            candles_by_symbol[sym] = await self._followup_minute_closes(sym, limit)
        for slot in slots:
            sym = str(slot["symbol"])
            key = str(slot["key"])
            record_id = str(slot["id"])
            if slot["late"]:
                candles = candles_by_symbol.get(sym)
                if candles is None:
                    journal.note_late_miss(record_id, key)
                    continue
                price = close_at(candles, int(slot["due_ms"]))
                if price is None:
                    journal.note_late_miss(record_id, key)
                    continue
                journal.fill(record_id, key, price, now_ms, source="kline_1m")
                continue
            price = prices.get(sym)
            if price is None:
                continue
            journal.fill(record_id, key, price, now_ms, source="last")

    def get_liquidation_events(self, symbol: str, max_age_sec: int = 3600, limit: int = 250) -> List[Dict]:
        now_ms = int(time.time() * 1000)
        cutoff = now_ms - max_age_sec * 1000
        events = [item for item in self._liquidation_cache.get(symbol, []) if item.get("timestamp", 0) >= cutoff]
        return events[-limit:]

    async def stop_orderbook_stream(self):
        if self._ob_task and not self._ob_task.done():
            self._ob_task.cancel()
            try:
                await self._ob_task
            except asyncio.CancelledError:
                pass
        self._ob_task = None
        self._ob_symbols = ()

    async def set_orderbook_symbols(self, symbols: List[str]):
        if not self._ob_enabled:
            return
        normalized = tuple(sorted({str(s).upper() for s in symbols if s})[:30])
        if normalized == self._ob_symbols:
            return
        await self.stop_orderbook_stream()
        if not normalized:
            return
        self._ob_symbols = normalized
        self._ob_task = asyncio.create_task(self._orderbook_worker(list(normalized)))

    def _store_orderbook_message(self, payload: Dict[str, Any]) -> None:
        topic = str(payload.get("topic", "") or "")
        if not topic.startswith("orderbook."):
            return
        msg_type = str(payload.get("type", "") or "")
        data = payload.get("data") or {}
        symbol = str(data.get("s", "") or "").upper()
        if not symbol:
            parts = topic.split(".")
            if len(parts) >= 3:
                symbol = parts[-1].upper()
        if not symbol:
            return
        book = self._ob_books.setdefault(
            symbol,
            {"bids": {}, "asks": {}, "ts": 0, "seq": 0},
        )
        if msg_type == "snapshot":
            book["bids"] = {str(p): str(s) for p, s in (data.get("b") or [])}
            book["asks"] = {str(p): str(s) for p, s in (data.get("a") or [])}
        elif msg_type == "delta":
            _apply_orderbook_levels(book["bids"], data.get("b") or [])
            _apply_orderbook_levels(book["asks"], data.get("a") or [])
        else:
            return
        book["seq"] = int(data.get("seq", book.get("seq", 0)) or 0)
        book["ts"] = int(payload.get("ts", data.get("ts", 0)) or int(time.time() * 1000))

    def _orderbook_from_cache(self, symbol: str, limit: int) -> Optional[Dict[str, Any]]:
        if not self._ob_enabled:
            return None
        sym = str(symbol or "").upper()
        book = self._ob_books.get(sym)
        if not book or not book.get("bids") or not book.get("asks"):
            return None
        ts = int(book.get("ts", 0) or 0)
        if ts <= 0:
            return None
        age_ms = int(time.time() * 1000) - ts
        if age_ms > self._ob_max_age_sec * 1000:
            return None
        return {
            "bids": _orderbook_levels_sorted(book["bids"], bids=True, limit=limit),
            "asks": _orderbook_levels_sorted(book["asks"], bids=False, limit=limit),
            "ts": ts,
            "source": "ws",
        }

    def peek_orderbook(self, symbol: str, limit: int = 50) -> Optional[Dict[str, Any]]:
        """Только свежая книга WebSocket. Если её нет — None, без REST."""
        return self._orderbook_from_cache(str(symbol or "").upper(), limit)

    async def _orderbook_worker(self, symbols: List[str]):
        depth = self._ob_depth
        args = [f"orderbook.{depth}.{symbol}" for symbol in symbols]
        while tuple(sorted(symbols)) == self._ob_symbols:
            try:
                session = await self._get_session()
                async with session.ws_connect(self.ws_public_url, heartbeat=20, autoping=True) as ws:
                    await ws.send_json({"op": "subscribe", "args": args})
                    async for message in ws:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(message.data)
                            if str(payload.get("topic", "")).startswith("orderbook."):
                                self._store_orderbook_message(payload)
                        elif message.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                print(f"[BYBIT] orderbook stream error: {exc}")
                await asyncio.sleep(5)

    def _generate_signature(self, params: str, timestamp: str) -> str:
        payload = f"{timestamp}{self.api_key}{self.recv_window}{params}"
        return hmac.new(self.api_secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _get_headers(self, params: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(params, timestamp)
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window),
            "Content-Type": "application/json",
        }

    async def _read_response_json(self, response: aiohttp.ClientResponse) -> Dict:
        try:
            data = await response.json()
            return data if isinstance(data, dict) else {"_error": "Invalid response"}
        except Exception:
            text = await response.text()
            return {"_error": "Non-JSON", "_raw": text[:200]}

    def _note_exchange_response(self, endpoint: str, response: Any) -> Optional[str]:
        self._record_rest(endpoint)
        headers = getattr(response, "headers", None)
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return None
        raw = getter("X-Bapi-Limit-Reset-Timestamp")
        if raw in (None, ""):
            raw = getter("x-bapi-limit-reset-timestamp")
        if raw in (None, ""):
            return None
        return str(raw)

    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, private: bool = False, signed: bool = False, retries: int = 4, return_full: bool = False) -> Optional[Dict]:
        is_private = private or signed
        if self.circuit_breaker.is_open():
            wait = self.circuit_breaker.seconds_until_retry()
            return {
                "_error": f"Bybit circuit open, retry in {wait:.0f}s",
                "_code": "circuit_open",
            }

        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"

        for attempt in range(retries):
            reset_header: Optional[str] = None
            try:
                await self._respect_rate_limit(is_private)
                if method == "GET":
                    items = sorted(
                        (str(k), str(v))
                        for k, v in (params or {}).items()
                        if v is not None and v != ""
                    )
                    query = urlencode(items, quote_via=quote)
                    headers = self._get_headers(query) if is_private else {}
                    full_url = f"{url}?{query}" if query else url
                    async with session.get(
                        full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                    ) as response:
                        reset_header = self._note_exchange_response(endpoint, response)
                        if response.status in self._retryable_http and attempt < retries - 1:
                            self.circuit_breaker.record_failure()
                            wait = 2 ** (attempt + 1)
                            print(
                                f"[BYBIT] HTTP {response.status}, retry in {wait}s "
                                f"({attempt + 1}/{retries})"
                            )
                            await asyncio.sleep(wait)
                            continue
                        data = await self._read_response_json(response)
                else:
                    body = json.dumps(params or {})
                    headers = self._get_headers(body) if is_private else {"Content-Type": "application/json"}
                    async with session.post(
                        url, headers=headers, data=body, timeout=aiohttp.ClientTimeout(total=20)
                    ) as response:
                        reset_header = self._note_exchange_response(endpoint, response)
                        if response.status in self._retryable_http and attempt < retries - 1:
                            self.circuit_breaker.record_failure()
                            wait = 2 ** (attempt + 1)
                            print(
                                f"[BYBIT] HTTP {response.status}, retry in {wait}s "
                                f"({attempt + 1}/{retries})"
                            )
                            await asyncio.sleep(wait)
                            continue
                        data = await self._read_response_json(response)

                if data.get("_error") and "_code" not in data:
                    self.circuit_breaker.record_failure()
                    return data

                ret_code_early = data.get("retCode")
                if ret_code_early == 10006 and attempt < retries - 1:
                    self.circuit_breaker.record_failure()
                    self.last_rate_limit_at_monotonic = time.monotonic()
                    fallback = float(2 ** (attempt + 1))
                    wait = limit_reset_sleep_sec(
                        {"X-Bapi-Limit-Reset-Timestamp": reset_header} if reset_header else {},
                        ret_code=10006,
                        now_ms=int(time.time() * 1000),
                        fallback_sec=fallback,
                    )
                    print(f"[BYBIT] retCode 10006, retry in {wait}s ({attempt + 1}/{retries})")
                    await self._backoff_shared(wait)
                    continue

                if return_full:
                    if data.get("retCode") == 0:
                        self.circuit_breaker.record_success()
                    return data

                if data.get("retCode") == 0:
                    self.circuit_breaker.record_success()
                    return data.get("result", {})

                ret_code = data.get("retCode")
                error_msg = data.get("retMsg", "Unknown error")
                print(f"[BYBIT] API error: {error_msg} (code: {ret_code})")
                if ret_code == 110043:
                    self.circuit_breaker.record_success()
                    return data.get("result", {})
                if ret_code in self._retryable_ret and attempt < retries - 1:
                    self.circuit_breaker.record_failure()
                    self.last_rate_limit_at_monotonic = time.monotonic()
                    fallback = float(2 ** (attempt + 1))
                    wait = limit_reset_sleep_sec(
                        {"X-Bapi-Limit-Reset-Timestamp": reset_header} if reset_header else {},
                        ret_code=int(ret_code),
                        now_ms=int(time.time() * 1000),
                        fallback_sec=fallback,
                    )
                    print(f"[BYBIT] retCode {ret_code}, retry in {wait}s ({attempt + 1}/{retries})")
                    await self._backoff_shared(wait)
                    continue
                self.circuit_breaker.record_failure()
                return {"_error": error_msg, "_code": ret_code}
            except aiohttp.ClientError as exc:
                self.circuit_breaker.record_failure()
                print(f"[BYBIT] Request error {attempt + 1}/{retries}: {exc}")
                if attempt < retries - 1:
                    await asyncio.sleep(2 ** (attempt + 1))
            except Exception as exc:
                self.circuit_breaker.record_failure()
                print(f"[BYBIT] Unexpected error: {exc}")
                return None
        return None

    # === PUBLIC ===

    async def get_price(self, symbol: str) -> float:
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            return float(result["list"][0].get("lastPrice", 0))
        return 0.0

    async def get_klines(self, symbol: str, interval: str = "15", limit: int = 200) -> List[Dict]:
        result = await self._request("GET", "/v5/market/kline", {
            "category": self.category, "symbol": symbol, "interval": interval, "limit": limit,
        })
        if result and result.get("list"):
            klines = []
            for k in reversed(result["list"]):
                klines.append({
                    "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                })
            return klines
        return []

    def fresh_tickers(self) -> Optional[List[Dict]]:
        """Последний полный список тикеров, если он моложе TTL. Без HTTP."""
        if not self._tickers_snapshot:
            return None
        age = time.monotonic() - self._tickers_snapshot_at
        if age > float(self.tickers_ttl_sec):
            return None
        return list(self._tickers_snapshot)

    def _remember_tickers(self, rows: List[Dict]) -> None:
        self._tickers_snapshot = list(rows)
        self._tickers_snapshot_at = time.monotonic()

    async def get_tickers(self) -> List[Dict]:
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category})
        if result and result.get("list"):
            self._remember_tickers(result["list"])
            return list(self._tickers_snapshot)
        return []

    async def get_ticker(self, symbol: str) -> Optional[Dict]:
        """Single-symbol ticker (lightweight vs downloading all category tickers)."""
        result = await self._request(
            "GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol}
        )
        if result and result.get("list"):
            lst = result["list"]
            if lst:
                return lst[0]
        return None

    async def get_orderbook(
        self,
        symbol: str,
        limit: int = 50,
        *,
        lazy: bool = False,
        signal_passed_cheap_filters: bool = True,
    ) -> Dict:
        # Аргументы адаптера. Сырой клиент их не отбрасывает: иначе SPIKE-проверка
        # стакана падает TypeError и защита «не гнаться за импульсом» молчит.
        del lazy, signal_passed_cheap_filters
        sym = str(symbol or "").upper()
        cached = self._orderbook_from_cache(sym, limit)
        if cached:
            return cached
        result = await self._request(
            "GET",
            "/v5/market/orderbook",
            {"category": self.category, "symbol": sym, "limit": limit},
        )
        if result:
            return {
                "bids": result.get("b", []),
                "asks": result.get("a", []),
                "ts": int(result.get("ts", 0) or 0),
                "source": "rest",
            }
        return {"bids": [], "asks": [], "ts": 0, "source": "rest"}

    async def get_recent_trades(
        self,
        symbol: str,
        limit: int = 100,
        *,
        lazy: bool = False,
        signal_passed_cheap_filters: bool = True,
    ) -> List[Dict]:
        del lazy, signal_passed_cheap_filters
        result = await self._request("GET", "/v5/market/recent-trade", {"category": self.category, "symbol": symbol, "limit": limit})
        if result and result.get("list"):
            trades = []
            for item in result["list"]:
                trades.append(
                    {
                        "price": float(item.get("price", 0.0)),
                        "size": float(item.get("size", 0.0)),
                        "side": item.get("side", ""),
                        "timestamp": int(item.get("time", 0) or 0),
                    }
                )
            return trades
        return []

    async def get_instrument_info(self, symbol: str) -> Optional[Dict]:
        result = await self._request("GET", "/v5/market/instruments-info", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            inst = result["list"][0]
            lot = inst.get("lotSizeFilter", {}) or {}
            return {
                "min_qty": float(lot.get("minOrderQty", 1)),
                "qty_step": float(lot.get("qtyStep", 1)),
                "price_step": float(inst.get("priceFilter", {}).get("tickSize", 0.0001)),
                "min_notional": float(lot.get("minNotionalValue", 5.0) or 5.0),
            }
        return None

    async def get_max_leverage(self, symbol: str) -> int:
        result = await self._request("GET", "/v5/market/instruments-info", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            return int(float(result["list"][0].get("leverageFilter", {}).get("maxLeverage", 100)))
        return 100

    # === PRIVATE ===

    @staticmethod
    def _position_size(p: Dict) -> float:
        for key in ("size", "qty", "positionQty"):
            val = float(p.get(key, 0) or 0)
            if val > 0:
                return val
        avg = float(p.get("avgPrice", 0) or p.get("entryPrice", 0) or 0)
        pval = float(p.get("positionValue", 0) or 0)
        if pval > 0 and avg > 0:
            return pval / avg
        return 0.0

    async def get_positions(self, symbol: str = None) -> List[Dict]:
        params = {"category": self.category, "settleCoin": "USDT", "limit": 200}
        if symbol:
            params["symbol"] = str(symbol).upper()
        result = await self._request("GET", "/v5/position/list", params, private=True)
        if result and result.get("list"):
            return [p for p in result["list"] if self._position_size(p) > 0]
        return []

    async def has_open_position(self, symbol: str) -> bool:
        rows = await self.get_positions(symbol)
        return len(rows) > 0

    async def get_balance(self) -> float:
        """USDT на счёте: walletBalance, иначе equity (если API отдал пустой wallet)."""
        snap = await self.get_wallet_snapshot()
        wallet = float(snap.get("wallet_balance", 0) or 0)
        equity = float(snap.get("equity", 0) or 0)
        available = float(snap.get("available_balance", 0) or 0)
        if wallet > 0:
            return wallet
        if equity > 0:
            logger.warning(
                "wallet_balance=0, fallback equity=%.4f available=%.4f err=%s",
                equity,
                available,
                snap.get("_error") or "",
            )
            return equity
        if available > 0:
            logger.warning(
                "wallet_balance=0 equity=0, fallback available=%.4f err=%s",
                available,
                snap.get("_error") or "",
            )
            return available
        if snap.get("_error"):
            logger.warning(
                "get_balance=0: wallet API error %s code=%s",
                snap.get("_error"),
                snap.get("_code"),
            )
        else:
            logger.warning(
                "get_balance=0: empty wallet snapshot equity=%.4f available=%.4f",
                equity,
                available,
            )
        return 0.0

    async def get_usdt_available_balance(self) -> float:
        """Алиас для telegram_signal_agent и старых вызовов."""
        return await self.get_available_balance()

    async def get_available_balance(self) -> float:
        """Свободная маржа для нового ордера (не walletBalance — он включает занятую маржу)."""
        snap = await self.get_wallet_snapshot()
        avail = float(snap.get("available_balance", 0) or 0)
        if avail > 0:
            return avail
        # Если available пустой, но equity/wallet есть — не блокируем сайзинг нулём молча
        wallet = float(snap.get("wallet_balance", 0) or 0)
        equity = float(snap.get("equity", 0) or 0)
        fallback = wallet if wallet > 0 else equity
        if fallback > 0 and snap.get("_error"):
            logger.warning(
                "available_balance=0 after API error, fallback=%.4f", fallback
            )
        return fallback if fallback > 0 else 0.0

    async def get_wallet_snapshot(self) -> Dict[str, Any]:
        """Снимок UNIFIED-кошелька. При ошибке API — retry и поля _error/_code."""
        out: Dict[str, Any] = {
            "wallet_balance": 0.0,
            "available_balance": 0.0,
            "equity": 0.0,
        }
        result = await self._request(
            "GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}, private=True
        )
        if isinstance(result, dict) and result.get("_error"):
            logger.warning(
                "wallet-balance API error: %s code=%s — retry once",
                result.get("_error"),
                result.get("_code"),
            )
            await asyncio.sleep(0.6)
            result = await self._request(
                "GET",
                "/v5/account/wallet-balance",
                {"accountType": "UNIFIED"},
                private=True,
            )
        if isinstance(result, dict) and result.get("_error"):
            out["_error"] = str(result.get("_error") or "wallet API error")
            out["_code"] = result.get("_code")
            logger.warning(
                "wallet-balance failed after retry: %s code=%s",
                out["_error"],
                out["_code"],
            )
            return out
        if not result or not isinstance(result, dict) or not result.get("list"):
            logger.warning(
                "wallet-balance empty result keys=%s",
                list(result.keys()) if isinstance(result, dict) else type(result),
            )
            out["_error"] = "empty_wallet_list"
            return out
        acc = result["list"][0]
        total_avail = float(acc.get("totalAvailableBalance", 0) or 0)
        if total_avail > 0:
            out["available_balance"] = total_avail
        out["equity"] = float(acc.get("totalEquity", 0) or acc.get("totalWalletBalance", 0) or 0)
        for coin in acc.get("coin", []):
            if coin.get("coin") != "USDT":
                continue
            out["wallet_balance"] = float(coin.get("walletBalance", 0) or 0)
            if out["available_balance"] <= 0:
                for key in ("availableToWithdraw", "availableBalance", "equity", "usdValue"):
                    val = float(coin.get(key, 0) or 0)
                    if val > 0:
                        out["available_balance"] = val
                        break
            # Иногда Bybit на рестарте отдаёт walletBalance=0 при ненулевом equity/usdValue
            if out["wallet_balance"] <= 0:
                for key in ("equity", "usdValue", "walletBalance"):
                    val = float(coin.get(key, 0) or 0)
                    if val > 0:
                        out["wallet_balance"] = val
                        break
            break
        if out["wallet_balance"] <= 0 and out["equity"] > 0:
            out["wallet_balance"] = out["equity"]
        if out["available_balance"] <= 0 and out["wallet_balance"] > 0:
            out["available_balance"] = out["wallet_balance"]
        return out


    async def ensure_one_way_mode(self, *, coin: str = "USDT", probe_symbol: str = "BTCUSDT") -> Dict[str, Any]:
        """Принудительно one-way (Merged Single). Без hedge positionIdx 1/2.

        Bybit hedge на субаккаунте даёт retCode=10001 «position idx not match…»,
        если бот шлёт ордер без hedge-idx. Мы всегда торгуем в one-way → mode=0.
        """
        out: Dict[str, Any] = {"ok": False, "mode": "unknown", "ret_code": None, "ret_msg": ""}
        attempts = [
            {"category": self.category, "coin": str(coin).upper(), "mode": 0},
            {"category": self.category, "symbol": str(probe_symbol).upper(), "mode": 0},
        ]
        for params in attempts:
            full = await self._request(
                "POST",
                "/v5/position/switch-mode",
                params,
                private=True,
                return_full=True,
            )
            last_full = full if isinstance(full, dict) else None
            code = int((last_full or {}).get("retCode", -1) or -1)
            msg = str((last_full or {}).get("retMsg", "") or "")
            out["ret_code"] = code
            out["ret_msg"] = msg
            # 0 = switched; 110025 = already one-way / not modified
            if code in (0, 110025):
                break

        probe = await self._request(
            "GET",
            "/v5/position/list",
            {"category": self.category, "symbol": str(probe_symbol).upper()},
            private=True,
        )
        rows = (probe or {}).get("list") or []
        idxs = {int(r.get("positionIdx") or 0) for r in rows}
        if idxs & {1, 2}:
            out["mode"] = "hedge"
            out["ok"] = False
            logger.warning(
                "Bybit still HEDGE after switch-mode (idxs=%s ret=%s %s) — orders may fail 10001",
                sorted(idxs),
                out.get("ret_code"),
                out.get("ret_msg"),
            )
        else:
            out["mode"] = "one_way"
            out["ok"] = True
            logger.info(
                "Bybit position mode ONE-WAY (no hedge idx); switch ret=%s %s",
                out.get("ret_code"),
                out.get("ret_msg") or "ok",
            )
        return out

    async def place_order(self, symbol: str, side: str, qty: float,
                          order_type: str = "Market", price: float = None,
                          stop_loss: float = None, take_profit: float = None,
                          reduce_only: bool = False, time_in_force: str = "GTC",
                          position_idx: int = 0) -> Dict:
        params = {
            "category": self.category, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": str(qty), "timeInForce": time_in_force,
        }
        if price and order_type == "Limit":
            params["price"] = str(price)
        if stop_loss:
            params["stopLoss"] = str(stop_loss)
        if take_profit:
            params["takeProfit"] = str(take_profit)
        if reduce_only:
            params["reduceOnly"] = True
        # Всегда one-way: hedge idx 1/2 запрещён (retCode 10001 на one-way счёте).
        if int(position_idx or 0) != 0:
            logger.warning(
                "place_order %s: ignore hedge position_idx=%s → force 0 (one-way)",
                symbol,
                position_idx,
            )
        params["positionIdx"] = 0

        result = await self._request("POST", "/v5/order/create", params, private=True)
        if not result:
            return {"success": False, "orderId": "", "error": "Empty response from Bybit (network or timeout)"}
        if result.get("_error"):
            code = result.get("_code", "")
            msg = result.get("_error", "Order failed")
            detail = f"{msg}" + (f" (retCode={code})" if code != "" else "")
            return {"success": False, "orderId": "", "error": detail}
        return {"success": True, "orderId": result.get("orderId", ""), "error": ""}

    async def get_order_status(self, symbol: str, order_id: str) -> Dict:
        result = await self._request("GET", "/v5/order/realtime", {"category": self.category, "symbol": symbol, "orderId": order_id}, private=True)
        if result and result.get("list"):
            order = result["list"][0]
            return {
                "status": order.get("orderStatus", "Unknown"),
                "filled_qty": float(order.get("cumExecQty", 0.0)),
                "avg_price": float(order.get("avgPrice", 0.0) or 0.0),
            }
        return {"status": "Unknown", "filled_qty": 0.0, "avg_price": 0.0}

    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        result = await self._request("POST", "/v5/order/cancel", {"category": self.category, "symbol": symbol, "orderId": order_id}, private=True)
        return bool(result and not result.get("_error"))

    async def update_stop_loss(self, symbol: str, stop_loss: float, position_idx: int = 0) -> Dict:
        # one-way only — hedge idx ломает trading-stop на Merged Single
        position_idx = 0
        params = {
            "category": self.category, "symbol": symbol,
            "stopLoss": str(stop_loss), "positionIdx": position_idx,
            "slTriggerBy": "MarkPrice",
        }
        result = await self._request("POST", "/v5/position/trading-stop", params, private=True, return_full=True)
        if result and isinstance(result, dict) and result.get("retCode") == 0:
            return {"success": True, "error": ""}
        error = result.get("retMsg", "Failed") if isinstance(result, dict) else "Empty response"
        return {"success": False, "error": error}

    async def update_take_profit(self, symbol: str, take_profit: float, position_idx: int = 0) -> Dict:
        position_idx = 0
        params = {
            "category": self.category, "symbol": symbol,
            "takeProfit": str(take_profit), "positionIdx": position_idx,
            "tpTriggerBy": "MarkPrice",
        }
        result = await self._request("POST", "/v5/position/trading-stop", params, private=True, return_full=True)
        if result and isinstance(result, dict) and result.get("retCode") == 0:
            return {"success": True, "error": ""}
        error = result.get("retMsg", "Failed") if isinstance(result, dict) else "Empty response"
        return {"success": False, "error": error}

    async def close_position(self, symbol: str, side: str, qty: float = None, position_idx: int = 0) -> Dict:
        close_side = "Sell" if side.upper() in ["BUY", "LONG"] else "Buy"
        if qty is None:
            positions = await self.get_positions(symbol)
            if positions:
                qty = float(positions[0].get("size", 0))
            else:
                return {"success": False, "orderId": "", "error": "Position not found"}
        return await self.place_order(symbol=symbol, side=close_side, qty=qty, order_type="Market", reduce_only=True, position_idx=0)

    async def get_symbol_leverage(self, symbol: str) -> int:
        """Фактическое плечо по символу с биржи (не «желаемое» супервизора)."""
        sym = str(symbol).upper()
        result = await self._request(
            "GET",
            "/v5/position/list",
            {"category": self.category, "symbol": sym},
            private=True,
        )
        if isinstance(result, dict) and result.get("_error"):
            return 0
        if result and result.get("list"):
            for row in result["list"]:
                lev = int(float(row.get("leverage", 0) or 0))
                if lev > 0:
                    return lev
        return 0

    async def set_leverage(self, symbol: str, leverage: int) -> Dict[str, Any]:
        """Устаревший bool заменён на dict — см. set_leverage_verified."""
        return await self.set_leverage_verified(symbol, leverage)

    async def set_leverage_verified(self, symbol: str, leverage: int) -> Dict[str, Any]:
        sym = str(symbol).upper()
        requested = max(1, int(leverage))
        max_inst = await self.get_max_leverage(sym)
        target = min(requested, max_inst)

        params = {
            "category": self.category,
            "symbol": sym,
            "buyLeverage": str(target),
            "sellLeverage": str(target),
        }
        full = await self._request(
            "POST",
            "/v5/position/set-leverage",
            params,
            private=True,
            return_full=True,
        )
        applied = await self.get_symbol_leverage(sym)

        if not isinstance(full, dict):
            return {
                "success": False,
                "requested": requested,
                "target": target,
                "applied": applied,
                "max_instrument": max_inst,
                "error": "пустой ответ Bybit set-leverage",
            }

        ret_code = full.get("retCode")
        ret_msg = str(full.get("retMsg", "") or "")

        if ret_code == 0:
            if applied <= 0:
                applied = target
            ok = applied <= target or abs(applied - target) <= 1
            if applied < requested:
                return {
                    "success": ok,
                    "requested": requested,
                    "target": target,
                    "applied": applied,
                    "max_instrument": max_inst,
                    "error": (
                        f"лимит аккаунта/пары: на бирже {applied}x "
                        f"(запрос {requested}x)"
                    )
                    if applied < requested
                    else "",
                }
            return {
                "success": True,
                "requested": requested,
                "target": target,
                "applied": applied,
                "max_instrument": max_inst,
                "error": "",
            }

        # 110043 — плечо не изменилось (уже стоит другое значение)
        if ret_code == 110043:
            if applied <= 0:
                applied = target
            return {
                "success": applied > 0,
                "requested": requested,
                "target": target,
                "applied": applied,
                "max_instrument": max_inst,
                "error": (
                    f"Bybit 110043: фактически {applied}x (запрос {requested}x)"
                    if applied != requested
                    else ""
                ),
            }

        if full.get("_error"):
            ret_msg = str(full.get("_error"))

        return {
            "success": False,
            "requested": requested,
            "target": target,
            "applied": applied,
            "max_instrument": max_inst,
            "error": ret_msg or f"set-leverage retCode={ret_code}",
        }

    async def get_closed_pnl(self, symbol: str = None, limit: int = 50) -> List[Dict]:
        rows, _ = await self.get_closed_pnl_page(symbol=symbol, limit=limit)
        return rows

    async def get_closed_pnl_page(
        self,
        *,
        symbol: Optional[str] = None,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: int = 50,
    ) -> Tuple[List[Dict], str]:
        """Закрытый PnL с окном времени (для отчётов 2ч / 24ч)."""
        params: Dict[str, Any] = {
            "category": self.category,
            "limit": min(100, max(1, int(limit))),
        }
        if symbol:
            params["symbol"] = symbol
        if start_time_ms is not None:
            params["startTime"] = int(start_time_ms)
        if end_time_ms is not None:
            params["endTime"] = int(end_time_ms)
        if cursor:
            params["cursor"] = cursor
        result = await self._request("GET", "/v5/position/closed-pnl", params, private=True)
        if not result:
            return [], ""
        rows = list(result.get("list") or [])
        next_cursor = str(result.get("nextPageCursor") or "")
        return rows, next_cursor

    def _funding_from_rows(self, symbol: str, rows: List[Dict]) -> Optional[Dict]:
        sym = str(symbol or "").upper()
        for row in rows:
            if str(row.get("symbol", "")).upper() == sym:
                return ticker_derivatives(row)
        return None

    async def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        """Funding и OI одной стороны. Свежий get_tickers() не дёргает HTTP ещё раз."""
        cached = self.fresh_tickers()
        if cached:
            found = self._funding_from_rows(symbol, cached)
            if found is not None:
                return found
        result = await self._request("GET", "/v5/market/tickers", {"category": self.category, "symbol": symbol})
        if result and result.get("list"):
            ticker = result["list"][0]
            return ticker_derivatives(ticker)
        return None

    async def get_open_interest_history(self, symbol: str, interval: str = "1h", limit: int = 25) -> List[Dict]:
        """Получить историю OI."""
        result = await self._request("GET", "/v5/market/open-interest", {
            "category": self.category, "symbol": symbol, "intervalTime": interval, "limit": limit,
        })
        if result and result.get("list"):
            return result["list"]
        return []

    async def get_long_short_ratio(
        self, symbol: str, period: str = "1h", limit: int = 10
    ) -> List[Dict]:
        """Long/short ratio аккаунтов (Bybit v5 account-ratio)."""
        result = await self._request(
            "GET",
            "/v5/market/account-ratio",
            {
                "category": self.category,
                "symbol": symbol,
                "period": period,
                "limit": int(limit),
            },
        )
        if result and result.get("list"):
            return result["list"]
        return []
