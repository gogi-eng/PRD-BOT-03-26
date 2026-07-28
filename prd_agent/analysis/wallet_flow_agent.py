"""
Wallet Flow Agent (v1) — advisory ончейн-интерес умных кошельков.

Следит за watch-адресами, нормализует свапы/токен-трансферы и строит
рекомендации для торгового бота (bias long/short/neutral).

v1 НЕ ставит ордера. Только лог + recommendations.jsonl + in-memory.

Config: wallet_tracker
Маркеры лога: «Wallet tracker», «Wallet flow recommendation»
Ключи API только из .env: DEBANK_ACCESS_KEY, ETHERSCAN_API_KEY
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger("prd_agent.wallet_flow")

_LOG_MARKER = "Wallet tracker"
_REC_MARKER = "Wallet flow recommendation"

# Известные мем/scaled тикеры на Bybit linear USDT
_BYBIT_SYMBOL_MAP: Dict[str, str] = {
    "BTC": "BTCUSDT",
    "WBTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "WETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "DOT": "DOTUSDT",
    "MATIC": "MATICUSDT",
    "POL": "POLUSDT",
    "NEAR": "NEARUSDT",
    "APT": "APTUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
    "SUI": "SUIUSDT",
    "PEPE": "1000PEPEUSDT",
    "SHIB": "1000SHIBUSDT",
    "FLOKI": "1000FLOKIUSDT",
    "BONK": "1000BONKUSDT",
    "LUNC": "1000LUNCUSDT",
    "XEC": "1000XECUSDT",
    "RATS": "1000RATUSDT",
    "SATS": "1000SATSUSDT",
    "MOG": "1000000MOGUSDT",
    "BABYDOGE": "1000000BABYDOGEUSDT",
}

# Trash / stable / LP — не маппим на perp
_IGNORE_SYMBOLS = {
    "USDT",
    "USDC",
    "DAI",
    "USD",
    "TUSD",
    "BUSD",
    "FRAX",
    "WSTETH",
    "STETH",
    "RETH",
    "CBETH",
    "UNI-V2",
    "UNI-V3",
    "LP",
}


@dataclass(frozen=True)
class SwapEvent:
    wallet: str
    chain: str
    token_symbol: str
    token_address: str
    side: str  # buy | sell
    usd_value: float
    ts: float
    tx_hash: str
    label: str = ""


@dataclass(frozen=True)
class WalletRecommendation:
    symbol: str
    bias: str  # long | short | neutral
    confidence: float
    reason: str
    source: str = "wallet_flow"
    advisory: bool = True
    usd_volume: float = 0.0
    created_at: float = 0.0
    expires_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WatchWallet:
    address: str
    label: str = ""
    chain: str = "eth"


def read_wallet_tracker_cfg(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("wallet_tracker")
    return dict(raw) if isinstance(raw, dict) else {}


def wallet_tracker_enabled(cfg: Mapping[str, Any]) -> bool:
    return bool(read_wallet_tracker_cfg(cfg).get("enabled", False))


def map_token_to_bybit_symbol(token_symbol: str) -> Optional[str]:
    """Маппинг тикера токена → Bybit linear USDT perpetual."""
    raw = str(token_symbol or "").strip().upper()
    if not raw or raw in _IGNORE_SYMBOLS:
        return None
    # убрать префиксы вида $PEPE / PEPE.e
    cleaned = raw.replace("$", "").split(".")[0].strip()
    if cleaned in _IGNORE_SYMBOLS:
        return None
    if cleaned in _BYBIT_SYMBOL_MAP:
        return _BYBIT_SYMBOL_MAP[cleaned]
    # эвристика 1000* / обычный USDT
    if cleaned.startswith("1000") and cleaned.endswith("USDT"):
        return cleaned
    if cleaned.endswith("USDT") and len(cleaned) > 4:
        return cleaned
    # мем-паттерн: часто на Bybit как 1000SYMBOLUSDT
    meme_guess = f"1000{cleaned}USDT"
    if cleaned in ("PEPE", "SHIB", "FLOKI", "BONK", "LUNC", "XEC"):
        return meme_guess
    # обычный контракт SYMBOLUSDT (бот всё равно проверит наличие на бирже позже)
    return f"{cleaned}USDT"


def _http_get_json(url: str, *, headers: Optional[Dict[str, str]] = None, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "PRD-BOT-wallet-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


class SwapProvider(ABC):
    """Интерфейс источника свапов/трансферов."""

    name: str = "base"

    @abstractmethod
    def fetch_swaps(self, address: str, chain: str = "eth") -> List[SwapEvent]:
        raise NotImplementedError


class StubSwapProvider(SwapProvider):
    """Провайдер для тестов: отдаёт заранее заданные события."""

    name = "stub"

    def __init__(self, events: Optional[Sequence[SwapEvent]] = None):
        self._events: List[SwapEvent] = list(events or [])

    def set_events(self, events: Sequence[SwapEvent]) -> None:
        self._events = list(events)

    def fetch_swaps(self, address: str, chain: str = "eth") -> List[SwapEvent]:
        addr = address.lower()
        return [e for e in self._events if e.wallet.lower() == addr and e.chain.lower() == chain.lower()]


class EtherscanSwapProvider(SwapProvider):
    """
    Публичный Etherscan account/tokentx.
    Трансфер to=wallet → buy, from=wallet → sell.
    USD: CoinGecko token_price (best-effort, без ключа).
    """

    name = "etherscan"

    def __init__(self, api_key: str, *, lookback_sec: float = 86400.0):
        self.api_key = api_key
        self.lookback_sec = lookback_sec
        self._price_cache: Dict[str, float] = {}

    def _token_usd(self, token_address: str) -> float:
        key = token_address.lower()
        if key in self._price_cache:
            return self._price_cache[key]
        try:
            q = urllib.parse.urlencode(
                {
                    "contract_addresses": key,
                    "vs_currencies": "usd",
                }
            )
            url = f"https://api.coingecko.com/api/v3/simple/token_price/ethereum?{q}"
            data = _http_get_json(url, timeout=12.0)
            price = 0.0
            if isinstance(data, dict):
                row = data.get(key) or data.get(token_address)
                if isinstance(row, dict):
                    price = float(row.get("usd") or 0)
            self._price_cache[key] = price
            return price
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.debug("%s CoinGecko price fail %s: %s", _LOG_MARKER, key[:10], exc)
            self._price_cache[key] = 0.0
            return 0.0

    def fetch_swaps(self, address: str, chain: str = "eth") -> List[SwapEvent]:
        if chain.lower() not in ("eth", "ethereum", "1"):
            return []
        q = urllib.parse.urlencode(
            {
                "module": "account",
                "action": "tokentx",
                "address": address,
                "page": 1,
                "offset": 40,
                "sort": "desc",
                "apikey": self.api_key,
            }
        )
        url = f"https://api.etherscan.io/api?{q}"
        try:
            data = _http_get_json(url, timeout=25.0)
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("%s etherscan fetch failed: %s", _LOG_MARKER, exc)
            return []
        if not isinstance(data, dict) or str(data.get("status")) not in ("1", "0"):
            return []
        rows = data.get("result") or []
        if not isinstance(rows, list):
            return []
        now = time.time()
        cutoff = now - self.lookback_sec
        addr_l = address.lower()
        out: List[SwapEvent] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                ts = float(row.get("timeStamp") or 0)
            except (TypeError, ValueError):
                continue
            if ts < cutoff:
                continue
            frm = str(row.get("from") or "").lower()
            to = str(row.get("to") or "").lower()
            if addr_l == to:
                side = "buy"
            elif addr_l == frm:
                side = "sell"
            else:
                continue
            symbol = str(row.get("tokenSymbol") or "").strip()
            token_addr = str(row.get("contractAddress") or "").strip()
            try:
                decimals = int(row.get("tokenDecimal") or 18)
            except (TypeError, ValueError):
                decimals = 18
            try:
                raw_val = float(row.get("value") or 0)
            except (TypeError, ValueError):
                raw_val = 0.0
            amount = raw_val / (10 ** max(0, decimals)) if decimals >= 0 else 0.0
            price = self._token_usd(token_addr) if token_addr else 0.0
            usd = amount * price if price > 0 else 0.0
            out.append(
                SwapEvent(
                    wallet=address,
                    chain="eth",
                    token_symbol=symbol,
                    token_address=token_addr,
                    side=side,
                    usd_value=usd,
                    ts=ts,
                    tx_hash=str(row.get("hash") or ""),
                )
            )
        return out


class DebankSwapProvider(SwapProvider):
    """Debank OpenAPI history (нужен DEBANK_ACCESS_KEY)."""

    name = "debank"

    def __init__(self, access_key: str, *, lookback_sec: float = 86400.0):
        self.access_key = access_key
        self.lookback_sec = lookback_sec

    def fetch_swaps(self, address: str, chain: str = "eth") -> List[SwapEvent]:
        chain_id = "eth" if chain.lower() in ("eth", "ethereum", "1") else chain.lower()
        q = urllib.parse.urlencode({"id": address, "chain_id": chain_id, "page_count": 20})
        url = f"https://pro-openapi.debank.com/v1/user/history_list?{q}"
        try:
            data = _http_get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "AccessKey": self.access_key,
                    "User-Agent": "PRD-BOT-wallet-tracker/1.0",
                },
                timeout=25.0,
            )
        except (urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
            logger.warning("%s debank fetch failed: %s", _LOG_MARKER, exc)
            return []
        history = []
        if isinstance(data, dict):
            history = data.get("history_list") or data.get("data") or []
        if not isinstance(history, list):
            return []
        now = time.time()
        cutoff = now - self.lookback_sec
        out: List[SwapEvent] = []
        for item in history:
            if not isinstance(item, dict):
                continue
            try:
                ts = float(item.get("time_at") or item.get("timestamp") or 0)
            except (TypeError, ValueError):
                ts = 0.0
            if ts and ts < cutoff:
                continue
            sends = item.get("sends") or []
            receives = item.get("receives") or []
            tx = str(item.get("tx") or item.get("id") or "")
            if isinstance(tx, dict):
                tx = str(tx.get("id") or tx.get("hash") or "")
            for recv in receives if isinstance(receives, list) else []:
                if not isinstance(recv, dict):
                    continue
                token = recv.get("token") if isinstance(recv.get("token"), dict) else {}
                sym = str((token or {}).get("symbol") or recv.get("symbol") or "")
                addr = str((token or {}).get("id") or recv.get("token_id") or "")
                try:
                    usd = float(recv.get("amount") or 0) * float((token or {}).get("price") or 0)
                except (TypeError, ValueError):
                    usd = float(recv.get("usd_value") or 0) or 0.0
                out.append(
                    SwapEvent(
                        wallet=address,
                        chain=chain_id,
                        token_symbol=sym,
                        token_address=addr,
                        side="buy",
                        usd_value=max(0.0, usd),
                        ts=ts or now,
                        tx_hash=tx,
                    )
                )
            for send in sends if isinstance(sends, list) else []:
                if not isinstance(send, dict):
                    continue
                token = send.get("token") if isinstance(send.get("token"), dict) else {}
                sym = str((token or {}).get("symbol") or send.get("symbol") or "")
                addr = str((token or {}).get("id") or send.get("token_id") or "")
                try:
                    usd = float(send.get("amount") or 0) * float((token or {}).get("price") or 0)
                except (TypeError, ValueError):
                    usd = float(send.get("usd_value") or 0) or 0.0
                out.append(
                    SwapEvent(
                        wallet=address,
                        chain=chain_id,
                        token_symbol=sym,
                        token_address=addr,
                        side="sell",
                        usd_value=max(0.0, usd),
                        ts=ts or now,
                        tx_hash=tx,
                    )
                )
        return out


def detect_api_keys() -> Dict[str, str]:
    """Ключи только из окружения (не из yaml)."""
    out: Dict[str, str] = {}
    for env_name, short in (
        ("DEBANK_ACCESS_KEY", "debank"),
        ("ETHERSCAN_API_KEY", "etherscan"),
        ("DUNE_API_KEY", "dune"),
    ):
        val = str(os.environ.get(env_name) or "").strip()
        if val:
            out[short] = val
    return out


def build_default_provider(
    *,
    lookback_sec: float = 86400.0,
    force_stub: Optional[SwapProvider] = None,
) -> Optional[SwapProvider]:
    if force_stub is not None:
        return force_stub
    keys = detect_api_keys()
    if "debank" in keys:
        return DebankSwapProvider(keys["debank"], lookback_sec=lookback_sec)
    if "etherscan" in keys:
        return EtherscanSwapProvider(keys["etherscan"], lookback_sec=lookback_sec)
    # Dune ключ есть, но SQL-провайдер в v1 не реализован — не считаем рабочим источником
    return None


class WalletFlowAgent:
    """Advisory агент: watch-кошельки → рекомендации, без ордеров."""

    def __init__(
        self,
        cfg: Mapping[str, Any],
        data_dir: Path,
        *,
        provider: Optional[SwapProvider] = None,
        time_fn: Optional[Any] = None,
    ):
        self.cfg = cfg
        block = read_wallet_tracker_cfg(cfg)
        self.enabled = bool(block.get("enabled", False))
        self.poll_interval_sec = float(block.get("poll_interval_sec", 300))
        self.min_swap_usd = float(block.get("min_swap_usd", 5000))
        self.recommendation_ttl_sec = float(block.get("recommendation_ttl_sec", 3600))
        self.symbol_cooldown_sec = float(block.get("symbol_cooldown_sec", 1800))
        self.lookback_sec = float(block.get("lookback_sec", 86400))
        chains_raw = block.get("chains") or ["eth"]
        self.chains = [str(c).lower() for c in chains_raw if str(c).strip()]
        self.watches = self._parse_watches(block.get("watches") or [])
        self._time = time_fn or time.time
        self._out_dir = Path(data_dir) / "wallet_tracker"
        self._rec_path = self._out_dir / "recommendations.jsonl"
        self._recommendations: List[WalletRecommendation] = []
        self._last_emit_by_symbol: Dict[str, float] = {}
        self._provider: Optional[SwapProvider] = None
        self.active = False
        self.disable_reason = ""

        if not self.enabled:
            return

        if provider is not None:
            self._provider = provider
        else:
            self._provider = build_default_provider(lookback_sec=self.lookback_sec)

        if self._provider is None:
            self.disable_reason = "no API key"
            logger.warning("%s disabled: no API key", _LOG_MARKER)
            self.active = False
            return

        self.active = True
        keys = detect_api_keys()
        key_hint = ",".join(sorted(keys.keys())) or self._provider.name
        logger.info(
            "%s enabled / Wallet tracker advisory (provider=%s keys=%s watches=%d)",
            _LOG_MARKER,
            self._provider.name,
            key_hint,
            len(self.watches),
        )
        if not self.watches:
            logger.info("%s: watches empty — рекомендаций не будет, пока не добавите адреса", _LOG_MARKER)

    @staticmethod
    def _parse_watches(raw: Any) -> List[WatchWallet]:
        out: List[WatchWallet] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            addr = str(item.get("address") or "").strip()
            if not addr or addr.startswith("0x...") or "ЗАМЕНИТЕ" in addr.upper() or "REPLACE" in addr.upper():
                continue
            if len(addr) < 10:
                continue
            out.append(
                WatchWallet(
                    address=addr,
                    label=str(item.get("label") or ""),
                    chain=str(item.get("chain") or "eth").lower(),
                )
            )
        return out

    def should_run_loop(self) -> bool:
        return self.enabled and self.active and self._provider is not None

    def filter_min_usd(self, events: Sequence[SwapEvent]) -> List[SwapEvent]:
        return [e for e in events if float(e.usd_value or 0) >= self.min_swap_usd]

    def _cooldown_ok(self, symbol: str, now: float) -> bool:
        last = self._last_emit_by_symbol.get(symbol.upper(), 0.0)
        return (now - last) >= self.symbol_cooldown_sec

    def build_recommendations(self, events: Sequence[SwapEvent]) -> List[WalletRecommendation]:
        """Агрегирует свапы → рекомендации с фильтрами min_usd / mapping / cooldown."""
        now = float(self._time())
        filtered = self.filter_min_usd(events)
        # symbol -> buy_usd, sell_usd, reasons
        agg: Dict[str, Dict[str, Any]] = {}
        for ev in filtered:
            bybit = map_token_to_bybit_symbol(ev.token_symbol)
            if not bybit:
                continue
            bucket = agg.setdefault(
                bybit,
                {"buy": 0.0, "sell": 0.0, "n": 0, "wallets": set(), "tokens": set()},
            )
            if ev.side == "buy":
                bucket["buy"] += float(ev.usd_value)
            else:
                bucket["sell"] += float(ev.usd_value)
            bucket["n"] += 1
            bucket["wallets"].add(ev.wallet.lower())
            bucket["tokens"].add(ev.token_symbol.upper())

        recs: List[WalletRecommendation] = []
        for symbol, bucket in agg.items():
            if not self._cooldown_ok(symbol, now):
                continue
            buy = float(bucket["buy"])
            sell = float(bucket["sell"])
            net = buy - sell
            total = buy + sell
            if total <= 0:
                continue
            if net > total * 0.15:
                bias = "long"
            elif net < -total * 0.15:
                bias = "short"
            else:
                bias = "neutral"
            # confidence: доля нетто + число кошельков
            conf = min(0.95, 0.35 + abs(net) / max(total, 1.0) * 0.4 + min(0.2, 0.05 * len(bucket["wallets"])))
            reason = (
                f"watch wallets {bias}: buy=${buy:,.0f} sell=${sell:,.0f} "
                f"n={bucket['n']} wallets={len(bucket['wallets'])} "
                f"tokens={','.join(sorted(bucket['tokens']))}"
            )
            rec = WalletRecommendation(
                symbol=symbol,
                bias=bias,
                confidence=round(conf, 3),
                reason=reason,
                source="wallet_flow",
                advisory=True,
                usd_volume=round(total, 2),
                created_at=now,
                expires_at=now + self.recommendation_ttl_sec,
            )
            recs.append(rec)
            self._last_emit_by_symbol[symbol] = now
        recs.sort(key=lambda r: r.usd_volume, reverse=True)
        return recs

    def _persist(self, recs: Sequence[WalletRecommendation]) -> None:
        if not recs:
            return
        try:
            self._out_dir.mkdir(parents=True, exist_ok=True)
            with self._rec_path.open("a", encoding="utf-8") as fh:
                for r in recs:
                    fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("%s persist failed: %s", _LOG_MARKER, exc)

    def _prune_memory(self, now: float) -> None:
        self._recommendations = [r for r in self._recommendations if r.expires_at > now]

    def active_recommendations(self) -> List[WalletRecommendation]:
        now = float(self._time())
        self._prune_memory(now)
        return list(self._recommendations)

    def recommendation_for_symbol(self, symbol: str) -> Optional[WalletRecommendation]:
        sym = str(symbol or "").upper()
        for r in self.active_recommendations():
            if r.symbol == sym:
                return r
        return None

    def collect_swaps(self) -> List[SwapEvent]:
        if not self._provider:
            return []
        events: List[SwapEvent] = []
        for w in self.watches:
            chain = (w.chain or "eth").lower()
            if self.chains and chain not in self.chains:
                continue
            try:
                batch = self._provider.fetch_swaps(w.address, chain=chain)
            except Exception as exc:
                logger.warning("%s provider error %s: %s", _LOG_MARKER, w.label or w.address[:10], exc)
                continue
            for ev in batch:
                if w.label and not ev.label:
                    events.append(
                        SwapEvent(
                            wallet=ev.wallet,
                            chain=ev.chain,
                            token_symbol=ev.token_symbol,
                            token_address=ev.token_address,
                            side=ev.side,
                            usd_value=ev.usd_value,
                            ts=ev.ts,
                            tx_hash=ev.tx_hash,
                            label=w.label,
                        )
                    )
                else:
                    events.append(ev)
        return events

    async def poll_and_recommend(self) -> List[WalletRecommendation]:
        """Один цикл опроса (async-обёртка; HTTP синхронный в v1)."""
        if not self.should_run_loop():
            return []
        events = self.collect_swaps()
        recs = self.build_recommendations(events)
        now = float(self._time())
        self._prune_memory(now)
        for r in recs:
            self._recommendations.append(r)
            logger.info(
                "%s %s bias=%s conf=%.2f usd=%.0f — %s",
                _REC_MARKER,
                r.symbol,
                r.bias,
                r.confidence,
                r.usd_volume,
                r.reason[:160],
            )
        self._persist(recs)
        if not recs and events:
            logger.info("%s: свапы есть (%d), но после фильтров рекомендаций 0", _LOG_MARKER, len(events))
        elif not events and self.watches:
            logger.info("%s: новых свапов нет (watches=%d)", _LOG_MARKER, len(self.watches))
        return recs

    def build_report(self) -> str:
        """Текстовый отчёт для будущего (без кнопки Telegram в v1)."""
        lines = [
            f"<b>{_LOG_MARKER}</b>",
            f"enabled={self.enabled} active={self.active}",
        ]
        if self.disable_reason:
            lines.append(f"reason: {self.disable_reason}")
        if self._provider:
            lines.append(f"provider: {self._provider.name}")
        lines.append(f"watches: {len(self.watches)}")
        lines.append(f"min_swap_usd: {self.min_swap_usd:g}")
        recs = self.active_recommendations()
        if not recs:
            lines.append("рекомендаций сейчас нет")
        else:
            lines.append(f"активных: {len(recs)}")
            for r in recs[:12]:
                lines.append(
                    f"• {r.symbol} {r.bias} conf={r.confidence:.0%} ${r.usd_volume:,.0f} — {r.reason[:80]}"
                )
        return "\n".join(lines)


def log_wallet_tracker_startup(cfg: Mapping[str, Any], log: Optional[logging.Logger] = None) -> None:
    """Маркер при старте orchestrator (даже если агент ещё не создан с data_dir)."""
    lg = log or logger
    block = read_wallet_tracker_cfg(cfg)
    if not bool(block.get("enabled", False)):
        return
    keys = detect_api_keys()
    if not keys or (not keys.get("debank") and not keys.get("etherscan")):
        lg.warning("%s disabled: no API key (нужен DEBANK_ACCESS_KEY или ETHERSCAN_API_KEY)", _LOG_MARKER)
        return
    lg.info("%s enabled / Wallet tracker advisory", _LOG_MARKER)
