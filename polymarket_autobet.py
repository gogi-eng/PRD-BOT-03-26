#!/usr/bin/env python3
"""Polymarket auto-betting bot with strict risk controls.

Default mode is DRY-RUN (paper trading). Live execution is optional and
requires `py-clob-client` plus Polymarket credentials in environment.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

try:
    from dotenv import load_dotenv
except ImportError:  # Optional quality-of-life dependency.
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


UTC = timezone.utc
GAMMA_API = "https://gamma-api.polymarket.com"


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def parse_outcome_prices(raw: Any) -> Tuple[Optional[float], Optional[float]]:
    """Return (yes_price, no_price) from `outcomePrices` field."""
    if isinstance(raw, list):
        arr = raw
    elif isinstance(raw, str) and raw.startswith("["):
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            return None, None
    else:
        return None, None
    if not isinstance(arr, list) or len(arr) < 2:
        return None, None
    return to_float(arr[0], default=0.0), to_float(arr[1], default=0.0)


def parse_token_ids(raw: Any) -> Tuple[Optional[str], Optional[str]]:
    """Return (yes_token_id, no_token_id) from `clobTokenIds` field."""
    if isinstance(raw, list):
        arr = raw
    elif isinstance(raw, str) and raw.startswith("["):
        try:
            arr = json.loads(raw)
        except json.JSONDecodeError:
            return None, None
    else:
        return None, None
    if not isinstance(arr, list) or len(arr) < 2:
        return None, None
    yes_token = str(arr[0]) if arr[0] else None
    no_token = str(arr[1]) if arr[1] else None
    return yes_token, no_token


@dataclass
class SideSnapshot:
    bid: float
    ask: float
    last: float
    spread: float


def side_snapshot(market: Dict[str, Any], side: str) -> SideSnapshot:
    """Convert market fields into side-specific bid/ask/last values."""
    side_norm = side.upper().strip()
    yes_bid = to_float(market.get("bestBid"))
    yes_ask = to_float(market.get("bestAsk"))
    yes_last = to_float(market.get("lastTradePrice"))
    yes_from_outcome, no_from_outcome = parse_outcome_prices(market.get("outcomePrices"))

    # If book fields are missing, derive a synthetic spread around last.
    if yes_bid <= 0 or yes_ask <= 0:
        anchor = yes_from_outcome if yes_from_outcome and yes_from_outcome > 0 else yes_last
        if anchor <= 0:
            anchor = 0.5
        half_spread = 0.01
        yes_bid = max(0.001, anchor - half_spread)
        yes_ask = min(0.999, anchor + half_spread)
        yes_last = anchor

    if yes_last <= 0 and yes_from_outcome and yes_from_outcome > 0:
        yes_last = yes_from_outcome

    no_bid = max(0.001, min(0.999, 1.0 - yes_ask))
    no_ask = max(0.001, min(0.999, 1.0 - yes_bid))
    no_last = no_from_outcome if no_from_outcome and no_from_outcome > 0 else max(0.001, min(0.999, 1.0 - yes_last))

    if side_norm == "YES":
        bid, ask, last = yes_bid, yes_ask, yes_last
    else:
        bid, ask, last = no_bid, no_ask, no_last
    return SideSnapshot(bid=bid, ask=ask, last=last, spread=max(0.0, ask - bid))


@dataclass
class WatchlistItem:
    slug: str
    side: str
    entry_price_max: float
    risk_usdc: Optional[float] = None
    note: str = ""


@dataclass
class RiskConfig:
    bankroll_usdc: float = 100.0
    risk_per_bet_usdc: float = 1.5
    max_risk_per_bet_usdc: float = 2.0
    max_daily_loss_usdc: float = 6.0
    max_trades_per_day: int = 4
    max_open_positions: int = 2
    max_total_open_risk_usdc: float = 3.0
    max_notional_per_bet_usdc: float = 20.0


@dataclass
class ChecklistConfig:
    min_volume_24h: float = 100_000.0
    min_liquidity: float = 20_000.0
    max_spread: float = 0.02
    min_days_to_end: float = 0.2  # ~5h
    max_days_to_end: float = 14.0
    min_price: float = 0.03
    max_price: float = 0.97


@dataclass
class ExitConfig:
    stop_loss_delta: float = 0.05
    take_profit_delta: float = 0.08
    reduce_half_after_hours: float = 12.0
    time_stop_hours: float = 24.0


@dataclass
class BotConfig:
    dry_run: bool = True
    poll_interval_sec: int = 120
    state_path: str = "polymarket_autobet_state.json"
    log_path: str = "polymarket_autobet_log.jsonl"
    watchlist: List[WatchlistItem] = field(default_factory=list)
    risk: RiskConfig = field(default_factory=RiskConfig)
    checklist: ChecklistConfig = field(default_factory=ChecklistConfig)
    exits: ExitConfig = field(default_factory=ExitConfig)

    @staticmethod
    def from_json(path: Path) -> "BotConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        watchlist = [WatchlistItem(**item) for item in raw.get("watchlist", [])]
        return BotConfig(
            dry_run=bool(raw.get("dry_run", True)),
            poll_interval_sec=int(raw.get("poll_interval_sec", 120)),
            state_path=str(raw.get("state_path", "polymarket_autobet_state.json")),
            log_path=str(raw.get("log_path", "polymarket_autobet_log.jsonl")),
            watchlist=watchlist,
            risk=RiskConfig(**raw.get("risk", {})),
            checklist=ChecklistConfig(**raw.get("checklist", {})),
            exits=ExitConfig(**raw.get("exits", {})),
        )


class GammaClient:
    def __init__(self) -> None:
        self.timeout = aiohttp.ClientTimeout(total=30)

    async def _fetch_json(self, url: str) -> Any:
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.get(url, headers={"User-Agent": "polymarket-autobet/1.0"}) as resp:
                text = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"Gamma API HTTP {resp.status}: {text[:200]}")
                return json.loads(text)

    async def get_market_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        url = f"{GAMMA_API}/markets?slug={slug}"
        data = await self._fetch_json(url)
        if isinstance(data, list) and data:
            # Prefer active/open market if multiple.
            for m in data:
                if m.get("active") and not m.get("closed"):
                    return m
            return data[0]
        return None


class LiveExecutor:
    """Optional live executor via py-clob-client.

    It is intentionally initialized lazily only when live mode is enabled.
    """

    def __init__(self, host: str = "https://clob.polymarket.com", chain_id: int = 137):
        self.host = host
        self.chain_id = chain_id
        self.client = None

    def initialize(self) -> None:
        private_key = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
        funder = os.getenv("POLYMARKET_FUNDER", "").strip()
        signature_type = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
        if not private_key or not funder:
            raise RuntimeError("POLYMARKET_PRIVATE_KEY / POLYMARKET_FUNDER are required for live mode.")
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL
        except ImportError as exc:
            raise RuntimeError("Live mode requires py-clob-client. Install: pip install py-clob-client") from exc

        temp_client = ClobClient(self.host, key=private_key, chain_id=self.chain_id)
        creds = temp_client.create_or_derive_api_creds()
        self.client = ClobClient(
            self.host,
            key=private_key,
            chain_id=self.chain_id,
            creds=creds,
            signature_type=signature_type,
            funder=funder,
        )
        # Keep references for runtime methods.
        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._BUY = BUY
        self._SELL = SELL

    def post_buy(self, token_id: str, price: float, size: float) -> Dict[str, Any]:
        if self.client is None:
            self.initialize()
        order = self.client.create_order(
            self._OrderArgs(token_id=token_id, price=price, size=size, side=self._BUY)
        )
        return self.client.post_order(order, self._OrderType.GTC)

    def post_sell(self, token_id: str, price: float, size: float) -> Dict[str, Any]:
        if self.client is None:
            self.initialize()
        order = self.client.create_order(
            self._OrderArgs(token_id=token_id, price=price, size=size, side=self._SELL)
        )
        return self.client.post_order(order, self._OrderType.GTC)


class PolymarketAutoBetBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.gamma = GammaClient()
        self.executor = None if config.dry_run else LiveExecutor()
        self.state_file = Path(config.state_path).resolve()
        self.log_file = Path(config.log_path).resolve()
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        today = utc_now().date().isoformat()
        return {
            "day": today,
            "day_realized_pnl": 0.0,
            "day_trades": 0,
            "open_positions": [],
            "closed_trades": [],
        }

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, event: str, payload: Dict[str, Any]) -> None:
        rec = {"ts": iso(utc_now()), "event": event, **payload}
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _ensure_daily_reset(self) -> None:
        today = utc_now().date().isoformat()
        if self.state.get("day") != today:
            self.state["day"] = today
            self.state["day_realized_pnl"] = 0.0
            self.state["day_trades"] = 0

    def _open_risk_total(self) -> float:
        return sum(to_float(p.get("risk_usdc")) for p in self.state.get("open_positions", []))

    def _find_open_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        for p in self.state.get("open_positions", []):
            if p.get("slug") == slug:
                return p
        return None

    def _can_open_new(self, risk_usdc: float) -> Tuple[bool, str]:
        if len(self.state.get("open_positions", [])) >= self.config.risk.max_open_positions:
            return False, "max_open_positions"
        if to_float(self.state.get("day_trades")) >= self.config.risk.max_trades_per_day:
            return False, "max_trades_per_day"
        if to_float(self.state.get("day_realized_pnl")) <= -abs(self.config.risk.max_daily_loss_usdc):
            return False, "max_daily_loss_reached"
        if self._open_risk_total() + risk_usdc > self.config.risk.max_total_open_risk_usdc + 1e-9:
            return False, "max_total_open_risk"
        return True, "ok"

    def _entry_check(self, market: Dict[str, Any], side_data: SideSnapshot) -> Tuple[bool, str]:
        now = utc_now()
        end_dt = parse_dt(str(market.get("endDate", "")))
        if not end_dt:
            return False, "missing_end_date"
        days_to_end = (end_dt - now).total_seconds() / 86400
        if days_to_end < self.config.checklist.min_days_to_end:
            return False, "too_close_to_resolution"
        if days_to_end > self.config.checklist.max_days_to_end:
            return False, "too_far_from_resolution"
        volume_24h = to_float(market.get("volume24hr"))
        if volume_24h < self.config.checklist.min_volume_24h:
            return False, "low_volume_24h"
        liquidity = to_float(market.get("liquidityClob", market.get("liquidity")))
        if liquidity < self.config.checklist.min_liquidity:
            return False, "low_liquidity"
        if side_data.spread > self.config.checklist.max_spread:
            return False, "spread_too_wide"
        if side_data.ask < self.config.checklist.min_price or side_data.ask > self.config.checklist.max_price:
            return False, "price_out_of_range"
        if not bool(market.get("acceptingOrders", True)):
            return False, "market_not_accepting_orders"
        return True, "ok"

    def _build_position(self, item: WatchlistItem, market: Dict[str, Any], side_data: SideSnapshot) -> Optional[Dict[str, Any]]:
        if side_data.ask <= 0:
            return None
        if side_data.ask > item.entry_price_max + 1e-9:
            return None

        risk_usdc = min(
            to_float(item.risk_usdc, self.config.risk.risk_per_bet_usdc),
            self.config.risk.max_risk_per_bet_usdc,
        )
        stop_delta = self.config.exits.stop_loss_delta
        if stop_delta <= 0:
            return None

        qty = risk_usdc / stop_delta
        if qty <= 0:
            return None
        notional = qty * side_data.ask
        if notional > self.config.risk.max_notional_per_bet_usdc:
            qty = self.config.risk.max_notional_per_bet_usdc / side_data.ask
            notional = qty * side_data.ask

        token_yes, token_no = parse_token_ids(market.get("clobTokenIds"))
        side = item.side.upper().strip()
        token_id = token_yes if side == "YES" else token_no
        if not token_id and not self.config.dry_run:
            return None

        entry = side_data.ask
        stop_price = max(0.001, entry - self.config.exits.stop_loss_delta)
        tp_price = min(0.999, entry + self.config.exits.take_profit_delta)
        return {
            "id": str(uuid.uuid4()),
            "slug": item.slug,
            "question": market.get("question", ""),
            "side": side,
            "token_id": token_id,
            "entry_price": round(entry, 4),
            "qty": round(qty, 6),
            "notional_usdc": round(notional, 6),
            "risk_usdc": round(risk_usdc, 6),
            "stop_price": round(stop_price, 4),
            "tp_price": round(tp_price, 4),
            "opened_at": iso(utc_now()),
            "reduced_half": False,
        }

    async def _execute_open(self, position: Dict[str, Any]) -> None:
        if self.config.dry_run:
            self._log("open_dry_run", position)
            return
        resp = await asyncio.to_thread(
            self.executor.post_buy, position["token_id"], position["entry_price"], position["qty"]
        )
        self._log("open_live", {"position": position, "response": resp})

    async def _execute_close(self, position: Dict[str, Any], close_price: float, close_reason: str, close_fraction: float = 1.0) -> Dict[str, Any]:
        qty = round(to_float(position.get("qty")) * close_fraction, 6)
        if qty <= 0:
            return {}
        if self.config.dry_run:
            resp = {"status": "dry_run", "filled_qty": qty}
        else:
            resp = await asyncio.to_thread(
                self.executor.post_sell, position["token_id"], close_price, qty
            )
        entry = to_float(position.get("entry_price"))
        pnl = round((close_price - entry) * qty, 8)
        rec = {
            "position_id": position.get("id"),
            "slug": position.get("slug"),
            "question": position.get("question"),
            "side": position.get("side"),
            "qty": qty,
            "entry_price": entry,
            "exit_price": round(close_price, 4),
            "pnl": pnl,
            "reason": close_reason,
            "opened_at": position.get("opened_at"),
            "closed_at": iso(utc_now()),
            "dry_run": self.config.dry_run,
            "executor_response": resp,
        }
        self._log("close", rec)
        return rec

    async def _maybe_open_entries(self) -> None:
        for item in self.config.watchlist:
            if self._find_open_by_slug(item.slug):
                continue
            try:
                market = await self.gamma.get_market_by_slug(item.slug)
            except Exception as exc:
                self._log("market_fetch_error", {"slug": item.slug, "error": str(exc)})
                continue
            if not market:
                self._log("market_missing", {"slug": item.slug})
                continue

            side_data = side_snapshot(market, item.side)
            ok, reason = self._entry_check(market, side_data)
            if not ok:
                self._log("entry_reject", {"slug": item.slug, "reason": reason})
                continue

            position = self._build_position(item, market, side_data)
            if not position:
                self._log("entry_reject", {"slug": item.slug, "reason": "price_or_size_invalid"})
                continue

            can_open, guard_reason = self._can_open_new(to_float(position.get("risk_usdc")))
            if not can_open:
                self._log("entry_reject", {"slug": item.slug, "reason": guard_reason})
                continue

            await self._execute_open(position)
            self.state["open_positions"].append(position)
            self.state["day_trades"] = int(to_float(self.state.get("day_trades"))) + 1

    async def _manage_open_positions(self) -> None:
        keep_positions: List[Dict[str, Any]] = []
        for pos in list(self.state.get("open_positions", [])):
            slug = str(pos.get("slug"))
            try:
                market = await self.gamma.get_market_by_slug(slug)
            except Exception as exc:
                self._log("market_fetch_error", {"slug": slug, "error": str(exc)})
                keep_positions.append(pos)
                continue
            if not market:
                keep_positions.append(pos)
                continue

            side_data = side_snapshot(market, str(pos.get("side")))
            now = utc_now()
            opened_at = parse_dt(str(pos.get("opened_at", ""))) or now
            held_hours = (now - opened_at).total_seconds() / 3600

            # optional half reduction after fixed time if no momentum
            if (
                not bool(pos.get("reduced_half"))
                and held_hours >= self.config.exits.reduce_half_after_hours
                and side_data.last < to_float(pos.get("entry_price")) + 0.01
            ):
                rec = await self._execute_close(pos, side_data.bid, "time_reduce_half", close_fraction=0.5)
                if rec:
                    self.state["closed_trades"].append(rec)
                    self.state["day_realized_pnl"] = round(
                        to_float(self.state.get("day_realized_pnl")) + to_float(rec.get("pnl")), 8
                    )
                    pos["qty"] = round(to_float(pos.get("qty")) * 0.5, 6)
                    pos["reduced_half"] = True

            should_close = False
            reason = ""
            if side_data.bid <= to_float(pos.get("stop_price")):
                should_close = True
                reason = "stop_loss"
            elif side_data.bid >= to_float(pos.get("tp_price")):
                should_close = True
                reason = "take_profit"
            elif held_hours >= self.config.exits.time_stop_hours:
                should_close = True
                reason = "time_stop"

            if should_close:
                rec = await self._execute_close(pos, side_data.bid, reason, close_fraction=1.0)
                if rec:
                    self.state["closed_trades"].append(rec)
                    self.state["day_realized_pnl"] = round(
                        to_float(self.state.get("day_realized_pnl")) + to_float(rec.get("pnl")), 8
                    )
                continue

            keep_positions.append(pos)
        self.state["open_positions"] = keep_positions

    async def step(self) -> None:
        self._ensure_daily_reset()
        await self._manage_open_positions()
        await self._maybe_open_entries()
        self._save_state()

    async def run_forever(self) -> None:
        if not self.config.watchlist:
            print("Watchlist is empty. Add items in config JSON first.")
            return
        print(
            f"[POLYBOT] Started | dry_run={self.config.dry_run} "
            f"| watchlist={len(self.config.watchlist)} | poll={self.config.poll_interval_sec}s"
        )
        while True:
            try:
                await self.step()
            except Exception as exc:
                self._log("fatal_step_error", {"error": str(exc)})
            await asyncio.sleep(self.config.poll_interval_sec)


def default_config_dict() -> Dict[str, Any]:
    return {
        "dry_run": True,
        "poll_interval_sec": 120,
        "state_path": "polymarket_autobet_state.json",
        "log_path": "polymarket_autobet_log.jsonl",
        "risk": asdict(RiskConfig()),
        "checklist": asdict(ChecklistConfig()),
        "exits": asdict(ExitConfig()),
        "watchlist": [
            {
                "slug": "next-prime-minister-of-hungary",
                "side": "YES",
                "entry_price_max": 0.33,
                "risk_usdc": 1.5,
                "note": "Example only. Adjust manually.",
            }
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polymarket auto-betting bot (dry-run by default).")
    parser.add_argument(
        "--config",
        type=str,
        default="polymarket_autobet_config.json",
        help="Path to JSON config file.",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create example config file and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan/manage cycle and exit.",
    )
    return parser


async def amain(args: argparse.Namespace) -> int:
    script_dir = Path(__file__).resolve().parent
    load_dotenv(dotenv_path=script_dir / ".env", override=False)

    cfg_path = Path(args.config).resolve()
    if args.init_config:
        if cfg_path.exists():
            print(f"Config already exists: {cfg_path}")
            return 0
        cfg_path.write_text(json.dumps(default_config_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Created config: {cfg_path}")
        return 0

    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}")
        print("Run with --init-config to generate an example.")
        return 2

    config = BotConfig.from_json(cfg_path)
    bot = PolymarketAutoBetBot(config=config)
    if args.once:
        await bot.step()
        print("One cycle completed.")
        return 0
    await bot.run_forever()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
