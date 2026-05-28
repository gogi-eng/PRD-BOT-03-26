"""
Движок зеркала: только позиции Copy Trading с небольшим профитом → субаккаунт.
Не использует UnifiedOrchestrator и не мешает trading_bot.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from prd_agent.copy_mirror.clients import build_adapter
from prd_agent.copy_mirror.config_loader import load_mirror_config, validate_credentials
from prd_agent.copy_mirror.filters import MirrorEntryFilters
from prd_agent.copy_mirror.position_math import parse_position, position_key
from prd_agent.copy_mirror.pump_dump_agent import PumpDumpScout
from prd_agent.copy_mirror.state import MirrorStateStore

logger = logging.getLogger("prd_agent.copy_mirror")


class CopyMirrorEngine:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        m = cfg.get("copy_mirror", {})
        self.enabled = bool(m.get("enabled", True))
        self.copy_trading_enabled = bool(m.get("copy_trading_enabled", True))
        self.poll_seconds = float(m.get("poll_seconds", 12))
        self.close_when_source_closes = bool(m.get("close_when_source_closes", True))
        self.notify_telegram = bool(m.get("notify_telegram", True))

        root = Path(cfg["_root"])
        state_path = m.get("state_file", "data/copy_mirror/state.json")
        self.state = MirrorStateStore(root / state_path)

        self.source = build_adapter(cfg, "bybit_source")
        self.target = build_adapter(cfg, "bybit_target")
        self.filters = MirrorEntryFilters(cfg)
        self.pump_dump = PumpDumpScout(cfg, self.source)

        tg = cfg.get("telegram", {})
        self._tg_token = str(tg.get("bot_token", "")).strip()
        self._tg_chat = str(tg.get("chat_id", "")).strip()

    async def close(self) -> None:
        await self.source.close()
        await self.target.close()

    async def _notify(self, text: str) -> None:
        if not self.notify_telegram or not self._tg_token or not self._tg_chat:
            return
        try:
            import aiohttp

            url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                await session.post(
                    url,
                    json={"chat_id": self._tg_chat, "text": text[:4000]},
                    timeout=aiohttp.ClientTimeout(total=15),
                )
        except Exception as exc:
            logger.warning("telegram notify: %s", exc)

    def _live_keys(self, rows: List[Dict]) -> Set[str]:
        keys: Set[str] = set()
        for row in rows:
            p = parse_position(row)
            if p:
                keys.add(position_key(p["symbol"], p["side"]))
        return keys

    async def _close_target(self, symbol: str, side: str, qty: float, position_idx: int) -> bool:
        client = self.target._client
        if not hasattr(client, "close_position"):
            return False
        close_side = "Sell" if side == "Buy" else "Buy"
        res = await client.close_position(
            symbol, close_side, qty=qty, position_idx=position_idx
        )
        return bool(res.get("success") or res.get("orderId"))

    async def _sync_closes(self, source_keys: Set[str], target_rows: List[Dict]) -> None:
        target_by_key: Dict[str, Dict] = {}
        for row in target_rows:
            p = parse_position(row)
            if p:
                target_by_key[position_key(p["symbol"], p["side"])] = p

        for w in self.state.iter_tracked():
            if w.status != "mirrored":
                continue
            key = position_key(w.symbol, w.side)
            if key in source_keys:
                continue
            p = target_by_key.get(key)
            if self.close_when_source_closes and p:
                ok = await self._close_target(
                    p["symbol"], p["side"], p["size"], p["position_idx"]
                )
                msg = (
                    f"🪞 Зеркало закрыто {p['symbol']} {p['side']} (мастер закрыл)"
                    if ok
                    else f"⚠️ Не удалось закрыть зеркало {p['symbol']}"
                )
                logger.info(msg)
                await self._notify(msg)
            self.state.remove(w.symbol, w.side)
        self.state.save()

    async def _try_open_mirror(self, pos: Dict[str, Any], profit_pct: float) -> None:
        sym, side = pos["symbol"], pos["side"]
        w = self.state.get(sym, side)
        if not w or w.status != "watching":
            return

        pos["first_seen_ts"] = w.first_seen_ts
        target_rows = await self.target.get_positions()
        open_count = len([r for r in target_rows if parse_position(r)])

        ok, reason = await self.filters.check_before_open(
            pos, profit_pct, self.target, open_on_target=open_count
        )
        if not ok:
            if "истёк лимит" in reason or "поздно" in reason:
                self.state.mark_skipped(sym, side, reason)
                self.state.save()
            logger.info("mirror skip %s %s: %s", sym, side, reason)
            return

        balance = await self.target.get_available_balance()
        lev = self.filters.leverage
        if pos.get("leverage", 0) > 0:
            lev = min(lev, int(pos["leverage"]))

        await self.target.apply_trade_leverage(sym, lev)
        entry = float(pos["entry"])
        sl = float(pos.get("stop_loss") or 0)
        tp = float(pos.get("take_profit") or 0)
        if sl <= 0:
            sl = entry * (0.99 if side == "Buy" else 1.01)
        if tp <= 0:
            tp = entry * (1.02 if side == "Buy" else 0.98)

        qty = self.filters.calc_qty(balance, entry, sl, lev)
        if qty <= 0:
            self.state.mark_skipped(sym, side, "mirror: qty=0")
            self.state.save()
            return

        if await self.target.has_open_position(sym):
            self.state.mark_skipped(sym, side, "mirror: уже есть позиция на субаккаунте")
            self.state.save()
            return

        res = await self.target.place_order(
            sym, side, qty, stop_loss=sl, take_profit=tp, order_type="Market"
        )
        if not res.get("success"):
            logger.warning("mirror order fail %s: %s", sym, res.get("error"))
            return

        self.state.mark_mirrored(sym, side, qty)
        self.state.save()
        msg = (
            f"🪞 Зеркало ОТКРЫТО {sym} {side} qty={qty:.6f} "
            f"профит источника {profit_pct:.2f}% lev={lev}x"
        )
        logger.info(msg)
        await self._notify(msg)

    async def _tick(self) -> None:
        source_rows = await self.source.get_positions()
        target_rows = await self.target.get_positions()
        source_keys = self._live_keys(source_rows)

        await self._sync_closes(source_keys, target_rows)

        for row in source_rows:
            pos = parse_position(row)
            if not pos:
                continue
            sym, side = pos["symbol"], pos["side"]
            profit_pct = float(pos["profit_pct"])
            w = self.state.get(sym, side)

            if not w:
                w = self.state.upsert_watch(sym, side, pos["entry"])
                logger.info(
                    "mirror watch %s %s entry=%.4f (ждём профит %.2f–%.2f%%)",
                    sym,
                    side,
                    pos["entry"],
                    self.filters.min_profit_pct,
                    self.filters.max_profit_pct,
                )
                self.state.save()
                continue

            if w.status == "skipped":
                continue

            if w.status == "mirrored":
                self.state.update_peak(sym, side, profit_pct)
                continue

            self.state.update_peak(sym, side, profit_pct)
            await self._try_open_mirror(pos, profit_pct)

        self.state.save()

    async def run_forever(self) -> None:
        ok, err = validate_credentials(self.cfg)
        if not ok:
            raise RuntimeError(err)
        logger.info(
            "Copy mirror started (poll=%.0fs, profit %.2f–%.2f%%, copy_trading=%s)",
            self.poll_seconds,
            self.filters.min_profit_pct,
            self.filters.max_profit_pct,
            self.copy_trading_enabled,
        )
        while self.enabled:
            try:
                if self.copy_trading_enabled:
                    await self._tick()
                await self.pump_dump.tick()
            except Exception as exc:
                logger.exception("mirror tick error: %s", exc)
            await asyncio.sleep(self.poll_seconds)


async def run_from_config(path: Optional[Path] = None) -> None:
    cfg = load_mirror_config(path)
    engine = CopyMirrorEngine(cfg)
    try:
        await engine.run_forever()
    finally:
        await engine.close()
