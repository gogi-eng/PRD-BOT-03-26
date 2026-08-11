"""
Сторож закрытий: все открытые сделки (ручные + бот).

Если закрытие некорректное и/или убытков подряд больше двух —
сразу алерт в Telegram.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

logger = logging.getLogger("prd_agent.positions.close_watchdog")


@dataclass
class CloseWatchdogConfig:
    enabled: bool = True
    # «больше двух убытков» → алерт когда consecutive_losses > 2 (на 3-м)
    alert_when_losses_gt: int = 2
    # столько же для некорректных закрытий подряд / в окне
    alert_when_bad_closes_gt: int = 2
    fast_loss_minutes: float = 5.0
    # убыток «слишком крупный» относительно типичного SL (~0.5–1%) — тоже bad
    large_loss_pct: float = 3.0
    notify_telegram: bool = True
    # не спамить один и тот же алерт чаще чем раз в N секунд
    alert_cooldown_sec: float = 120.0
    state_filename: str = "close_watchdog_state.json"

    @classmethod
    def from_cfg(cls, cfg: Mapping[str, Any]) -> "CloseWatchdogConfig":
        pos = cfg.get("positions") if isinstance(cfg.get("positions"), dict) else {}
        raw = pos.get("close_watchdog") if isinstance(pos.get("close_watchdog"), dict) else {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            alert_when_losses_gt=int(raw.get("alert_when_losses_gt", 2) or 2),
            alert_when_bad_closes_gt=int(raw.get("alert_when_bad_closes_gt", 2) or 2),
            fast_loss_minutes=float(raw.get("fast_loss_minutes", 5.0) or 5.0),
            large_loss_pct=float(raw.get("large_loss_pct", 3.0) or 3.0),
            notify_telegram=bool(raw.get("notify_telegram", True)),
            alert_cooldown_sec=float(raw.get("alert_cooldown_sec", 120.0) or 120.0),
            state_filename=str(raw.get("state_filename") or "close_watchdog_state.json"),
        )


@dataclass
class TrackedOpen:
    symbol: str
    side: str
    entry: float
    qty: float
    origin: str  # manual | bot
    opened_at_ms: float
    stop_loss: float = 0.0
    take_profit: float = 0.0


@dataclass
class CloseWatchdog:
    cfg: CloseWatchdogConfig
    data_dir: Path
    consecutive_losses: int = 0
    consecutive_bad: int = 0
    open_map: Dict[str, TrackedOpen] = field(default_factory=dict)
    _last_alert_ts: float = 0.0
    _seen_close_ids: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self._load_state()

    def _state_path(self) -> Path:
        return self.data_dir / "risk" / self.cfg.state_filename

    def _load_state(self) -> None:
        path = self._state_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.consecutive_losses = int(raw.get("consecutive_losses", 0) or 0)
        self.consecutive_bad = int(raw.get("consecutive_bad", 0) or 0)
        seen = raw.get("seen_close_ids") or []
        if isinstance(seen, list):
            self._seen_close_ids = {str(x) for x in seen[-500:]}

    def _save_state(self) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "consecutive_losses": self.consecutive_losses,
            "consecutive_bad": self.consecutive_bad,
            "seen_close_ids": list(self._seen_close_ids)[-500:],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _key(symbol: str, side: str) -> str:
        return f"{str(symbol).upper()}|{str(side).capitalize()}"

    @staticmethod
    def _norm_side(side: str) -> str:
        s = str(side or "").strip().upper()
        if s in ("BUY", "LONG"):
            return "Buy"
        if s in ("SELL", "SHORT"):
            return "Sell"
        return str(side or "").capitalize() or "Buy"

    def snapshot_opens(
        self,
        positions: List[Mapping[str, Any]],
        *,
        bot_symbols: Optional[Set[str]] = None,
        tracked: Optional[Mapping[str, Any]] = None,
    ) -> None:
        """Обновить карту всех открытых (ручные + бот)."""
        if not self.cfg.enabled:
            return
        bot_symbols = bot_symbols or set()
        tracked = tracked or {}
        now_ms = time.time() * 1000.0
        fresh: Dict[str, TrackedOpen] = {}
        for p in positions:
            sym = str(p.get("symbol", "")).upper()
            if not sym:
                continue
            size = float(p.get("size") or p.get("qty") or p.get("positionQty") or 0)
            if size <= 0:
                avg = float(p.get("avgPrice") or p.get("entryPrice") or 0)
                pval = float(p.get("positionValue") or 0)
                if pval > 0 and avg > 0:
                    size = pval / avg
            if size <= 0:
                continue
            side = self._norm_side(str(p.get("side", "")))
            entry = float(p.get("avgPrice") or p.get("entryPrice") or 0)
            key = self._key(sym, side)
            prev = self.open_map.get(key)
            tr = tracked.get(sym)
            origin = "bot" if sym in bot_symbols else "manual"
            if tr is not None:
                origin = str(getattr(tr, "origin", origin) or origin).lower()
                if origin not in ("bot", "manual"):
                    origin = "manual"
            opened_ms = now_ms
            if prev and prev.symbol == sym and prev.side == side:
                opened_ms = prev.opened_at_ms
            elif tr is not None and getattr(tr, "opened_at_utc", ""):
                try:
                    ts = datetime.fromisoformat(
                        str(tr.opened_at_utc).replace("Z", "+00:00")
                    )
                    opened_ms = ts.timestamp() * 1000.0
                except ValueError:
                    pass
            sl = float(p.get("stopLoss") or p.get("stop_loss") or 0)
            tp = float(p.get("takeProfit") or p.get("take_profit") or 0)
            if tr is not None:
                if sl <= 0:
                    sl = float(getattr(tr, "stop_loss", 0) or 0)
                if tp <= 0:
                    tp = float(getattr(tr, "take_profit", 0) or 0)
            fresh[key] = TrackedOpen(
                symbol=sym,
                side=side,
                entry=entry,
                qty=size,
                origin=origin,
                opened_at_ms=opened_ms,
                stop_loss=sl,
                take_profit=tp,
            )
        self.open_map = fresh

    def classify_close(
        self,
        *,
        pnl_usdt: float,
        entry: float,
        exit_price: float,
        side: str,
        age_minutes: Optional[float],
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> Tuple[bool, List[str]]:
        """Вернуть (is_bad, reasons)."""
        reasons: List[str] = []
        if pnl_usdt >= 0:
            return False, reasons
        # убыток
        if age_minutes is not None and age_minutes < self.cfg.fast_loss_minutes:
            reasons.append(
                f"быстрый убыток за {age_minutes:.1f} мин "
                f"(<{self.cfg.fast_loss_minutes:g} мин)"
            )
        pnl_pct = 0.0
        if entry > 0 and exit_price > 0:
            side_n = self._norm_side(side)
            if side_n == "Buy":
                pnl_pct = (exit_price - entry) / entry * 100.0
            else:
                pnl_pct = (entry - exit_price) / entry * 100.0
            if pnl_pct <= -abs(self.cfg.large_loss_pct):
                reasons.append(f"крупный убыток {pnl_pct:.2f}%")
        # закрылись далеко от заявленного SL (подозрение на срыв / перепись)
        if entry > 0 and exit_price > 0 and stop_loss > 0:
            side_n = self._norm_side(side)
            if side_n == "Buy" and exit_price < stop_loss * 0.997:
                reasons.append("выход заметно хуже выставленного SL")
            if side_n == "Sell" and exit_price > stop_loss * 1.003:
                reasons.append("выход заметно хуже выставленного SL")
        return (len(reasons) > 0), reasons

    def on_closed_trade(
        self,
        row: Mapping[str, Any],
        *,
        origin: str = "manual",
        order_id: str = "",
    ) -> Optional[str]:
        """
        Обработать новое закрытие с биржи.
        Возвращает текст алерта для Telegram или None.
        """
        if not self.cfg.enabled:
            return None
        oid = str(order_id or row.get("orderId") or row.get("id") or "")
        if oid and oid in self._seen_close_ids:
            return None
        if oid:
            self._seen_close_ids.add(oid)

        sym = str(row.get("symbol", "")).upper()
        side = self._norm_side(str(row.get("side", "")))
        pnl = float(row.get("closedPnl") or row.get("pnl") or 0)
        entry = float(row.get("avgEntryPrice") or row.get("entryPrice") or 0)
        exit_p = float(row.get("avgExitPrice") or row.get("exitPrice") or 0)
        key = self._key(sym, side)
        tracked = self.open_map.pop(key, None)
        if tracked and entry <= 0:
            entry = tracked.entry
        origin_u = str(origin or (tracked.origin if tracked else "manual")).lower()
        if origin_u not in ("bot", "manual"):
            origin_u = "manual"

        age_min: Optional[float] = None
        if tracked:
            age_min = max(0.0, (time.time() * 1000.0 - tracked.opened_at_ms) / 60000.0)
        else:
            # Bybit иногда отдаёт createdTime/updatedTime в ms
            try:
                c0 = float(row.get("createdTime") or 0)
                c1 = float(row.get("updatedTime") or 0)
                if c0 > 1e11 and c1 > c0:
                    age_min = (c1 - c0) / 60000.0
            except (TypeError, ValueError):
                age_min = None

        sl = float(tracked.stop_loss if tracked else 0) or 0.0
        tp = float(tracked.take_profit if tracked else 0) or 0.0
        is_bad, bad_reasons = self.classify_close(
            pnl_usdt=pnl,
            entry=entry,
            exit_price=exit_p,
            side=side,
            age_minutes=age_min,
            stop_loss=sl,
            take_profit=tp,
        )

        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        if is_bad:
            self.consecutive_bad += 1
        elif pnl >= 0:
            self.consecutive_bad = 0
        # прибыльное закрытие сбрасывает bad-streak; убыток без «bad» — streak bad не растёт

        self._save_state()

        loss_alert = self.consecutive_losses > self.cfg.alert_when_losses_gt
        bad_alert = self.consecutive_bad > self.cfg.alert_when_bad_closes_gt
        if not (loss_alert or bad_alert):
            if is_bad:
                logger.warning(
                    "CloseWatchdog bad close %s %s origin=%s pnl=%.4f reasons=%s "
                    "(streak losses=%d bad=%d — алерт ещё не)",
                    sym,
                    side,
                    origin_u,
                    pnl,
                    "; ".join(bad_reasons) or "-",
                    self.consecutive_losses,
                    self.consecutive_bad,
                )
            return None

        now = time.time()
        if now - self._last_alert_ts < self.cfg.alert_cooldown_sec:
            logger.warning(
                "CloseWatchdog alert suppressed (cooldown) %s pnl=%.4f losses=%d bad=%d",
                sym,
                pnl,
                self.consecutive_losses,
                self.consecutive_bad,
            )
            return None
        self._last_alert_ts = now

        age_txt = f"{age_min:.1f} мин" if age_min is not None else "?"
        lines = [
            "🚨 <b>АВАРИЯ ЗАКРЫТИЙ</b>",
            f"Сделка: <b>{sym}</b> {side} ({origin_u})",
            f"PnL: <b>{pnl:+.4f} USDT</b>",
            f"Вход/выход: {entry:.6g} → {exit_p:.6g}",
            f"Возраст позиции: {age_txt}",
            f"Убытков подряд: <b>{self.consecutive_losses}</b> "
            f"(порог &gt; {self.cfg.alert_when_losses_gt})",
            f"Некорректных подряд: <b>{self.consecutive_bad}</b> "
            f"(порог &gt; {self.cfg.alert_when_bad_closes_gt})",
        ]
        if bad_reasons:
            lines.append("Признаки: " + "; ".join(bad_reasons))
        lines.append("Отслеживаются <b>все</b> сделки — ручные и бота.")
        msg = "\n".join(lines)
        logger.error("CloseWatchdog ALERT: %s", msg.replace("\n", " | "))
        return msg if self.cfg.notify_telegram else None
