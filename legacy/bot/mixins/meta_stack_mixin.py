"""Meta-Controller + RL meta + hybrid voter stack (+Gemma.txt integration)."""
from __future__ import annotations

import asyncio
import logging
import statistics
from copy import copy
from typing import Any, Dict, List, Optional

from bot.trading_bot_imports import *  # noqa: F401,F403

from engine.hybrid_voter import HybridVoter
from engine.meta_controller import MetaController
from engine.rl_meta_controller import (
    RLMetaControllerFacade,
    state_from_meta_ohlcv,
)

_log = logging.getLogger(__name__)


class TradingBotMetaStackMixin:
    """Состояние и вызовы meta / rl_meta / hybrid_voter (конфиг: meta_controller, rl_meta, hybrid_voter)."""

    def _init_meta_stack(self) -> None:
        _mc: Dict[str, Any] = (self.cfg.get("meta_controller", default={}) or {})  # type: ignore[union-attr]
        self.meta_controller_enabled = bool(_mc.get("enabled", False))
        self.meta: Optional[MetaController] = (
            MetaController.from_config(self.cfg) if self.meta_controller_enabled else None
        )
        _hv: Dict[str, Any] = (self.cfg.get("hybrid_voter", default={}) or {})  # type: ignore[union-attr]
        self.hybrid_voter_enabled = bool(_hv.get("enabled", False))
        self.hybrid_voter: Optional[HybridVoter] = (
            HybridVoter.from_config(self.cfg) if self.hybrid_voter_enabled else None
        )
        self.hybrid_voter_require_side_match: bool = bool(
            _hv.get("require_side_match", True)
        )
        _rm: Dict[str, Any] = (self.cfg.get("rl_meta", default={}) or {})  # type: ignore[union-attr]
        self.rl_meta_enabled: bool = bool(_rm.get("enabled", False))
        self.rl_meta: Optional[RLMetaControllerFacade] = (
            RLMetaControllerFacade.from_config(self.cfg) if self.rl_meta_enabled else None
        )
        self.rl_meta_block_scan_on_no_trade: bool = bool(
            _rm.get("block_scan_on_no_trade", True)
        )
        self.rl_meta_train_every_n_closes: int = int(
            _rm.get("train_every_n_closes", 0) or 0
        )
        self._rl_meta_close_train_counter: int = 0
        # Multipliers / gating (обновляются в цикле)
        self._meta_stack_risk_mult: float = 1.0
        self._meta_block_scan: bool = False
        self._rl_block_scan: bool = False
        self._last_rl_state: List[float] = [0.0] * 7
        self._last_rl_action: int = 2
        self._last_trade_pnl: float = 0.0
        self._meta_last_closes: List[float] = []
        self._rl_entry_state_by_symbol: Dict[str, List[float]] = {}
        self._rl_entry_action_by_symbol: Dict[str, int] = {}
        # Параметры бенчмарка
        self._meta_kline_interval: str = str(_mc.get("kline_interval", "60") or "60")
        self._meta_klines_lookback: int = max(30, int(_mc.get("klines_lookback", 120) or 120))
        _bench = (str(_mc.get("benchmark_symbol", "") or "")).strip()
        self._meta_benchmark_symbol_override: str = _bench

    def _meta_stack_any(self) -> bool:
        return bool(
            (self.meta_controller_enabled and self.meta is not None)
            or (self.rl_meta_enabled and self.rl_meta is not None)
            or (self.hybrid_voter_enabled and self.hybrid_voter is not None)
        )

    def _meta_resolved_benchmark_symbol(self) -> str:
        s = self._meta_benchmark_symbol_override
        if s:
            return s
        return str(
            getattr(self, "adaptive_regime_presets_benchmark_symbol", "BTCUSDT")
            or "BTCUSDT"
        )

    async def _meta_stack_cycle_update(
        self, cycle: int, stage_timeout: float
    ) -> None:
        if not self._meta_stack_any():
            self._meta_stack_risk_mult = 1.0
            self._meta_block_scan = False
            self._rl_block_scan = False
            return
        sym = self._meta_resolved_benchmark_symbol()
        klines: List[Dict] = []
        t_out = max(8.0, float(stage_timeout))
        try:
            klines = await asyncio.wait_for(
                self.client.get_klines(  # type: ignore[union-attr, misc]
                    sym, self._meta_kline_interval, self._meta_klines_lookback
                ),
                timeout=t_out,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "[meta_stack] benchmark klines %s: %s — using last closes if any", sym, exc
            )
        closes = [float(k.get("close", 0) or 0) for k in (klines or []) if k]
        self._meta_last_closes = [c for c in closes if c and c > 0]
        vol = 0.0
        cl = self._meta_last_closes
        if len(cl) > 2:
            rets = [
                (cl[i] - cl[i - 1]) / (cl[i - 1] + 1e-9) for i in range(1, len(cl))
            ]
            if rets and len(rets) > 1:
                try:
                    vol = float(statistics.pstdev(rets))
                except Exception:  # noqa: BLE001
                    vol = 0.0
        if self.hybrid_voter is not None:
            self.hybrid_voter.set_benchmark_volatility(vol)

        regime = "RANGE"
        m_meta: float = 1.0
        if self.meta is not None:
            regime = self.meta.detect_regime(self._meta_last_closes) if self._meta_last_closes else "RANGE"
            self.meta.decide_mode()
            m_meta = self.meta.get_risk_multiplier()
            self._meta_block_scan = not self.meta.allow_trade()
        else:
            self._meta_block_scan = False

        win_r = 0.5
        if self.meta is not None and self.meta.winrate_window:
            win_r = float(list(self.meta.winrate_window)[-1])

        dd = float(self.meta.drawdown_abs) if self.meta is not None else 0.0
        st = state_from_meta_ohlcv(
            meta_drawdown=dd,
            last_pnl=float(self._last_trade_pnl),
            last_signal_conf=50.0,
            vol_closes=self._meta_last_closes,
            regime=regime,
            win_rate_hint=win_r,
        )
        self._last_rl_state = st
        m_rl = 1.0
        act = 2
        if self.rl_meta is not None:
            act = int(self.rl_meta.act(st))
            self._last_rl_action = act
            m_rl = float(RLMetaControllerFacade.get_risk_multiplier_for_action(act))
        else:
            self._last_rl_action = 2
        self._meta_stack_risk_mult = max(0.0, float(m_meta) * float(m_rl))
        self._rl_block_scan = bool(
            self.rl_meta is not None
            and self.rl_meta_block_scan_on_no_trade
            and int(self._last_rl_action) == 0
        )
        if self.meta is None:
            self._rl_block_scan = self._rl_block_scan  # no change
        _log.info(
            "[meta_stack] c=%s sym=%s regime=%s mode=%s meta_mult=%.2f rl_a=%s rl_mult=%.2f total=%.2f "
            "block_meta=%s block_rl=%s",
            cycle,
            sym,
            (self.meta.market_regime if self.meta else "?"),
            (self.meta.mode if self.meta else "?"),
            m_meta,
            self._last_rl_action,
            m_rl,
            self._meta_stack_risk_mult,
            self._meta_block_scan,
            self._rl_block_scan,
        )

    def _meta_stack_allows_entry_scan(self) -> bool:
        if self.meta_controller_enabled and self.meta is not None and self._meta_block_scan:
            return False
        if self.rl_meta_enabled and self._rl_block_scan:
            return False
        if self._meta_stack_risk_mult <= 0.0:
            return False
        return True

    def _register_rl_entry_state(self, symbol: str) -> None:
        if not self.rl_meta_enabled or self.rl_meta is None:
            return
        self._rl_entry_state_by_symbol[str(symbol)] = list(self._last_rl_state)
        self._rl_entry_action_by_symbol[str(symbol)] = int(self._last_rl_action)

    def _meta_stack_update_pnl(self, pnl: float) -> None:
        """Мета по каждой фиксации PnL (часть/полный)."""
        self._last_trade_pnl = float(pnl)
        if self.meta is not None:
            self.meta.update_performance(float(pnl))
            self.meta.decide_mode()

    def _meta_stack_remember_rl(
        self, symbol: str, pnl: float, balance: float
    ) -> None:
        if self.rl_meta is None:
            return
        r = max(-1.0, min(1.0, float(pnl) / max(1.0, abs(float(balance) or 1.0))))
        wr = 0.5
        if self.meta is not None and self.meta.winrate_window:
            wr = float(list(self.meta.winrate_window)[-1])
        s_next = state_from_meta_ohlcv(
            float(self.meta.drawdown_abs) if self.meta is not None else 0.0,
            float(pnl),
            50.0,
            self._meta_last_closes,
            str(self.meta.market_regime if self.meta else "RANGE"),
            win_rate_hint=wr,
        )
        s0 = self._rl_entry_state_by_symbol.pop(
            str(symbol), copy(self._last_rl_state)
        )
        a0 = int(
            self._rl_entry_action_by_symbol.pop(
                str(symbol), int(self._last_rl_action)
            )
        )
        self.rl_meta.remember(s0, a0, r, s_next)
        self._rl_meta_close_train_counter += 1
        n = int(self.rl_meta_train_every_n_closes)
        if n > 0 and self._rl_meta_close_train_counter % n == 0:
            self.rl_meta.train()

    def _hybrid_voter_check_signal(
        self,
        _symbol: str,
        signal: EntrySignal,
        _market: Any,
        _orderflow: Any,
    ) -> Optional[str]:
        if not self.hybrid_voter_enabled or self.hybrid_voter is None:
            return None
        h4 = int(signal.metadata.get("htf_4h_trend", 0) or 0)
        side_u = str(signal.side or "").upper()
        in_long = side_u in ("BUY", "LONG")
        if in_long:
            ta_sig = 0.8 if h4 > 0 else (-0.55 if h4 < 0 else 0.0)
        else:
            ta_sig = -0.8 if h4 < 0 else (0.55 if h4 > 0 else 0.0)
        conf = float(signal.confidence or 0.0)
        xgb_sig = 2.0 * conf - 1.0
        imb = float(signal.metadata.get("normalized_imbalance", 0.0) or 0.0)
        gemma_sig = max(-1.0, min(1.0, imb * 2.0))
        payload = {
            "xgb": xgb_sig,
            "ta": ta_sig,
            "gemma": gemma_sig,
        }
        v = self.hybrid_voter.vote(payload)
        th = self.hybrid_voter.effective_threshold()
        if abs(v) < th:
            return f"hybrid_voter_no_consensus (v={v:.2f} th={th:.2f})"
        if self.hybrid_voter_require_side_match:
            if in_long and v < th:
                return f"hybrid_voter_bearish_for_long (v={v:.2f} need>={th:.2f})"
            if (not in_long) and v > -th:
                return f"hybrid_voter_bullish_for_short (v={v:.2f} need<={-th:.2f})"
        signal.metadata["hybrid_voter_score"] = round(v, 4)
        signal.metadata["hybrid_voter_threshold"] = round(th, 4)
        return None
