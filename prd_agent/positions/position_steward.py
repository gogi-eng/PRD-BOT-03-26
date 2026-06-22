"""
Сопровождение позиций с биржи (в т.ч. открытых вручную): трейлинг SL, time-stop, breakeven по ATR.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from prd_agent.positions.liquidation_guard import (
    LiquidationGuardConfig,
    evaluate_liquidation_stop,
)
from prd_agent.positions.exit_management import (
    ExitManagementConfig,
    age_minutes,
    effective_breakeven_pct,
    evaluate_exit_actions,
    late_retrace_active,
    profit_pct,
    progress_in_atr,
)
from prd_agent.positions.tp_progress_exit import evaluate_tp_progress_exit
from prd_agent.positions.bot_position_registry import (
    bot_levels_from_registry,
    bot_symbols_from_registry,
    close_journal_ghosts,
    load_registry,
    merge_open_sources,
    reconcile_registry_with_exchange,
    register_bot_open,
    symbols_open_in_journal,
    unregister_bot_symbol,
)
from prd_agent.positions.sync_guard import PositionSyncGuard
from prd_agent.signals.pump_dump_mode import TrailingProfile

logger = logging.getLogger("prd_agent.positions")


@dataclass
class TrackedPosition:
    symbol: str
    side: str
    entry: float
    qty: float
    stop_loss: float = 0.0
    take_profit: float = 0.0
    best_price: float = 0.0
    tp_progress_phase: str = ""
    trailing_active: bool = False
    position_idx: int = 0
    origin: str = "manual"
    pump_dump_mode: bool = False
    last_sl_sent: float = 0.0
    opened_at_utc: str = ""
    peak_profit_pct: float = 0.0


class PositionSteward:
    def __init__(self, cfg: Dict[str, Any]):
        self._tracked: Dict[str, TrackedPosition] = {}
        self._bot_symbols: set[str] = set()
        self._pump_dump_symbols: set[str] = set()
        self._bot_levels: Dict[str, Dict[str, float]] = {}
        root = Path(str(cfg.get("_root", ".")))
        self._data_dir = root / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._sync_guard = PositionSyncGuard()
        self.apply_config(cfg)
        self._load_bot_registry()

    def _telegram_audit_path(self) -> Path:
        cfg = getattr(self, "cfg", {}) or {}
        agent = cfg.get("telegram_signal_agent", {}) if isinstance(cfg.get("telegram_signal_agent"), dict) else {}
        out = str(agent.get("out_dir", "reports/telegram_signals") or "reports/telegram_signals")
        return Path(str(cfg.get("_root", "."))) / out / "signals.jsonl"

    def _load_bot_registry(self) -> None:
        journal = self._data_dir / "trades" / "trade_history.jsonl"
        audit = self._telegram_audit_path()
        merged = merge_open_sources(
            self._data_dir,
            journal_path=journal,
            telegram_audit_path=audit if audit.exists() else None,
        )
        self._bot_symbols |= merged
        for sym, levels in bot_levels_from_registry(self._data_dir).items():
            self._bot_levels[sym] = levels
        reg = load_registry(self._data_dir)
        sym_rows = reg.get("symbols") if isinstance(reg.get("symbols"), dict) else {}
        for sym, row in sym_rows.items():
            if isinstance(row, dict) and bool(row.get("pump_dump")):
                self._pump_dump_symbols.add(str(sym).upper())

    def hydrate_open_symbols_from_journal(self, journal_path: Path) -> None:
        merged = merge_open_sources(
            self._data_dir,
            journal_path=journal_path,
            telegram_audit_path=None,
        )
        self._bot_symbols |= merged

    def apply_config(self, cfg: Dict[str, Any]) -> None:
        self.cfg = cfg
        p = cfg.get("positions", {}) if isinstance(cfg.get("positions"), dict) else {}
        self.enabled = bool(p.get("trailing_enabled", True))
        self.adopt_manual = bool(p.get("adopt_manual", True))
        self.activation_pct = float(p.get("trailing_activation_pct", 0.4))
        self.distance_pct = float(p.get("trailing_distance_pct", 0.35))
        self.distance_atr_mult = float(p.get("trailing_distance_atr_mult", 1.4))
        self.min_distance_pct = float(p.get("trailing_min_distance_pct", 0.0))
        self.breakeven_pct = float(p.get("breakeven_after_pct", 0.25))
        self.lock_initial_sl = bool(p.get("lock_initial_sl", False))
        self.atr_period = int(p.get("atr_period", 14))
        self.notify_trailing = bool(p.get("notify_trailing_telegram", False))
        self.exit_cfg = ExitManagementConfig.from_cfg(p)
        self._default_profile = TrailingProfile.from_positions_cfg(p)
        self._pump_dump_profile = TrailingProfile.from_positions_cfg(
            p, subsection="pump_dump_trailing"
        )
        ps = cfg.get("position_sync", {}) if isinstance(cfg.get("position_sync"), dict) else {}
        self._sync_guard.enabled = bool(ps.get("alert_on_mismatch", True))
        self._sync_guard.alert_registry_mismatch = bool(
            ps.get("alert_registry_mismatch", False)
        )
        self._sync_guard.cooldown_sec = max(
            300.0, float(ps.get("alert_cooldown_sec", 3600))
        )
        self._sync_guard.max_alerts_per_cycle = max(
            1, int(ps.get("max_mismatch_alerts_per_cycle", 1))
        )
        self._auto_clean_stale_registry = bool(ps.get("auto_clean_stale_registry", True))
        self._auto_close_journal_ghosts = bool(ps.get("auto_close_journal_ghosts", True))
        self._liq_guard = LiquidationGuardConfig.from_cfg(cfg)

    def _profile_for(self, pos: TrackedPosition) -> TrailingProfile:
        if pos.pump_dump_mode or pos.symbol in self._pump_dump_symbols:
            return self._pump_dump_profile
        return self._default_profile

    def mark_bot_opened(
        self,
        symbol: str,
        *,
        take_profit: float = 0.0,
        stop_loss: float = 0.0,
        pump_dump: bool = False,
    ) -> None:
        sym = symbol.upper()
        now_iso = datetime.now(timezone.utc).isoformat()
        self._bot_symbols.add(sym)
        if pump_dump:
            self._pump_dump_symbols.add(sym)
        self._bot_levels[sym] = {
            "take_profit": float(take_profit or 0),
            "stop_loss": float(stop_loss or 0),
            "opened_at_utc": now_iso,
        }
        register_bot_open(
            self._data_dir,
            sym,
            stop_loss=stop_loss,
            take_profit=take_profit,
            source="orchestrator",
            pump_dump=pump_dump,
        )
        if sym in self._tracked:
            if take_profit > 0:
                self._tracked[sym].take_profit = take_profit
            if stop_loss > 0:
                self._tracked[sym].stop_loss = stop_loss
                self._tracked[sym].last_sl_sent = stop_loss
            if pump_dump:
                self._tracked[sym].pump_dump_mode = True
            if not self._tracked[sym].opened_at_utc:
                self._tracked[sym].opened_at_utc = now_iso

    @staticmethod
    def _atr_from_klines(klines: List[Dict], period: int = 14) -> float:
        if not klines or len(klines) < period + 2:
            return 0.0
        df = pd.DataFrame(klines)
        if "high" not in df.columns or "low" not in df.columns or "close" not in df.columns:
            return 0.0
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev = close.shift(1)
        tr = pd.concat([(high - low), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]
        return float(atr) if pd.notna(atr) else 0.0

    def _adopt_from_exchange(self, row: Dict) -> Optional[TrackedPosition]:
        sym = str(row.get("symbol", "")).upper()
        size = float(row.get("size", 0) or 0)
        if not sym or size <= 0:
            return None
        side_raw = str(row.get("side", "")).lower()
        side = "Buy" if side_raw == "buy" else "Sell"
        entry = float(row.get("avgPrice") or row.get("entryPrice") or row.get("markPrice") or 0)
        if entry <= 0:
            return None
        sl = float(row.get("stopLoss") or 0)
        tp = float(row.get("takeProfit") or 0)
        levels = self._bot_levels.get(sym, {})
        if tp <= 0:
            tp = float(levels.get("take_profit", 0) or 0)
        if sl <= 0:
            sl = float(levels.get("stop_loss", 0) or 0)
        opened_iso = str(levels.get("opened_at_utc") or "") or datetime.now(timezone.utc).isoformat()
        origin = "bot" if sym in self._bot_symbols else "manual"
        if origin == "manual" and not self.adopt_manual:
            return None
        mark = float(row.get("markPrice") or entry)
        return TrackedPosition(
            symbol=sym,
            side=side,
            entry=entry,
            qty=size,
            stop_loss=sl,
            take_profit=tp,
            best_price=mark,
            position_idx=int(row.get("positionIdx", 0) or 0),
            origin=origin,
            pump_dump_mode=sym in self._pump_dump_symbols,
            last_sl_sent=sl,
            opened_at_utc=opened_iso,
        )

    def _calc_trailing_sl(
        self,
        pos: TrackedPosition,
        price: float,
        atr: float,
        profile: TrailingProfile,
        *,
        be_pct_override: Optional[float] = None,
        distance_factor: float = 1.0,
    ) -> Optional[float]:
        entry = pos.entry
        is_long = pos.side == "Buy"
        p_pct = profit_pct(pos.side, entry, price)
        if is_long:
            pos.best_price = max(pos.best_price, price)
        else:
            pos.best_price = min(pos.best_price or entry, price)

        be_pct = be_pct_override if be_pct_override is not None else profile.breakeven_pct
        if p_pct < be_pct:
            return None
        if p_pct < profile.activation_pct:
            return None

        ref = pos.best_price if pos.best_price > 0 else price
        dist_pct = ref * profile.distance_pct / 100 * distance_factor
        dist_atr = atr * profile.distance_atr_mult * distance_factor if atr > 0 else 0.0
        dist = max(dist_pct, dist_atr)
        if profile.min_distance_pct > 0:
            dist = max(dist, ref * profile.min_distance_pct / 100)
        if dist <= 0:
            return None

        if is_long:
            new_sl = pos.best_price - dist
            # Безубыток у входа только после полного порога активации трейлинга (не early BE)
            if p_pct >= profile.activation_pct:
                new_sl = max(new_sl, entry * 1.001)
            if pos.stop_loss > 0:
                new_sl = max(new_sl, pos.stop_loss)
            if new_sl >= price:
                return None
            return new_sl
        new_sl = pos.best_price + dist
        if p_pct >= profile.activation_pct:
            new_sl = min(new_sl, entry * 0.999)
        if pos.stop_loss > 0:
            new_sl = min(new_sl, pos.stop_loss)
        if new_sl <= price:
            return None
        return new_sl

    def _log_note_close(self, pos: TrackedPosition, action: str, reason: str) -> None:
        logger.info(
            "EXIT %s %s action=%s reason=%s peak=%.2f%% age=%.0fm",
            pos.symbol,
            pos.side,
            action,
            reason,
            pos.peak_profit_pct,
            age_minutes(pos.opened_at_utc),
        )

    async def _try_close_position(self, exchange, pos: TrackedPosition, reason: str) -> Optional[str]:
        if not hasattr(exchange, "close_position"):
            return None
        res = await exchange.close_position(
            pos.symbol,
            pos.side,
            qty=pos.qty,
            position_idx=pos.position_idx,
        )
        if res.get("success") or res.get("orderId"):
            msg = f"⏹ Выход {pos.symbol} ({reason})"
            logger.info("%s origin=%s", msg, pos.origin)
            return msg
        err = str(res.get("error", ""))[:120]
        logger.warning("Close failed %s: %s", pos.symbol, err)
        return None

    async def manage(self, exchange, positions: List[Dict]) -> List[str]:
        """Трейлинг SL, time-stop, breakeven. Возвращает сообщения для лога/Telegram."""
        if (
            not self.enabled
            and not self.exit_cfg.enabled
            and not self._default_profile.tp_progress.enabled
        ):
            return []
        notes: List[str] = []
        live_syms = set()
        for row in positions:
            sym = str(row.get("symbol", "")).upper()
            if sym:
                live_syms.add(sym)

        journal = self._data_dir / "trades" / "trade_history.jsonl"
        if getattr(self, "_auto_close_journal_ghosts", True):
            close_journal_ghosts(
                journal,
                live_syms,
                protect_symbols=set(self._tracked.keys()),
            )
        if getattr(self, "_auto_clean_stale_registry", True):
            removed = reconcile_registry_with_exchange(self._data_dir, live_syms)
            for sym in removed:
                self._bot_symbols.discard(sym)
        self._bot_symbols = set(bot_symbols_from_registry(self._data_dir)) | symbols_open_in_journal(
            journal
        )

        notes.extend(
            self._sync_guard.check(
                bot_symbols=set(self._bot_symbols),
                live_symbols=live_syms,
                tracked=self._tracked,
            )
        )

        for sym in list(self._tracked.keys()):
            if sym not in live_syms:
                del self._tracked[sym]
                if sym in self._bot_symbols:
                    self._bot_symbols.discard(sym)
                    unregister_bot_symbol(self._data_dir, sym)

        for row in positions:
            adopted = self._adopt_from_exchange(row)
            if not adopted:
                continue
            sym = adopted.symbol
            if sym not in self._tracked:
                self._tracked[sym] = adopted
                notes.append(f"📌 Подхвачена позиция {sym} {adopted.side} ({adopted.origin})")
                logger.info("Adopted position %s %s origin=%s", sym, adopted.side, adopted.origin)
            else:
                t = self._tracked[sym]
                t.qty = adopted.qty
                t.entry = adopted.entry
                if adopted.opened_at_utc and not t.opened_at_utc:
                    t.opened_at_utc = adopted.opened_at_utc
                if adopted.take_profit > 0:
                    t.take_profit = adopted.take_profit

        for sym, pos in list(self._tracked.items()):
            row = next((p for p in positions if str(p.get("symbol", "")).upper() == sym), None)
            if not row:
                continue
            price = float(row.get("markPrice") or pos.entry)
            klines = await exchange.get_klines(sym, interval="15", limit=80)
            atr = self._atr_from_klines(klines, self.atr_period)
            p_pct = profit_pct(pos.side, pos.entry, price)
            pos.peak_profit_pct = max(pos.peak_profit_pct, p_pct)
            prog_atr = progress_in_atr(pos.side, pos.entry, price, atr)
            profile = self._profile_for(pos)

            liq_price = float(row.get("liqPrice") or 0)
            liq_hit, liq_reason = evaluate_liquidation_stop(
                side=pos.side,
                mark_price=price,
                liq_price=liq_price,
                cfg=self._liq_guard,
                origin=pos.origin,
            )
            if liq_hit:
                closed_msg = await self._try_close_position(exchange, pos, liq_reason)
                if closed_msg:
                    notes.append(f"🛡 {closed_msg}")
                    self._log_note_close(pos, "liquidation_stop", liq_reason)
                    del self._tracked[sym]
                continue

            action, action_reason = evaluate_exit_actions(
                cfg=profile.exit_management,
                side=pos.side,
                entry=pos.entry,
                price=price,
                atr=atr,
                opened_at_iso=pos.opened_at_utc,
                peak_profit_pct=pos.peak_profit_pct,
            )
            if action and action.startswith("close_"):
                closed_msg = await self._try_close_position(exchange, pos, action_reason)
                if closed_msg:
                    if self.notify_trailing or action == "close_time_stop":
                        notes.append(closed_msg)
                    self._log_note_close(pos, action, action_reason)
                    del self._tracked[sym]
                continue

            be_override = effective_breakeven_pct(
                profile.breakeven_pct,
                cfg=profile.exit_management,
                profit_pct_from_entry=p_pct,
            )
            dist_factor = 1.0
            if late_retrace_active(
                cfg=profile.exit_management,
                peak_profit_pct=pos.peak_profit_pct,
                current_profit_pct=p_pct,
            ):
                dist_factor = profile.exit_management.late_tighten_distance_factor

            if self.lock_initial_sl:
                continue

            # Любой перенос SL — только когда прибыль от цены входа >= trailing_activation_pct
            if p_pct < profile.activation_pct:
                continue

            new_sl = None
            if profile.tp_progress.enabled and pos.take_profit > 0:
                tp_res = evaluate_tp_progress_exit(
                    cfg=profile.tp_progress,
                    side=pos.side,
                    entry=pos.entry,
                    price=price,
                    take_profit=pos.take_profit,
                    current_sl=pos.stop_loss,
                    klines=klines or [],
                    atr=atr,
                    opened_at_iso=pos.opened_at_utc,
                    min_activation_profit_pct=profile.activation_pct,
                )
                if tp_res.suggested_sl is not None:
                    new_sl = tp_res.suggested_sl
                    if tp_res.phase and tp_res.phase != pos.tp_progress_phase:
                        pos.tp_progress_phase = tp_res.phase
                        logger.info(
                            "TP progress %s %s: %s",
                            sym,
                            pos.side,
                            tp_res.note,
                        )

            if self.enabled:
                trail_sl = self._calc_trailing_sl(
                    pos,
                    price,
                    atr,
                    profile,
                    be_pct_override=be_override,
                    distance_factor=dist_factor,
                )
                if trail_sl is not None:
                    if new_sl is None:
                        new_sl = trail_sl
                    elif pos.side == "Buy":
                        new_sl = max(new_sl, trail_sl)
                    else:
                        new_sl = min(new_sl, trail_sl)

            if new_sl is None:
                continue
            if pos.last_sl_sent > 0 and abs(new_sl - pos.last_sl_sent) / max(pos.last_sl_sent, 1e-9) < 0.0003:
                continue
            client = exchange._client
            if not hasattr(client, "update_stop_loss"):
                continue
            res = await client.update_stop_loss(sym, new_sl, position_idx=pos.position_idx)
            if res.get("success"):
                pos.stop_loss = new_sl
                pos.last_sl_sent = new_sl
                pos.trailing_active = True
                age = age_minutes(pos.opened_at_utc)
                mode = "pump/dump" if profile is self._pump_dump_profile else pos.origin
                msg = f"🔁 Трейлинг {sym} SL→{new_sl:.4f} ({mode}, {age:.0f}m)"
                if self.notify_trailing:
                    notes.append(msg)
                logger.info(msg)
        return notes
