#!/usr/bin/env python3
"""
Мост «prop-спеки» ↔ ``RLMetaControllerFacade`` (действия 0..3 = NO / LOW / NORMAL / AGGRESSIVE).

В спецификации фигурируют trade/side/aggression; здесь side берётся из знака ``signal`` ( [-1,1] или итог voter ).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional

from engine.rl_meta_controller import RLMetaControllerFacade


@dataclass
class RLMetaControllerPro:
    facade: RLMetaControllerFacade

    def __init__(self, facade: Optional[RLMetaControllerFacade] = None) -> None:
        self.facade = facade or RLMetaControllerFacade()

    @staticmethod
    def build_state(features: Mapping[str, Any], signal: float) -> List[float]:
        """7-D state как у ``state_from_meta_ohlcv`` / ``RuleRLMetaController``."""
        conf = float(features.get("ai_confidence", features.get("confidence", 50.0)) or 50.0)
        conf_norm = conf if 0.0 <= conf <= 1.0 else min(1.0, max(0.0, conf / 100.0))
        vol = float(features.get("volatility", features.get("atr_pct", 0.02)) or 0.02)
        vol_norm = min(1.0, max(0.0, vol * 25.0))
        _ = signal  # при необходимости прокиньте в features
        return [
            min(1.0, max(0.0, float(features.get("win_rate_hint", 0.5) or 0.5))),
            float(features.get("last_pnl", 0.0) or 0.0),
            float(min(1.0, max(0.0, float(features.get("meta_drawdown", 0.0) or 0.0)))),
            vol_norm,
            float(min(1.0, max(0.0, float(features.get("trades_ratio", 0.5) or 0.5)))),
            conf_norm,
            0.0,
        ]

    def decide(self, signal: float, features: Mapping[str, Any]) -> Dict[str, Any]:
        state = self.build_state(features, signal)
        action = int(self.facade.act(state))
        risk_mult = float(RLMetaControllerFacade.get_risk_multiplier_for_action(action))
        trade = action != 0
        if signal > 1e-6:
            side = "LONG"
        elif signal < -1e-6:
            side = "SHORT"
        else:
            side = "FLAT"
        return {
            "trade": trade,
            "side": side,
            "aggression": action,
            "risk_multiplier": risk_mult,
            "raw_signal": float(signal),
        }
