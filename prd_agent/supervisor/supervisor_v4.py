"""
Supervisor V4 — единый надсмотрщик:
- виртуальные сделки, позиции, плечо, bi-hourly tuning (бывший TradeSupervisor)
- режимы DEFENSIVE/NORMAL/AGGRESSIVE, блок часов/символов, risk_pct (бывший MetaSupervisor V3)
"""
from __future__ import annotations

import json
import logging
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import yaml

from prd_agent.analysis.trade_analytics import load_closed_trades
from prd_agent.evolution.self_improver import SelfImprover
from prd_agent.signals.types import UnifiedSignal
from prd_agent.supervisor.position_tracker import PositionTracker
from prd_agent.supervisor.trade_advisor import LeverageAdvice, TradeAdvisor
from prd_agent.supervisor.skipped_signal_backtest import SkippedSignalBacktester
from prd_agent.supervisor.virtual_trade_engine import VirtualTradeEngine

logger = logging.getLogger("prd_agent.supervisor.v4")


class SupervisorMode(str, Enum):
    DEFENSIVE = "DEFENSIVE"
    NORMAL = "NORMAL"
    AGGRESSIVE = "AGGRESSIVE"


_MODE_RISK_MULT = {
    SupervisorMode.DEFENSIVE: 0.35,
    SupervisorMode.NORMAL: 1.0,
    SupervisorMode.AGGRESSIVE: 1.0,
}


@dataclass
class _MetaState:
    mode: SupervisorMode = SupervisorMode.NORMAL
    mode_changed_at: Optional[datetime] = None
    learned_bad_symbols: Set[str] = field(default_factory=set)
    learned_bad_hours: Set[int] = field(default_factory=set)
    panic_until: Optional[datetime] = None
    notes: List[str] = field(default_factory=list)


def load_supervisor_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """supervisor_v4 + fallback trade_supervisor + meta_supervisor_v3."""
    merged: Dict[str, Any] = {}
    for section in ("meta_supervisor_v3", "trade_supervisor", "supervisor_v4"):
        block = cfg.get(section)
        if isinstance(block, dict):
            merged.update(block)
    if "enabled" not in merged:
        merged["enabled"] = True
    return merged


class SupervisorV4:
    def __init__(
        self,
        cfg: Dict[str, Any],
        data_dir: Path,
        improver: SelfImprover,
    ):
        self.cfg = cfg
        self.improver = improver
        sup = load_supervisor_config(cfg)

        self.enabled = bool(sup.get("enabled", True))
        self.virtual_enabled = bool(sup.get("virtual_trades_enabled", True))
        self.interval_hours = float(sup.get("interval_hours", 2))
        self.tick_every_cycles = int(sup.get("tick_every_cycles", 15))
        self.mode_cooldown_minutes = int(sup.get("mode_cooldown_minutes", 120))
        self.min_risk_pct = float(sup.get("min_risk_pct", 0.1))
        self.max_risk_pct_cap = float(sup.get("max_risk_pct_cap", 1.5))
        self.lookback_hours = float(sup.get("lookback_hours", 168))
        self.min_trades_for_block = int(sup.get("min_trades_for_block", 4))
        self.max_symbol_wr_pct = float(sup.get("max_symbol_wr_pct", 38.0))
        self.max_symbol_loss_usdt = float(sup.get("max_symbol_loss_usdt", -8.0))
        self.max_hour_wr_pct = float(sup.get("max_hour_wr_pct", 35.0))
        self.max_hour_loss_usdt = float(sup.get("max_hour_loss_usdt", -12.0))
        self.panic_consecutive_losses = int(sup.get("panic_consecutive_losses", 3))
        self.panic_minutes = int(sup.get("panic_minutes", 90))
        self.defensive_day_loss_usdt = float(sup.get("defensive_day_loss_usdt", -15.0))
        self.aggressive_min_wr_pct = float(sup.get("aggressive_min_wr_pct", 55.0))
        self.aggressive_min_trades = int(sup.get("aggressive_min_trades", 6))

        trading = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
        seed_symbols = list(sup.get("seed_blocked_symbols") or [])
        cfg_blacklist = list(trading.get("symbol_blacklist") or [])
        self._seed_blocked_symbols = {
            str(s).upper() for s in (seed_symbols + cfg_blacklist) if str(s).strip()
        }
        seed_hours = sup.get("seed_blocked_utc_hours")
        if seed_hours is None:
            seed_hours = trading.get("block_entry_utc_hours") or []
        self._seed_blocked_hours = {int(h) % 24 for h in (seed_hours or [])}
        preferred = sup.get("preferred_utc_hours") or [4]
        self._preferred_hours = {int(h) % 24 for h in preferred}

        self.store_dir = data_dir / "supervisor"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.filters_path = self.store_dir / "dynamic_filters.yaml"
        self.notes_path = self.store_dir / "supervisor_notes.jsonl"
        self.meta_state_path = self.store_dir / "meta_state.json"
        self.learning_path = Path(
            sup.get("learning_data_path") or (data_dir / "learning_data.json")
        )
        journal = sup.get("journal_path") or (data_dir / "trades" / "trade_history.jsonl")
        self.journal_path = Path(journal)

        self.positions = PositionTracker(self.store_dir / "positions")
        self.virtual = VirtualTradeEngine(
            self.store_dir / "virtual",
            max_open=int(sup.get("virtual_max_open", 40)),
            max_age_hours=float(sup.get("virtual_max_age_hours", 72)),
        )
        self.leverage_advisor = TradeAdvisor(cfg)
        sb_cfg = sup.get("skipped_signal_backtest")
        if not isinstance(sb_cfg, dict):
            sb_cfg = {}
        if "enabled" not in sb_cfg:
            sb_cfg["enabled"] = bool(sup.get("skipped_backtest_enabled", True))
        self.skipped_bt_interval_hours = float(
            sup.get("skipped_backtest_interval_hours", 3.5)
        )
        self.skipped_bt = SkippedSignalBacktester(
            self.store_dir,
            cfg={"skipped_signal_backtest": sb_cfg},
        )
        self.auto_tune_filters_from_backtest = bool(
            sb_cfg.get(
                "auto_tune_filters",
                sup.get("auto_tune_filters_from_backtest", True),
            )
        )
        self._auto_tune_min_samples = int(sb_cfg.get("auto_tune_min_samples", 5))
        self._last_skipped_bt_at = datetime.now(timezone.utc).timestamp()
        self._last_skipped_bt_summary: Dict[str, Any] = {}
        self._last_filter_tunes: List[Dict[str, Any]] = []
        self._meta = self._load_meta_state()
        self._apply_learning_file()
        self._last_meta_snap: Dict[str, Any] = {}

    def _legacy_meta_state_path(self, data_dir: Path) -> Path:
        return data_dir / "meta_supervisor_v3" / "state.json"

    def _load_meta_state(self) -> _MetaState:
        path = self.meta_state_path
        if not path.exists():
            legacy = self._legacy_meta_state_path(self.store_dir.parent)
            if legacy.exists():
                try:
                    shutil.copy2(legacy, path)
                except OSError:
                    pass
        if not path.exists():
            return _MetaState()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return _MetaState()
        mode_raw = str(raw.get("mode", "NORMAL")).upper()
        try:
            mode = SupervisorMode(mode_raw)
        except ValueError:
            mode = SupervisorMode.NORMAL
        changed = None
        if raw.get("mode_changed_at"):
            try:
                changed = datetime.fromisoformat(
                    str(raw["mode_changed_at"]).replace("Z", "+00:00")
                )
            except ValueError:
                changed = None
        panic = None
        if raw.get("panic_until"):
            try:
                panic = datetime.fromisoformat(
                    str(raw["panic_until"]).replace("Z", "+00:00")
                )
            except ValueError:
                panic = None
        return _MetaState(
            mode=mode,
            mode_changed_at=changed,
            learned_bad_symbols={str(s).upper() for s in raw.get("learned_bad_symbols", [])},
            learned_bad_hours={int(h) % 24 for h in raw.get("learned_bad_hours", [])},
            panic_until=panic,
            notes=list(raw.get("notes", []) or [])[-20:],
        )

    def _save_meta_state(self) -> None:
        payload = {
            "mode": self._meta.mode.value,
            "mode_changed_at": (
                self._meta.mode_changed_at.isoformat() if self._meta.mode_changed_at else None
            ),
            "learned_bad_symbols": sorted(self._meta.learned_bad_symbols),
            "learned_bad_hours": sorted(self._meta.learned_bad_hours),
            "panic_until": (
                self._meta.panic_until.isoformat() if self._meta.panic_until else None
            ),
            "notes": self._meta.notes[-20:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.meta_state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _log_note(self, text: str, **extra: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "text": text,
            **extra,
        }
        with self.notes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _apply_learning_file(self) -> None:
        if not self.learning_path.exists():
            return
        try:
            data = json.loads(self.learning_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for sym, row in (data.get("symbols") or {}).items():
            pnl = float(row.get("pnl_usdt", row.get("pnl", 0)) or 0)
            wr = float(row.get("win_rate", row.get("wr", 100)) or 100)
            n = int(row.get("trades", row.get("n", 0)) or 0)
            if (
                n >= self.min_trades_for_block
                and pnl <= self.max_symbol_loss_usdt
                and wr <= self.max_symbol_wr_pct
            ):
                self._meta.learned_bad_symbols.add(str(sym).upper())
        for hour, row in (data.get("hours") or {}).items():
            try:
                h = int(hour) % 24
            except (TypeError, ValueError):
                continue
            pnl = float(row.get("pnl_usdt", row.get("pnl", 0)) or 0)
            wr = float(row.get("win_rate", row.get("wr", 100)) or 100)
            n = int(row.get("trades", row.get("n", 0)) or 0)
            if (
                n >= self.min_trades_for_block
                and pnl <= self.max_hour_loss_usdt
                and wr <= self.max_hour_wr_pct
            ):
                self._meta.learned_bad_hours.add(h)

    def _learn_from_journal(self) -> None:
        rows = load_closed_trades(self.journal_path, hours=self.lookback_hours)
        if not rows:
            return
        by_sym: Dict[str, List[float]] = defaultdict(list)
        by_hour: Dict[int, List[float]] = defaultdict(list)
        for r in rows:
            pnl = float(r.get("pnl", 0) or 0)
            sym = str(r.get("symbol", "") or "").upper()
            if sym:
                by_sym[sym].append(pnl)
            ts_raw = str(r.get("ts", ""))
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                by_hour[ts.hour].append(pnl)
            except ValueError:
                pass
        learned_syms: Set[str] = set()
        learned_hours: Set[int] = set()
        for sym, pnls in by_sym.items():
            n = len(pnls)
            if n < self.min_trades_for_block:
                continue
            total = sum(pnls)
            wr = sum(1 for p in pnls if p > 0) / n * 100
            if total <= self.max_symbol_loss_usdt and wr <= self.max_symbol_wr_pct:
                learned_syms.add(sym)
        for hour, pnls in by_hour.items():
            n = len(pnls)
            if n < self.min_trades_for_block:
                continue
            total = sum(pnls)
            wr = sum(1 for p in pnls if p > 0) / n * 100
            if total <= self.max_hour_loss_usdt and wr <= self.max_hour_wr_pct:
                learned_hours.add(hour)
        self._meta.learned_bad_symbols = learned_syms
        self._meta.learned_bad_hours = learned_hours

    def _mode_cooldown_active(self) -> bool:
        if not self._meta.mode_changed_at:
            return False
        elapsed = datetime.now(timezone.utc) - self._meta.mode_changed_at
        return elapsed < timedelta(minutes=self.mode_cooldown_minutes)

    def _set_mode(self, new_mode: SupervisorMode, reason: str) -> None:
        if new_mode == self._meta.mode:
            return
        if self._mode_cooldown_active():
            logger.debug(
                "SupervisorV4: mode %s→%s blocked by cooldown (%s)",
                self._meta.mode.value,
                new_mode.value,
                reason,
            )
            return
        old = self._meta.mode.value
        self._meta.mode = new_mode
        self._meta.mode_changed_at = datetime.now(timezone.utc)
        note = f"{old}→{new_mode.value}: {reason}"
        self._meta.notes.append(note)
        logger.info("Supervisor V4: %s", note)

    def tick_meta(
        self,
        *,
        day_pnl_usdt: float = 0.0,
        consecutive_losses: int = 0,
        recent_wr_pct: float = 0.0,
        recent_trades: int = 0,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        now = datetime.now(timezone.utc)
        self._apply_learning_file()
        self._learn_from_journal()

        if consecutive_losses >= self.panic_consecutive_losses:
            self._meta.panic_until = now + timedelta(minutes=self.panic_minutes)
            self._set_mode(SupervisorMode.DEFENSIVE, f"panic: {consecutive_losses} losses подряд")

        if self._meta.panic_until and now >= self._meta.panic_until:
            self._meta.panic_until = None

        target = SupervisorMode.NORMAL
        if self._meta.panic_until and now < self._meta.panic_until:
            target = SupervisorMode.DEFENSIVE
        elif day_pnl_usdt <= self.defensive_day_loss_usdt or consecutive_losses >= 2:
            target = SupervisorMode.DEFENSIVE
        elif (
            recent_trades >= self.aggressive_min_trades
            and recent_wr_pct >= self.aggressive_min_wr_pct
            and day_pnl_usdt > 0
            and not self._mode_cooldown_active()
        ):
            target = SupervisorMode.AGGRESSIVE
        self._set_mode(
            target,
            f"pnl={day_pnl_usdt:.2f} wr={recent_wr_pct:.0f}% losses={consecutive_losses}",
        )
        self._save_meta_state()
        self._last_meta_snap = self.meta_snapshot()
        return self._last_meta_snap

    async def run_cycle_tick(
        self,
        exchange,
        bot_symbols: Set[str],
        *,
        cycle_num: int = 0,
        day_pnl_usdt: float = 0.0,
        consecutive_losses: int = 0,
        recent_wr_pct: float = 0.0,
        recent_trades: int = 0,
    ) -> None:
        if not self.enabled:
            return
        snap = await self.positions.sync(exchange, bot_symbols)
        if snap.get("count", 0):
            logger.debug(
                "SupervisorV4 positions: total=%s bot=%s manual=%s",
                snap["count"],
                snap.get("bot"),
                snap.get("manual"),
            )
        if self.virtual_enabled:
            closed = await self.virtual.tick(exchange)
            for vt in closed:
                self._log_note(
                    f"virtual {vt.symbol} {vt.close_reason} pnl={vt.pnl_pct:.2f}%",
                    symbol=vt.symbol,
                    event="virtual_close",
                )
        if cycle_num > 0 and cycle_num % max(1, self.tick_every_cycles) == 0:
            meta_snap = self.tick_meta(
                day_pnl_usdt=day_pnl_usdt,
                consecutive_losses=consecutive_losses,
                recent_wr_pct=recent_wr_pct,
                recent_trades=recent_trades,
            )
            line = self.format_meta_status_line(meta_snap)
            if line:
                logger.info(line)

    def register_virtual_signal(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        take_profit: float,
        source: str,
        confidence: float,
        ledger_id: str = "",
    ) -> None:
        if not self.enabled or not self.virtual_enabled:
            return
        self.virtual.open_trade(
            symbol=symbol,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            source=source,
            confidence=confidence,
            ledger_id=ledger_id,
            real_status="received",
        )

    def note_signal_outcome(self, ledger_id: str, status: str, reason: str = "") -> None:
        if ledger_id:
            self.virtual.mark_real_status(ledger_id, status, reason)

    async def run_skipped_backtests_if_due(self, ledger, exchange) -> Optional[Dict[str, Any]]:
        """Бэктест пропущенных сигналов раз в 3–4 ч (по config)."""
        if not self.enabled or not self.skipped_bt.enabled:
            return None
        now = datetime.now(timezone.utc).timestamp()
        interval_sec = self.skipped_bt_interval_hours * 3600.0
        if self._last_skipped_bt_at > 0 and (now - self._last_skipped_bt_at) < interval_sec:
            return None
        self._last_skipped_bt_at = now
        summary = await self.skipped_bt.run_batch(ledger, exchange)
        self._last_skipped_bt_summary = summary
        if int(summary.get("tested", 0)) > 0:
            self._log_note(
                "skipped_signal_backtest",
                tested=summary.get("tested"),
                outcomes=summary.get("outcomes"),
            )
            logger.info(
                "SupervisorV4 skipped BT: tested=%s outcomes=%s",
                summary.get("tested"),
                summary.get("outcomes"),
            )
            if self.auto_tune_filters_from_backtest:
                applied = self._apply_backtest_filter_tunes()
                summary["filter_tunes_applied"] = len(applied)
                summary["filter_tunes"] = applied
                if applied:
                    logger.info(
                        "SupervisorV4 filter tune from backtest: %s change(s)",
                        len(applied),
                    )
        return summary

    def recommend_leverage(
        self,
        sig: UnifiedSignal,
        *,
        entry: float,
        stop_loss: float,
        take_profit: float,
    ) -> LeverageAdvice:
        virtual_stats = self.virtual.stats(24) if self.virtual_enabled else {}
        return self.leverage_advisor.recommend_leverage(
            sig,
            entry=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            virtual_stats=virtual_stats,
        )

    @property
    def state(self) -> _MetaState:
        """Обратная совместимость с MetaSupervisorV3."""
        return self._meta

    def blocked_symbols(self) -> Set[str]:
        return self._seed_blocked_symbols | self._meta.learned_bad_symbols

    def blocked_hours(self) -> Set[int]:
        return self._seed_blocked_hours | self._meta.learned_bad_hours

    def can_enter(self, symbol: str = "", utc_hour: Optional[int] = None) -> Tuple[bool, str]:
        if not self.enabled:
            return True, ""
        sym = str(symbol or "").upper()
        hour = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour

        if self._meta.panic_until and datetime.now(timezone.utc) < self._meta.panic_until:
            return False, "supervisor_v4: протокол восстановления после серии убытков"

        if sym and sym in self.blocked_symbols():
            return False, f"supervisor_v4: символ {sym} в чёрном списке"

        if hour in self.blocked_hours():
            if self._meta.mode != SupervisorMode.AGGRESSIVE or hour not in self._preferred_hours:
                return False, (
                    f"supervisor_v4: UTC час {hour} заблокирован "
                    f"(режим {self._meta.mode.value})"
                )

        if self._meta.mode == SupervisorMode.DEFENSIVE and hour not in self._preferred_hours:
            return False, (
                f"supervisor_v4: DEFENSIVE — торговля только в часы "
                f"{sorted(self._preferred_hours)} UTC"
            )

        return True, ""

    def effective_risk_pct(self, base_risk_pct: float) -> float:
        if not self.enabled:
            return base_risk_pct
        mult = _MODE_RISK_MULT.get(self._meta.mode, 1.0)
        hour = datetime.now(timezone.utc).hour
        if hour in self.blocked_hours():
            mult = min(mult, 0.15)
        if hour not in self._preferred_hours and self._meta.mode == SupervisorMode.DEFENSIVE:
            mult = min(mult, 0.2)
        scaled = base_risk_pct * mult
        scaled = max(self.min_risk_pct, scaled)
        return min(scaled, self.max_risk_pct_cap, base_risk_pct)

    def meta_snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self._meta.mode.value,
            "blocked_symbols": sorted(self.blocked_symbols()),
            "blocked_hours": sorted(self.blocked_hours()),
            "preferred_hours": sorted(self._preferred_hours),
            "panic_active": bool(
                self._meta.panic_until
                and datetime.now(timezone.utc) < self._meta.panic_until
            ),
            "mode_cooldown_active": self._mode_cooldown_active(),
            "min_risk_pct": self.min_risk_pct,
        }

    @staticmethod
    def format_meta_status_line(snap: Dict[str, Any]) -> str:
        if not snap.get("enabled"):
            return ""
        panic = " 🛑" if snap.get("panic_active") else ""
        return (
            f"SupervisorV4: {snap.get('mode', 'NORMAL')}{panic} | "
            f"часы−{len(snap.get('blocked_hours', []))} "
            f"симв−{len(snap.get('blocked_symbols', []))}"
        )

    def _count_time_stop_closes(self, hours: float = 24) -> int:
        if not self.notes_path.is_file():
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
        n = 0
        for line in self.notes_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event") != "exit_time_stop":
                continue
            ts = row.get("ts", "")
            try:
                t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                continue
            if t >= cutoff:
                n += 1
        return n

    def _proposals_from_skipped_backtest_by_reason(self) -> List[Dict[str, Any]]:
        """Подстройка фильтров по результатам бэктеста пропущенных сигналов."""
        proposals: List[Dict[str, Any]] = []
        min_n = max(3, self._auto_tune_min_samples)
        by_reason = self.skipped_bt.stats_by_reason(24)

        def _add(path: List[str], delta: float, summary: str, justification: str) -> None:
            proposals.append(
                {
                    "risk": "low",
                    "path": path,
                    "delta": delta,
                    "summary": summary,
                    "justification": justification,
                }
            )

        for bucket, st in by_reason.items():
            n = int(st.get("n", 0))
            if n < min_n:
                continue
            wr = float(st.get("win_rate_pct", 0))
            avg = float(st.get("avg_pnl_pct", 0))
            tp = int(st.get("tp_hits", 0))
            sl = int(st.get("sl_hits", 0))
            just = f"skipped_bt[{bucket}] n={n} WR={wr}% avg={avg}% TP={tp} SL={sl}"

            if bucket == "quality_gate_rr":
                if wr >= 52 and tp >= sl:
                    _add(
                        ["quality_gate", "min_rr_ratio"],
                        -0.05,
                        "SupervisorV4: ослабить min_rr — пропуски по RR были бы в плюс",
                        just,
                    )
                elif wr < 38 and sl >= tp:
                    _add(
                        ["quality_gate", "min_rr_ratio"],
                        +0.05,
                        "SupervisorV4: ужесточить min_rr — пропуски по RR спасли от SL",
                        just,
                    )
            elif bucket == "quality_gate_conf":
                if wr >= 52:
                    _add(
                        ["quality_gate", "min_confidence"],
                        -0.02,
                        "SupervisorV4: снизить quality_gate confidence — пропуски выгодны",
                        just,
                    )
                elif wr < 38:
                    _add(
                        ["quality_gate", "min_confidence"],
                        +0.02,
                        "SupervisorV4: повысить quality_gate confidence — пропуски спасли",
                        just,
                    )
            elif bucket == "entry_guard":
                if wr >= 52:
                    _add(
                        ["entry_guard", "max_market_drift_pct"],
                        +0.001,
                        "SupervisorV4: чуть шире market drift — пропуски по drift выгодны",
                        just,
                    )
                    _add(
                        ["entry_guard", "max_limit_drift_pct"],
                        +0.001,
                        "SupervisorV4: чуть шире limit drift — пропуски по drift выгодны",
                        just,
                    )
                elif wr < 38:
                    _add(
                        ["entry_guard", "max_market_drift_pct"],
                        -0.001,
                        "SupervisorV4: уже market drift — пропуски по drift оправданы",
                        just,
                    )
            elif bucket == "pullback_entry":
                if wr >= 52:
                    _add(
                        ["pullback_entry", "min_momentum_pct"],
                        +0.05,
                        "SupervisorV4: мягче anti-chase — пропуски по откату выгодны",
                        just,
                    )
                elif wr < 38:
                    _add(
                        ["pullback_entry", "min_momentum_pct"],
                        -0.05,
                        "SupervisorV4: жёстче anti-chase — пропуски по откату спасли",
                        just,
                    )
            elif bucket in ("quality_gate", "unknown") and wr >= 55:
                _add(
                    ["signals", "min_analysis_confidence"],
                    -0.02,
                    "SupervisorV4: снизить min_analysis — пропуски quality_gate выгодны",
                    just,
                )

        sb = self.skipped_bt.stats(24)
        if int(sb.get("n", 0)) >= min_n and float(sb.get("win_rate_pct", 0)) >= 55:
            _add(
                ["trading", "min_signal_confidence"],
                -0.02,
                "SupervisorV4: снизить min_signal_confidence — бэктест пропусков в плюс",
                f"skipped_bt total WR={sb.get('win_rate_pct')}%",
            )
        if int(sb.get("n", 0)) >= min_n and float(sb.get("win_rate_pct", 0)) < 35:
            _add(
                ["trading", "min_signal_confidence"],
                +0.02,
                "SupervisorV4: повысить min_signal_confidence — бэктест пропусков в минус",
                f"skipped_bt total WR={sb.get('win_rate_pct')}%",
            )
        return proposals

    def _apply_backtest_filter_tunes(self) -> List[Dict[str, Any]]:
        proposals = self._proposals_from_skipped_backtest_by_reason()
        if not proposals:
            return []
        applied: List[Dict[str, Any]] = []
        if self.improver.enabled:
            applied = self.improver.process_proposals(proposals)
        self._last_filter_tunes = applied
        self._persist_backtest_filter_hints(applied)
        return applied

    def _persist_backtest_filter_hints(self, applied: List[Dict[str, Any]]) -> None:
        filters: Dict[str, Any] = {}
        if self.filters_path.exists():
            try:
                filters = yaml.safe_load(self.filters_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                filters = {}
        filters["skipped_backtest_by_reason"] = self.skipped_bt.stats_by_reason(24)
        filters["last_filter_tunes_from_backtest"] = [
            {
                "summary": a.get("summary", ""),
                "path": a.get("path"),
                "old": a.get("old"),
                "new": a.get("new"),
            }
            for a in applied
        ]
        filters["filter_tune_updated_at"] = datetime.now(timezone.utc).isoformat()
        self.filters_path.write_text(
            yaml.safe_dump(filters, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    def _analyze_ledger_skips(self, ledger, hours: float = 2) -> Dict[str, Any]:
        entries = ledger.recent(hours) if hasattr(ledger, "recent") else []
        reasons = Counter()
        for e in entries:
            if str(e.get("status", "")) == "skipped":
                r = str(e.get("reason", "") or "unknown")[:120]
                key = r.split(":")[0] if ":" in r else r
                reasons[key] += 1
        return {"skipped_by_reason": dict(reasons.most_common(8)), "total": len(entries)}

    def _proposals_from_supervisor(
        self,
        *,
        report_2h: Dict[str, Any],
        report_24h: Dict[str, Any],
        virtual_2h: Dict[str, Any],
        virtual_24h: Dict[str, Any],
        skip_analysis: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        proposals: List[Dict[str, Any]] = []
        rr_skips = sum(
            v
            for k, v in skip_analysis.get("skipped_by_reason", {}).items()
            if "quality_gate" in k and "RR" in k
        )
        if rr_skips >= 3:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["quality_gate", "min_rr_ratio"],
                    "delta": -0.1,
                    "summary": "SupervisorV4: ослабить min_rr — много Skip по RR",
                    "justification": f"RR skips={rr_skips} за 2ч",
                }
            )
        v_wr = float(virtual_24h.get("win_rate_pct", 0))
        v_n = int(virtual_24h.get("closed", 0))
        real_wr = float(report_24h.get("win_rate_pct", 0))
        real_pnl = float(report_24h.get("pnl_usdt", 0))
        if v_n >= 8 and v_wr >= 52 and real_pnl < 0:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["signals", "min_analysis_confidence"],
                    "delta": -0.02,
                    "summary": "SupervisorV4: виртуальные в плюсе — чуть снизить порог входа",
                    "justification": f"virtual WR={v_wr}% real PnL={real_pnl}",
                }
            )
        if v_n >= 10 and v_wr < 38:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["trading", "min_signal_confidence"],
                    "delta": +0.02,
                    "summary": "SupervisorV4: виртуальные слабые — ужесточить conf",
                    "justification": f"virtual WR={v_wr}%",
                }
            )
        if real_wr < 35 and float(report_24h.get("closed_trades", 0)) >= 5:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["risk", "cooldown_after_loss_sec"],
                    "delta": +120,
                    "summary": "SupervisorV4: увеличить паузу после убытка",
                    "justification": f"real WR={real_wr}%",
                }
            )
        if self._meta.mode == SupervisorMode.DEFENSIVE and real_pnl < -5:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["trading", "risk_pct_per_trade"],
                    "delta": -0.05,
                    "summary": "SupervisorV4: DEFENSIVE — снизить risk_pct",
                    "justification": f"mode=DEFENSIVE pnl={real_pnl}",
                }
            )
        not_opened = int(report_2h.get("ledger_not_opened", 0))
        sig_total = max(int(report_2h.get("signals_total", 1)), 1)
        if not_opened > sig_total * 0.85 and rr_skips < 2:
            proposals.append(
                {
                    "risk": "low",
                    "path": ["quality_gate", "min_rr_ratio"],
                    "delta": -0.05,
                    "summary": "SupervisorV4: много сигналов не доходит до ордера",
                    "justification": f"not_opened ratio high, skips={skip_analysis}",
                }
            )
        proposals.extend(self._proposals_from_skipped_backtest_by_reason())
        return proposals

    def _update_dynamic_filters(self, skip_analysis: Dict[str, Any]) -> List[str]:
        notes: List[str] = []
        filters: Dict[str, Any] = {}
        if self.filters_path.exists():
            try:
                filters = yaml.safe_load(self.filters_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                filters = {}
        filters.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        filters["skip_stats_2h"] = skip_analysis.get("skipped_by_reason", {})
        filters["meta_mode"] = self._meta.mode.value
        filters["blocked_symbols"] = sorted(self.blocked_symbols())
        filters["blocked_hours"] = sorted(self.blocked_hours())
        rr_heavy = sum(
            1
            for k in skip_analysis.get("skipped_by_reason", {})
            if "quality_gate" in k
        )
        if rr_heavy >= 2:
            filters["hint"] = "Рассмотреть min_rr_ratio 2.0 и проверку SR-зон"
            notes.append("dynamic_filters: hint RR")
        filters["require_sl_tp"] = True
        self.filters_path.write_text(
            yaml.safe_dump(filters, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )
        return notes

    async def run_bi_hourly_review(
        self,
        *,
        ledger,
        report_2h: Dict[str, Any],
        report_24h: Dict[str, Any],
    ) -> Dict[str, Any]:
        virtual_2h = self.virtual.stats(2) if self.virtual_enabled else {}
        virtual_24h = self.virtual.stats(24) if self.virtual_enabled else {}
        skip_analysis = self._analyze_ledger_skips(ledger, 2)
        filter_notes = self._update_dynamic_filters(skip_analysis)
        proposals = self._proposals_from_supervisor(
            report_2h=report_2h,
            report_24h=report_24h,
            virtual_2h=virtual_2h,
            virtual_24h=virtual_24h,
            skip_analysis=skip_analysis,
        )
        time_stop_n = self._count_time_stop_closes(hours=24)
        exit_props = self.improver.propose_exit_tuning(
            real_wr_24h=float(report_24h.get("win_rate_pct", 0)),
            real_pnl_24h=float(report_24h.get("pnl_usdt", 0)),
            virtual_wr_24h=float(virtual_24h.get("win_rate_pct", 0)),
            virtual_n_24h=int(virtual_24h.get("closed", 0)),
            time_stop_closes_24h=time_stop_n,
        )
        proposals.extend(exit_props)
        applied: List[Dict[str, Any]] = []
        if self.improver.enabled:
            applied = self.improver.process_proposals(proposals)
        summary = {
            "virtual_2h": virtual_2h,
            "virtual_24h": virtual_24h,
            "skip_analysis": skip_analysis,
            "proposals_count": len(proposals),
            "applied_count": len(applied),
            "filter_notes": filter_notes,
            "position_snapshot": {},
            "meta": self.meta_snapshot(),
            "skipped_backtest_24h": self.skipped_bt.stats(24),
            "skipped_backtest_by_reason": self.skipped_bt.stats_by_reason(24),
            "skipped_backtest_last": self._last_skipped_bt_summary,
            "filter_tunes_from_backtest": self._last_filter_tunes,
        }
        if self.positions.snapshot_path.exists():
            try:
                summary["position_snapshot"] = json.loads(
                    self.positions.snapshot_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                pass
        self._log_note(
            "bi_hourly review",
            virtual_2h=virtual_2h,
            applied=len(applied),
            mode=self._meta.mode.value,
        )
        logger.info(
            "SupervisorV4 2h: mode=%s virtual_closed=%s applied_tunes=%s",
            self._meta.mode.value,
            virtual_2h.get("closed"),
            len(applied),
        )
        return summary

    @staticmethod
    def format_report_section(supervisor_summary: Dict[str, Any]) -> List[str]:
        if not supervisor_summary:
            return []
        v2 = supervisor_summary.get("virtual_2h") or {}
        v24 = supervisor_summary.get("virtual_24h") or {}
        snap = supervisor_summary.get("position_snapshot") or {}
        meta = supervisor_summary.get("meta") or {}
        lines = [
            "",
            "<b>🧭 Supervisor V4</b>",
            f"• Режим: <b>{meta.get('mode', 'NORMAL')}</b>"
            + (" 🛑" if meta.get("panic_active") else ""),
            f"• Блок: {len(meta.get('blocked_hours', []))} ч UTC, "
            f"{len(meta.get('blocked_symbols', []))} символов",
            "",
            "<b>🧪 Виртуальные сделки (по сигналам бота)</b>",
            f"• За 2ч: закрыто {v2.get('closed', 0)} | WR {v2.get('win_rate_pct', 0)}% | "
            f"ср.PnL {v2.get('avg_pnl_pct', 0):+.3f}%",
            f"• За 24ч: закрыто {v24.get('closed', 0)} | WR {v24.get('win_rate_pct', 0)}% | "
            f"открыто сейчас {v24.get('open', 0)}",
            "",
            "<b>📌 Позиции на бирже (бот + ручные)</b>",
            f"• Всего: {snap.get('count', 0)} (бот {snap.get('bot', 0)}, ручные {snap.get('manual', 0)})",
        ]
        for p in (snap.get("positions") or [])[:6]:
            lines.append(
                f"• {p.get('symbol')} {p.get('side')} [{p.get('origin')}] "
                f"uPnL={float(p.get('upnl', 0)):+.2f}"
            )
        sb24 = supervisor_summary.get("skipped_backtest_24h") or {}
        if int(sb24.get("n", 0)) > 0:
            lines.append("")
            lines.append("<b>📉 Бэктест пропущенных сигналов (24ч)</b>")
            lines.append(
                f"• Проверено: {sb24.get('n', 0)} | WR {sb24.get('win_rate_pct', 0)}% | "
                f"ср.PnL {sb24.get('avg_pnl_pct', 0):+.3f}%"
            )
            lines.append(
                f"• TP {sb24.get('tp_hits', 0)} | SL {sb24.get('sl_hits', 0)} | "
                f"ещё открыты {sb24.get('still_open', 0)}"
            )
        tunes = supervisor_summary.get("filter_tunes_from_backtest") or []
        if tunes:
            lines.append("")
            lines.append("<b>🛠 Фильтры подстроены по бэктесту пропусков</b>")
            for t in tunes[:4]:
                lines.append(f"• {str(t.get('summary', ''))[:70]}")
        by_r = supervisor_summary.get("skipped_backtest_by_reason") or {}
        if by_r:
            lines.append("")
            lines.append("<b>📊 Пропуски по причинам (бэктест 24ч)</b>")
            for bucket, st in list(by_r.items())[:4]:
                lines.append(
                    f"• {bucket}: n={st.get('n')} WR={st.get('win_rate_pct')}% "
                    f"avg={st.get('avg_pnl_pct'):+.2f}%"
                )
        skips = supervisor_summary.get("skip_analysis", {}).get("skipped_by_reason", {})
        if skips:
            lines.append("")
            lines.append("<b>⏭ Пропуски сигналов (2ч)</b>")
            for reason, cnt in list(skips.items())[:5]:
                lines.append(f"• {cnt}× {reason[:60]}")
        if supervisor_summary.get("applied_count", 0):
            lines.append("")
            lines.append(
                f"<b>🛠 SupervisorV4 применил правок config:</b> "
                f"{supervisor_summary.get('applied_count')}"
            )
        return lines
