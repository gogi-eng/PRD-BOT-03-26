"""
Мост unified-бота к engine/entry_engine: зоны FVG/OB, BOS, вход на ретесте зоны.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from analysis.liquidation_clusters import LiquidationAnalysis, LiquidationClusterDetector
from analysis.market_analyzer import MarketAnalyzer
from analysis.market_regime_ai import MarketRegimeAI, RegimePrediction
from analysis.market_structure import MarketStructureEngine
from analysis.orderflow_analyzer import OrderflowSnapshot
from analysis.structure_zones import StructureZone, StructureZoneAnalyzer, ZoneContext
from analysis.transformer_model import TransformerPrediction
from core.config import BotConfig
from engine.entry_engine import EntryEngine, EntrySignal
from prd_agent.signals.pump_dump_mode import is_agent_world_signal, is_pump_dump_signal
from prd_agent.signals.types import UnifiedSignal

logger = logging.getLogger("prd_agent.entry.bridge")


@dataclass
class ZoneEntryPlan:
    entry: float
    stop_loss: float
    take_profit: float
    ok: bool = True
    block_reason: str = ""
    entry_mode: str = "market"
    metadata: Dict[str, Any] = field(default_factory=dict)


def _zone_entry_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ze = cfg.get("zone_entry", {})
    return ze if isinstance(ze, dict) else {}


def _entry_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    ec = cfg.get("entry", {})
    return ec if isinstance(ec, dict) else {}


def _is_buy(side: str) -> bool:
    return str(side or "").strip().upper() in ("BUY", "LONG")


def _sources_for_zone_entry(cfg: Dict[str, Any]) -> Set[str]:
    ze = _zone_entry_cfg(cfg)
    raw = ze.get("sources")
    if isinstance(raw, list) and raw:
        return {str(x).lower() for x in raw}
    return {"own_multi_agent", "ta_volatility", "hybrid"}


def should_apply_zone_entry(sig: UnifiedSignal, cfg: Dict[str, Any]) -> bool:
    ze = _zone_entry_cfg(cfg)
    if not bool(ze.get("enabled", True)):
        return False
    if is_pump_dump_signal(sig) or is_agent_world_signal(sig):
        return False
    src = str(sig.source or "").lower()
    skip_with_levels = ze.get("skip_sources_with_levels") or ["telegram", "tg", "mirror_pump_dump"]
    if isinstance(skip_with_levels, list):
        for tag in skip_with_levels:
            if str(tag).lower() in src and float(sig.entry or 0) > 0:
                return False
    return src in _sources_for_zone_entry(cfg)


def atr_from_klines(klines: List[Dict], period: int = 14) -> float:
    if len(klines) < period + 2:
        return 0.0
    highs = [float(k.get("high", 0) or 0) for k in klines]
    lows = [float(k.get("low", 0) or 0) for k in klines]
    closes = [float(k.get("close", 0) or 0) for k in klines]
    trs: List[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    chunk = trs[-period:]
    return sum(chunk) / max(len(chunk), 1)


def _htf_trend_int(htf_klines: Optional[List[Dict]]) -> int:
    if not htf_klines or len(htf_klines) < 60:
        return 0
    ma = MarketAnalyzer()
    m = ma.analyze(htf_klines)
    return int(m.htf_trend.value)


def _biased_orderflow(side: str) -> OrderflowSnapshot:
    is_long = _is_buy(side)
    imb = 0.35 if is_long else -0.35
    return OrderflowSnapshot(
        normalized_imbalance=imb,
        buy_volume=1000.0 if is_long else 400.0,
        sell_volume=400.0 if is_long else 1000.0,
        spread_pct=0.02,
        bullish_ratio=1.2 if is_long else 0.9,
        bearish_ratio=0.9 if is_long else 1.2,
    )


def _biased_transformer(side: str) -> TransformerPrediction:
    if _is_buy(side):
        return TransformerPrediction(prob_up=0.62, prob_down=0.18, prob_flat=0.20, confidence=0.55)
    return TransformerPrediction(prob_up=0.18, prob_down=0.62, prob_flat=0.20, confidence=0.55)


def compute_zone_entry_price(
    *,
    side: str,
    market_price: float,
    zone_context: ZoneContext,
    structure,
    atr_value: float,
    cfg: Dict[str, Any],
) -> Tuple[float, str]:
    """Цена входа на границе/середине зоны или на уровне BOS после ретеста."""
    ec = _entry_cfg(cfg)
    ze = _zone_entry_cfg(cfg)
    is_long = _is_buy(side)
    proximity = float(ec.get("zone_proximity_pct", ze.get("zone_proximity_pct", 0.4)))
    prefer_bos = bool(ze.get("prefer_bos_retest", True))
    at_zone = str(ze.get("entry_at_zone", "edge")).lower()

    bos = getattr(structure, "last_bos", None) if structure is not None else None
    if prefer_bos and bos is not None:
        if is_long and getattr(bos, "direction", "") == "up":
            level = float(getattr(bos, "broken_level", 0) or 0)
            if level > 0:
                pad = max(atr_value * 0.05, market_price * 0.0001)
                return min(market_price, level + pad), "bos_retest_long"
        if not is_long and getattr(bos, "direction", "") == "down":
            level = float(getattr(bos, "broken_level", 0) or 0)
            if level > 0:
                pad = max(atr_value * 0.05, market_price * 0.0001)
                return max(market_price, level - pad), "bos_retest_short"

    active: Optional[StructureZone] = None
    if is_long:
        active = zone_context.price_in_bullish_zone(market_price) or zone_context.price_near_bullish_zone(
            market_price, proximity
        )
        if active is None:
            active = zone_context.best_long_entry_zone()
    else:
        active = zone_context.price_in_bearish_zone(market_price) or zone_context.price_near_bearish_zone(
            market_price, proximity
        )
        if active is None:
            active = zone_context.best_short_entry_zone()

    if active is None:
        return market_price, "market_no_zone"

    if at_zone == "mid":
        return float(active.mid), f"zone_mid_{active.kind}"

    if is_long:
        if market_price > float(active.high):
            return float(active.high), f"zone_edge_high_{active.kind}"
        return float(active.mid), f"zone_mid_{active.kind}"
    if market_price < float(active.low):
        return float(active.low), f"zone_edge_low_{active.kind}"
    return float(active.mid), f"zone_mid_{active.kind}"


def _fallback_sl_tp(
    *,
    side: str,
    entry: float,
    zone_context: ZoneContext,
    structure,
    atr_value: float,
    cfg: Dict[str, Any],
) -> Tuple[float, float]:
    ec = _entry_cfg(cfg)
    sl_buf = float(ec.get("sl_buffer_atr_mult", 0.5))
    min_rr = float(ec.get("min_rr_ratio", 2.0))
    is_long = _is_buy(side)

    if is_long:
        if structure is not None and float(getattr(structure, "sweep_low", 0) or 0) > 0:
            sl = float(structure.sweep_low) - atr_value * sl_buf
        else:
            sl = zone_context.structural_sl_long(entry, atr_value)
        _, tp2 = zone_context.structural_tp_long(entry, atr_value)
        risk = abs(entry - sl)
        tp = max(tp2, entry + risk * min_rr) if risk > 0 else tp2
    else:
        if structure is not None and float(getattr(structure, "sweep_high", 0) or 0) > 0:
            sl = float(structure.sweep_high) + atr_value * sl_buf
        else:
            sl = zone_context.structural_sl_short(entry, atr_value)
        _, tp2 = zone_context.structural_tp_short(entry, atr_value)
        risk = abs(entry - sl)
        tp = min(tp2, entry - risk * min_rr) if risk > 0 else tp2
    return sl, tp


class EntryEngineBridge:
    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg
        self._engine = EntryEngine(BotConfig(cfg))
        self._zone_analyzer = StructureZoneAnalyzer()
        self._structure_engine = MarketStructureEngine()
        self._market_analyzer = MarketAnalyzer()
        self._regime_ai = MarketRegimeAI()
        self._liq_detector = LiquidationClusterDetector()

    def plan_levels(
        self,
        sig: UnifiedSignal,
        *,
        klines: List[Dict],
        htf_klines: Optional[List[Dict]],
        market_price: float,
    ) -> ZoneEntryPlan:
        if not should_apply_zone_entry(sig, self.cfg):
            return ZoneEntryPlan(
                entry=market_price,
                stop_loss=float(sig.stop_loss or 0),
                take_profit=float(sig.take_profit or 0),
                ok=True,
                entry_mode="market",
                metadata={"zone_entry": "skipped_source"},
            )

        if len(klines) < 30:
            return ZoneEntryPlan(
                entry=market_price,
                stop_loss=float(sig.stop_loss or 0),
                take_profit=float(sig.take_profit or 0),
                ok=True,
                block_reason="",
                entry_mode="market",
                metadata={"zone_entry": "insufficient_klines"},
            )

        price = float(market_price or 0) or float(klines[-1].get("close", 0) or 0)
        if price <= 0:
            return ZoneEntryPlan(0, 0, 0, ok=False, block_reason="zone_entry: нет цены")

        atr_v = atr_from_klines(klines)
        if atr_v <= 0:
            atr_v = price * 0.008

        zone_ctx = self._zone_analyzer.analyze(klines, price)
        structure = self._structure_engine.analyze(klines, atr_v)
        market = self._market_analyzer.analyze(klines, htf_klines)
        if not market.can_trade:
            market.can_trade = True

        regime: RegimePrediction = self._regime_ai.classify(market)
        orderflow = _biased_orderflow(sig.side)
        transformer = _biased_transformer(sig.side)
        liq = self._liq_detector.analyze(price, [])

        entry_sig: EntrySignal = self._engine.generate_signal(
            symbol=sig.symbol,
            klines=klines,
            current_price=price,
            market_analysis=market,
            regime_prediction=regime,
            transformer_prediction=transformer,
            orderflow_snapshot=orderflow,
            liq_analysis=liq,
            atr_value=atr_v,
            zone_context=zone_ctx,
            structure=structure,
            funding_rate=0.0,
            htf_4h_trend=_htf_trend_int(htf_klines),
            forced_side=sig.side,
        )

        ze = _zone_entry_cfg(self.cfg)
        require_engine = bool(ze.get("require_entry_engine_pass", False))
        block_no_zone = bool(ze.get("block_if_no_zone", False))

        has_zone = bool(
            zone_ctx.all_bullish_zones
            or zone_ctx.all_bearish_zones
            or zone_ctx.support_levels
            or zone_ctx.resistance_levels
        )
        has_bos = structure.last_bos is not None

        zone_entry, entry_mode = compute_zone_entry_price(
            side=sig.side,
            market_price=price,
            zone_context=zone_ctx,
            structure=structure,
            atr_value=atr_v,
            cfg=self.cfg,
        )

        md = dict(entry_sig.metadata) if isinstance(entry_sig.metadata, dict) else {}
        md["zone_entry_mode"] = entry_mode
        md["has_bos"] = has_bos
        md["entry_zone"] = md.get("entry_zone", "no_zone")

        if entry_sig.should_enter:
            sl = float(entry_sig.stop_loss or 0)
            tp = float(entry_sig.take_profit or 0)
            if sl <= 0 or tp <= 0:
                sl, tp = _fallback_sl_tp(
                    side=sig.side,
                    entry=zone_entry,
                    zone_context=zone_ctx,
                    structure=structure,
                    atr_value=atr_v,
                    cfg=self.cfg,
                )
            logger.info(
                "Zone entry %s %s: mode=%s entry=%.6g (mkt=%.6g) SL=%.6g TP=%.6g grade=%s",
                sig.symbol,
                sig.side,
                entry_mode,
                zone_entry,
                price,
                sl,
                tp,
                entry_sig.grade,
            )
            return ZoneEntryPlan(
                entry=zone_entry,
                stop_loss=sl,
                take_profit=tp,
                ok=True,
                entry_mode=entry_mode,
                metadata=md,
            )

        reject = str(md.get("reject_reason", "") or "entry_engine_reject")
        if require_engine:
            return ZoneEntryPlan(0, 0, 0, ok=False, block_reason=f"zone_entry: {reject}", metadata=md)

        if block_no_zone and not has_zone and not has_bos:
            return ZoneEntryPlan(
                0, 0, 0, ok=False, block_reason="zone_entry: нет зоны/BOS для входа", metadata=md
            )

        sl, tp = _fallback_sl_tp(
            side=sig.side,
            entry=zone_entry,
            zone_context=zone_ctx,
            structure=structure,
            atr_value=atr_v,
            cfg=self.cfg,
        )
        if entry_mode == "market_no_zone" and block_no_zone:
            return ZoneEntryPlan(
                0, 0, 0, ok=False, block_reason="zone_entry: нет зоны для точного входа", metadata=md
            )

        logger.info(
            "Zone entry fallback %s %s: mode=%s entry=%.6g (%s)",
            sig.symbol,
            sig.side,
            entry_mode,
            zone_entry,
            reject[:80],
        )
        return ZoneEntryPlan(
            entry=zone_entry,
            stop_loss=sl,
            take_profit=tp,
            ok=True,
            entry_mode=entry_mode,
            metadata={**md, "zone_entry_fallback": reject},
        )

    def enrich_signal(
        self,
        sig: UnifiedSignal,
        *,
        klines: List[Dict],
        htf_klines: Optional[List[Dict]] = None,
    ) -> UnifiedSignal:
        """Обновляет entry/SL/TP в сигнале до прохождения quality gate."""
        if not should_apply_zone_entry(sig, self.cfg):
            return sig
        price = float(sig.entry or 0)
        if price <= 0 and klines:
            price = float(klines[-1].get("close", 0) or 0)
        plan = self.plan_levels(sig, klines=klines, htf_klines=htf_klines, market_price=price)
        if not plan.ok:
            sig.reason = f"{sig.reason} | {plan.block_reason}"[:400]
            return sig
        sig.entry = plan.entry
        sig.stop_loss = plan.stop_loss
        sig.take_profit = plan.take_profit
        mode = plan.entry_mode
        sig.reason = f"{sig.reason} | zone_entry:{mode}"[:400]
        if isinstance(sig.raw, dict):
            sig.raw = {**sig.raw, "zone_entry": plan.metadata}
        return sig
