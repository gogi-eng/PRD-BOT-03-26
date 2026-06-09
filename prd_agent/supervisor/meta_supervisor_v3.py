"""
Meta-Supervisor V3: режим DEFENSIVE/NORMAL/AGGRESSIVE, блок убыточных часов и символов,
адаптивный risk_pct. Не ослабляет RiskGuard — только добавляет ограничения.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from prd_agent.analysis.trade_analytics import load_closed_trades, summarize_trades

logger = logging.getLogger("prd_agent.supervisor.meta_v3")


class SupervisorMode(str, Enum):
    DEFENSIVE = "DEFENSIVE"
    NORMAL = "NORMAL"
    AGGRESSIVE = "AGGRESSIVE"


_MODE_RANK = {
    SupervisorMode.DEFENSIVE: 0,
    SupervisorMode.NORMAL: 1,
    SupervisorMode.AGGRESSIVE: 2,
}

_MODE_RISK_MULT = {
    SupervisorMode.DEFENSIVE: 0.35,
    SupervisorMode.NORMAL: 1.0,
    SupervisorMode.AGGRESSIVE: 1.0,
}


@dataclass
class MetaSupervisorState:
    mode: SupervisorMode = SupervisorMode.NORMAL
    mode_changed_at: Optional[datetime] = None
    learned_bad_symbols: Set[str] = field(default_factory=set)
    learned_bad_hours: Set[int] = field(default_factory=set)
    last_tick_at: Optional[datetime] = None
    panic_until: Optional[datetime] = None
    notes: List[str] = field(default_factory=list)


class MetaSupervisorV3:
    def __init__(self, cfg: Dict[str, Any], data_dir: Path):
        ms = cfg.get("meta_supervisor_v3", {})
        if not isinstance(ms, dict):
            ms = {}
        self.enabled = bool(ms.get("enabled", True))
        self.mode_cooldown_minutes = int(ms.get("mode_cooldown_minutes", 120))
        self.tick_every_cycles = int(ms.get("tick_every_cycles", 15))
        self.min_risk_pct = float(ms.get("min_risk_pct", 0.1))
        self.max_risk_pct_cap = float(ms.get("max_risk_pct_cap", 1.5))
        self.lookback_hours = float(ms.get("lookback_hours", 168))
        self.min_trades_for_block = int(ms.get("min_trades_for_block", 4))
        self.max_symbol_wr_pct = float(ms.get("max_symbol_wr_pct", 38.0))
        self.max_symbol_loss_usdt = float(ms.get("max_symbol_loss_usdt", -8.0))
        self.max_hour_wr_pct = float(ms.get("max_hour_wr_pct", 35.0))
        self.max_hour_loss_usdt = float(ms.get("max_hour_loss_usdt", -12.0))
        self.panic_consecutive_losses = int(ms.get("panic_consecutive_losses", 3))
        self.panic_minutes = int(ms.get("panic_minutes", 90))
        self.defensive_day_loss_usdt = float(ms.get("defensive_day_loss_usdt", -15.0))
        self.aggressive_min_wr_pct = float(ms.get("aggressive_min_wr_pct", 55.0))
        self.aggressive_min_trades = int(ms.get("aggressive_min_trades", 6))

        trading = cfg.get("trading", {}) if isinstance(cfg.get("trading"), dict) else {}
        seed_symbols = list(ms.get("seed_blocked_symbols") or [])
        cfg_blacklist = list(trading.get("symbol_blacklist") or [])
        self._seed_blocked_symbols = {
            str(s).upper() for s in (seed_symbols + cfg_blacklist) if str(s).strip()
        }
        seed_hours = ms.get("seed_blocked_utc_hours")
        if seed_hours is None:
            seed_hours = trading.get("block_entry_utc_hours") or []
        self._seed_blocked_hours = {int(h) % 24 for h in (seed_hours or [])}
        preferred = ms.get("preferred_utc_hours") or [4, 18]
        self._preferred_hours = {int(h) % 24 for h in preferred}

        self.store_dir = data_dir / "meta_supervisor_v3"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.store_dir / "state.json"
        self.learning_path = Path(ms.get("learning_data_path") or (data_dir / "learning_data.json"))
        journal = ms.get("journal_path") or (data_dir / "trades" / "trade_history.jsonl")
        self.journal_path = Path(journal)

        self.state = self._load_state()
        self._apply_learning_file()

    def _load_state(self) -> MetaSupervisorState:
        if not self.state_path.exists():
            return MetaSupervisorState()
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return MetaSupervisorState()
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
        return MetaSupervisorState(
            mode=mode,
            mode_changed_at=changed,
            learned_bad_symbols={str(s).upper() for s in raw.get("learned_bad_symbols", [])},
            learned_bad_hours={int(h) % 24 for h in raw.get("learned_bad_hours", [])},
            last_tick_at=None,
            panic_until=panic,
            notes=list(raw.get("notes", []) or [])[-20:],
        )

    def _save_state(self) -> None:
        payload = {
            "mode": self.state.mode.value,
            "mode_changed_at": (
                self.state.mode_changed_at.isoformat() if self.state.mode_changed_at else None
            ),
            "learned_bad_symbols": sorted(self.state.learned_bad_symbols),
            "learned_bad_hours": sorted(self.state.learned_bad_hours),
            "panic_until": (
                self.state.panic_until.isoformat() if self.state.panic_until else None
            ),
            "notes": self.state.notes[-20:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

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
            if n >= self.min_trades_for_block and pnl <= self.max_symbol_loss_usdt and wr <= self.max_symbol_wr_pct:
                self.state.learned_bad_symbols.add(str(sym).upper())
        for hour, row in (data.get("hours") or {}).items():
            try:
                h = int(hour) % 24
            except (TypeError, ValueError):
                continue
            pnl = float(row.get("pnl_usdt", row.get("pnl", 0)) or 0)
            wr = float(row.get("win_rate", row.get("wr", 100)) or 100)
            n = int(row.get("trades", row.get("n", 0)) or 0)
            if n >= self.min_trades_for_block and pnl <= self.max_hour_loss_usdt and wr <= self.max_hour_wr_pct:
                self.state.learned_bad_hours.add(h)

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
        self.state.learned_bad_symbols = learned_syms
        self.state.learned_bad_hours = learned_hours

    def _mode_cooldown_active(self) -> bool:
        if not self.state.mode_changed_at:
            return False
        elapsed = datetime.now(timezone.utc) - self.state.mode_changed_at
        return elapsed < timedelta(minutes=self.mode_cooldown_minutes)

    def _set_mode(self, new_mode: SupervisorMode, reason: str) -> None:
        if new_mode == self.state.mode:
            return
        if self._mode_cooldown_active():
            logger.debug(
                "MetaV3: mode change %s→%s blocked by cooldown (%s)",
                self.state.mode.value,
                new_mode.value,
                reason,
            )
            return
        old = self.state.mode.value
        self.state.mode = new_mode
        self.state.mode_changed_at = datetime.now(timezone.utc)
        note = f"{old}→{new_mode.value}: {reason}"
        self.state.notes.append(note)
        logger.info("Meta-Supervisor V3: %s", note)

    def tick(
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
        self.state.last_tick_at = now
        self._apply_learning_file()
        self._learn_from_journal()

        if consecutive_losses >= self.panic_consecutive_losses:
            self.state.panic_until = now + timedelta(minutes=self.panic_minutes)
            self._set_mode(SupervisorMode.DEFENSIVE, f"panic: {consecutive_losses} losses подряд")

        if self.state.panic_until and now >= self.state.panic_until:
            self.state.panic_until = None

        target = SupervisorMode.NORMAL
        if self.state.panic_until and now < self.state.panic_until:
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
        self._set_mode(target, f"pnl={day_pnl_usdt:.2f} wr={recent_wr_pct:.0f}% losses={consecutive_losses}")
        self._save_state()
        return self.snapshot()

    def blocked_symbols(self) -> Set[str]:
        return self._seed_blocked_symbols | self.state.learned_bad_symbols

    def blocked_hours(self) -> Set[int]:
        return self._seed_blocked_hours | self.state.learned_bad_hours

    def can_enter(self, symbol: str = "", utc_hour: Optional[int] = None) -> Tuple[bool, str]:
        if not self.enabled:
            return True, ""
        sym = str(symbol or "").upper()
        hour = utc_hour if utc_hour is not None else datetime.now(timezone.utc).hour

        if self.state.panic_until and datetime.now(timezone.utc) < self.state.panic_until:
            return False, "meta_v3: протокол восстановления после серии убытков"

        if sym and sym in self.blocked_symbols():
            return False, f"meta_v3: символ {sym} в чёрном списке супервизора"

        if hour in self.blocked_hours():
            if self.state.mode != SupervisorMode.AGGRESSIVE or hour not in self._preferred_hours:
                return False, f"meta_v3: UTC час {hour} заблокирован (режим {self.state.mode.value})"

        if self.state.mode == SupervisorMode.DEFENSIVE and hour not in self._preferred_hours:
            return False, f"meta_v3: DEFENSIVE — торговля только в часы {sorted(self._preferred_hours)} UTC"

        return True, ""

    def effective_risk_pct(self, base_risk_pct: float) -> float:
        if not self.enabled:
            return base_risk_pct
        mult = _MODE_RISK_MULT.get(self.state.mode, 1.0)
        hour = datetime.now(timezone.utc).hour
        if hour in self.blocked_hours():
            mult = min(mult, 0.15)
        if hour not in self._preferred_hours and self.state.mode == SupervisorMode.DEFENSIVE:
            mult = min(mult, 0.2)
        scaled = base_risk_pct * mult
        scaled = max(self.min_risk_pct, scaled)
        return min(scaled, self.max_risk_pct_cap, base_risk_pct)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.state.mode.value,
            "blocked_symbols": sorted(self.blocked_symbols()),
            "blocked_hours": sorted(self.blocked_hours()),
            "preferred_hours": sorted(self._preferred_hours),
            "panic_active": bool(
                self.state.panic_until
                and datetime.now(timezone.utc) < self.state.panic_until
            ),
            "mode_cooldown_active": self._mode_cooldown_active(),
            "min_risk_pct": self.min_risk_pct,
        }

    @staticmethod
    def format_status_line(snap: Dict[str, Any]) -> str:
        if not snap.get("enabled"):
            return ""
        panic = " 🛑" if snap.get("panic_active") else ""
        return (
            f"MetaV3: {snap.get('mode', 'NORMAL')}{panic} | "
            f"часы−{len(snap.get('blocked_hours', []))} "
            f"симв−{len(snap.get('blocked_symbols', []))}"
        )
