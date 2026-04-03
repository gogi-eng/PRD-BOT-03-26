#!/usr/bin/env python3
"""
ENTRY ENGINE v6 — WEIGHTED SCORING MODEL

Instead of hard reject gates, uses weighted voting like quant funds:

  Trend score     × 0.30
  Orderflow score × 0.30
  AI/Transformer  × 0.40
  ─────────────────────
  Total score → must be > threshold (0.70)

Hard requirements (execution safety):
  - spread / funding checks
  - RR >= min_rr_ratio
  - Must have a valid zone (FVG/OB)
"""
from __future__ import annotations

import logging
import math
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import torch
    import torch.nn as nn
except Exception:  # pragma: no cover - optional dependency at runtime
    torch = None
    nn = None


logger = logging.getLogger("ENTRY")


@dataclass
class EntrySignal:
    should_enter: bool = False
    side: str = ""
    confidence: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    rr_ratio: float = 0.0
    reasons: list = field(default_factory=list)
    filters_passed: dict = field(default_factory=dict)
    capital_score: float = 0.0
    grade: str = "C"  # A/B/C signal grade
    metadata: dict = field(default_factory=dict)


def classify_signal_grade(
    confidence: float,
    rr_ratio: float,
    has_sweep: bool,
    has_bos: bool,
    htf_aligned: bool,
    entry_zone: str,
) -> str:
    """Classify signal into A/B/C grade.

    A — High conviction: conf >= 0.85, RR >= 4.0, sweep+BOS, HTF aligned, has zone
    B — Standard: conf >= 0.75, RR >= 3.0, at least 2 confirmations
    C — Marginal: everything else that passed entry threshold
    """
    confirmations = sum([has_sweep, has_bos, htf_aligned, entry_zone != "no_zone"])

    if confidence >= 0.85 and rr_ratio >= 4.0 and confirmations >= 3:
        return "A"
    if confidence >= 0.75 and rr_ratio >= 3.0 and confirmations >= 2:
        return "B"
    return "C"


if nn is not None and torch is not None:
    class _TinyTransformerClassifier(nn.Module):
        """Inference-only tiny transformer matching train_transformer.py architecture."""

        FEATURE_DIM = 7

        def __init__(self, d_model: int = 16, nhead: int = 2, num_layers: int = 1, dropout: float = 0.2):
            super().__init__()
            self.scalar_proj = nn.Linear(1, d_model)
            self.pos_embedding = nn.Parameter(torch.zeros(1, self.FEATURE_DIM, d_model))
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=d_model * 2,
                dropout=dropout,
                activation="gelu",
                batch_first=True,
            )
            self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.head = nn.Sequential(
                nn.LayerNorm(d_model),
                nn.Linear(d_model, d_model // 2),
                nn.GELU(),
                nn.Dropout(dropout),
                nn.Linear(d_model // 2, 1),
            )

        def forward(self, x):
            tokens = x.unsqueeze(-1)
            encoded = self.encoder(self.scalar_proj(tokens) + self.pos_embedding)
            pooled = encoded.mean(dim=1)
            return self.head(pooled).squeeze(-1)
else:  # pragma: no cover - torch missing
    class _TinyTransformerClassifier:  # type: ignore[override]
        FEATURE_DIM = 7

        def __init__(self, *args, **kwargs):
            raise RuntimeError("Torch is not available")


class EntryEngine:
    """Weighted scoring entry engine. Trend(0.3) + Orderflow(0.3) + AI(0.4) >= threshold."""

    # Scoring weights — AI weight reduced until model is trained
    W_TREND = 0.40
    W_ORDERFLOW = 0.35
    W_TRANSFORMER = 0.25
    ENTRY_THRESHOLD = 0.55

    def __init__(self, cfg):
        self.min_rr_ratio = cfg.get("entry", "min_rr_ratio", default=2.0)
        self.min_target_profit_pct = cfg.get("entry", "min_target_profit_pct", default=1.2)
        self.min_stop_distance_pct = cfg.get("entry", "min_stop_distance_pct", default=0.5)
        self.min_stop_atr_mult = cfg.get("entry", "min_stop_atr_mult", default=0.9)
        self.require_structural_tp = cfg.get("entry", "require_structural_tp", default=False)
        self.sl_buffer_atr_mult = cfg.get("entry", "sl_buffer_atr_mult", default=0.5)
        self.max_entry_extension_atr = cfg.get("entry", "max_entry_extension_atr", default=0.75)
        self.entry_range_atr_mult = cfg.get("entry", "entry_range_atr_mult", default=0.22)
        self.zone_proximity_pct = cfg.get("entry", "zone_proximity_pct", default=0.4)
        self.max_spread_pct = cfg.get("entry", "max_spread_pct", default=0.08)
        self.max_funding_rate = cfg.get("entry", "max_funding_rate", default=0.05)
        self.entry_threshold = cfg.get("entry", "entry_threshold", default=self.ENTRY_THRESHOLD)
        self.require_sweep = cfg.get("entry", "require_sweep", default=False)
        self.require_4h_trend = cfg.get("entry", "require_4h_trend", default=False)
        self.min_volatility_pct = float(cfg.get("entry", "min_volatility_pct", default=0.0))
        # Config may store legacy ratio threshold (e.g. 1.20) while runtime uses
        # normalized imbalance in [-1, +1]. Convert ratio → normalized:
        # n = (r - 1) / (r + 1). Example: 1.20 -> ~0.091.
        of_cfg = float(cfg.get("entry", "min_orderflow_imbalance", default=0.0))
        if of_cfg > 1.0:
            self.min_orderflow_imbalance = (of_cfg - 1.0) / (of_cfg + 1.0)
        else:
            self.min_orderflow_imbalance = max(of_cfg, 0.0)
        self.min_smc_score = float(cfg.get("entry", "min_smc_score", default=0.0))
        self.trained_model_enabled = cfg.get("entry", "trained_model_enabled", default=False)
        self.trained_model_min_prob = cfg.get("entry", "trained_model_min_prob", default=0.55)
        self.trained_model_blend = cfg.get("entry", "trained_model_blend", default=0.35)
        self.trained_model_weights_path = cfg.get("entry", "trained_model_weights_path", default="transformer_weights.pt")
        self._trained_model = None

        # PRO filters from user config
        self.ema_trend_filter = cfg.get("entry", "ema_trend_filter", default=True)
        self.ema_fast_period = cfg.get("entry", "ema_fast_period", default=20)
        self.ema_slow_period = cfg.get("entry", "ema_slow_period", default=50)
        self.ema_guard_min_diff_pct = float(
            cfg.get("entry", "ema_guard_min_diff_pct", default=0.15)
        )
        self.momentum_filter = cfg.get("entry", "momentum_filter", default=True)
        self.momentum_lookback = cfg.get("entry", "momentum_lookback", default=5)
        self.momentum_guard_min_pct = float(
            cfg.get("entry", "momentum_guard_min_pct", default=0.10)
        )
        self.volume_filter = cfg.get("entry", "volume_filter", default=True)
        self.volume_lookback = cfg.get("entry", "volume_lookback", default=20)
        self.volume_guard_min_ratio = float(
            cfg.get("entry", "volume_guard_min_ratio", default=0.50)
        )
        # Allow high-conviction setups to bypass micro guard conflicts.
        self.guard_confidence_bypass = float(
            cfg.get("entry", "guard_confidence_bypass", default=0.82)
        )

        if self.trained_model_enabled:
            self._load_trained_model()

    # =====================================================
    # PRO HELPERS: EMA / ATR calculation
    # =====================================================
    @staticmethod
    def _compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
        """Compute EMA for a price array."""
        if len(prices) < period:
            return prices.copy()
        ema = np.empty_like(prices, dtype=float)
        ema[:period] = np.nan
        ema[period - 1] = np.mean(prices[:period])
        k = 2.0 / (period + 1)
        for i in range(period, len(prices)):
            ema[i] = prices[i] * k + ema[i - 1] * (1 - k)
        return ema

    @staticmethod
    def _extract_closes_and_volumes(klines: List[Dict]):
        """Extract close prices and volumes from klines list."""
        closes = np.array([float(k.get("close", 0)) for k in klines], dtype=float)
        volumes = np.array([float(k.get("volume", 0)) for k in klines], dtype=float)
        return closes, volumes

    def _resolve_weights_path(self) -> Path:
        weights_path = Path(self.trained_model_weights_path)
        if weights_path.is_absolute():
            return weights_path
        return Path(__file__).resolve().parents[1] / weights_path

    def _load_trained_model(self):
        if torch is None or nn is None:
            logger.warning("Trained model disabled: torch is not available")
            return

        weights_path = self._resolve_weights_path()
        if not weights_path.exists():
            logger.info(f"Trained model checkpoint not found: {weights_path}")
            return

        try:
            checkpoint = torch.load(str(weights_path), map_location="cpu")
            model = _TinyTransformerClassifier(
                d_model=int(checkpoint.get("d_model", 16)),
                nhead=int(checkpoint.get("nhead", 2)),
                num_layers=int(checkpoint.get("num_layers", 1)),
            )
            state_dict = checkpoint.get("model_state_dict", checkpoint)
            model.load_state_dict(state_dict, strict=False)
            model.eval()
            self._trained_model = model
            logger.info(
                f"Loaded trained model: {weights_path.name} "
                f"(val_precision={float(checkpoint.get('val_precision', 0.0)):.3f})"
            )
        except Exception as exc:
            logger.warning(f"Failed to load trained model from {weights_path}: {exc}")
            self._trained_model = None

    @staticmethod
    def _normalize_rr(rr_ratio: float) -> float:
        return min(max(rr_ratio / 15.0, 0.0), 1.0)

    @staticmethod
    def _normalize_htf_trend(htf_4h_trend: int) -> float:
        return min(max((float(htf_4h_trend) + 1.0) / 2.0, 0.0), 1.0)

    @staticmethod
    def _clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _predict_trained_win_prob(
        self,
        composite_score: float,
        trend_score: float,
        orderflow_score: float,
        ai_score: float,
        normalized_imbalance: float,
        rr_ratio: float,
        htf_4h_trend: int,
    ) -> float | None:
        if self._trained_model is None or torch is None:
            return None

        features = [
            self._clamp(composite_score, 0.0, 1.0),
            self._clamp(trend_score, 0.0, 1.0),
            self._clamp(orderflow_score, 0.0, 1.0),
            self._clamp(ai_score, 0.0, 1.0),
            self._clamp(normalized_imbalance, -1.0, 1.0),
            self._normalize_rr(rr_ratio),
            self._normalize_htf_trend(htf_4h_trend),
        ]
        x = torch.tensor([features], dtype=torch.float32)
        with torch.no_grad():
            prob = torch.sigmoid(self._trained_model(x)).item()
        return self._clamp(float(prob), 0.0, 1.0)

    def generate_signal(
        self, symbol: str, klines: List[Dict], current_price: float,
        market_analysis, regime_prediction, transformer_prediction,
        orderflow_snapshot, liq_analysis, atr_value: float = 0.0,
        zone_context=None, structure=None, funding_rate: float = 0.0,
        htf_4h_trend: int = 0,
    ) -> EntrySignal:
        signal = EntrySignal(entry_price=current_price)

        if not market_analysis.can_trade:
            signal.metadata["reject_reason"] = "market_blocked"
            return signal

        if self.require_4h_trend and int(htf_4h_trend) == 0:
            signal.metadata["reject_reason"] = "require_4h_trend_neutral"
            return signal

        if self.min_volatility_pct > 0 and float(getattr(market_analysis, "atr_pct", 0.0) or 0.0) < self.min_volatility_pct:
            signal.metadata["reject_reason"] = (
                f"volatility_too_low ({float(getattr(market_analysis, 'atr_pct', 0.0) or 0.0):.3f}% < {self.min_volatility_pct:.3f}%)"
            )
            return signal

        if atr_value <= 0:
            atr_value = current_price * 0.008

        # =====================================================
        # HARD PRE-CHECK: spread & funding (execution safety)
        # =====================================================
        spread_pct = orderflow_snapshot.spread_pct if hasattr(orderflow_snapshot, 'spread_pct') else 0.0
        if self.max_spread_pct > 0 and spread_pct > self.max_spread_pct:
            signal.metadata["reject_reason"] = f"spread_too_wide ({spread_pct:.3f}%)"
            return signal
        if self.max_funding_rate > 0 and abs(funding_rate) > self.max_funding_rate:
            signal.metadata["reject_reason"] = f"funding_rate_high ({funding_rate:.4f})"
            return signal

        # =====================================================
        # SCORE 1: TREND (0.35)
        # 4H trend + structure trend + sweep alignment
        # =====================================================
        trend_score = 0.0
        trend_reasons = []
        has_structure = structure is not None
        sweep = structure.last_sweep if has_structure else None

        # 4H trend direction
        if htf_4h_trend > 0:
            trend_score += 0.5
            trend_reasons.append("4H_BULL")
        elif htf_4h_trend < 0:
            trend_score += 0.5
            trend_reasons.append("4H_BEAR")
        # else: neutral = 0

        # Sweep alignment with trend
        if sweep is not None:
            sweep_side = "BUY" if sweep.direction == "down" else "SELL"
            trend_side = "BUY" if htf_4h_trend > 0 else "SELL" if htf_4h_trend < 0 else ""
            if sweep_side == trend_side:
                trend_score += 0.35
                trend_reasons.append(f"sweep_{sweep.direction}_aligned")
            else:
                trend_score += 0.1  # sweep exists but misaligned
                trend_reasons.append(f"sweep_{sweep.direction}_misaligned")
        # Structure trend bonus
        if has_structure and structure.trend.value != "range":
            if (structure.trend.value == "up" and htf_4h_trend > 0) or \
               (structure.trend.value == "down" and htf_4h_trend < 0):
                trend_score += 0.15
                trend_reasons.append("struct_aligned")

        trend_score = min(1.0, trend_score)

        # =====================================================
        # SCORE 2: ORDERFLOW (0.40)
        # Normalized imbalance [-1, +1]
        # =====================================================
        orderflow_score = 0.0
        of_reasons = []
        norm_imb = getattr(orderflow_snapshot, 'normalized_imbalance', 0.0)

        # Determine intended direction from strongest signal
        if htf_4h_trend > 0 or (htf_4h_trend == 0 and norm_imb > 0):
            # Looking for bullish orderflow
            if norm_imb > 0.25:
                orderflow_score = 1.0
                of_reasons.append(f"OF_strong_bull({norm_imb:+.2f})")
            elif norm_imb > 0.05:
                orderflow_score = 0.75
                of_reasons.append(f"OF_bull({norm_imb:+.2f})")
            elif norm_imb > -0.05:
                orderflow_score = 0.50
                of_reasons.append(f"OF_neutral({norm_imb:+.2f})")
            elif norm_imb > -0.25:
                orderflow_score = 0.25
                of_reasons.append(f"OF_weak_bear({norm_imb:+.2f})")
            else:
                orderflow_score = 0.1
                of_reasons.append(f"OF_bearish({norm_imb:+.2f})")
        else:
            # Looking for bearish orderflow
            if norm_imb < -0.25:
                orderflow_score = 1.0
                of_reasons.append(f"OF_strong_bear({norm_imb:+.2f})")
            elif norm_imb < -0.05:
                orderflow_score = 0.75
                of_reasons.append(f"OF_bear({norm_imb:+.2f})")
            elif norm_imb < 0.05:
                orderflow_score = 0.50
                of_reasons.append(f"OF_neutral({norm_imb:+.2f})")
            elif norm_imb < 0.25:
                orderflow_score = 0.25
                of_reasons.append(f"OF_weak_bull({norm_imb:+.2f})")
            else:
                orderflow_score = 0.1
                of_reasons.append(f"OF_bullish({norm_imb:+.2f})")

        # =====================================================
        # SCORE 3: TRANSFORMER / AI (0.25)
        # Uses calibrated probabilities
        # =====================================================
        ai_score = 0.0
        ai_reasons = []

        if htf_4h_trend > 0 or (htf_4h_trend == 0 and norm_imb > 0):
            # Looking for bullish AI signal
            ai_score = transformer_prediction.prob_up
            ai_reasons.append(f"AI_up={transformer_prediction.prob_up:.2f}")
        elif htf_4h_trend < 0 or (htf_4h_trend == 0 and norm_imb < 0):
            ai_score = transformer_prediction.prob_down
            ai_reasons.append(f"AI_down={transformer_prediction.prob_down:.2f}")
        else:
            ai_score = max(transformer_prediction.prob_up, transformer_prediction.prob_down)
            ai_reasons.append(f"AI_max={ai_score:.2f}")

        # =====================================================
        # COMPOSITE SCORE
        # =====================================================
        composite = (
            trend_score * self.W_TREND +
            orderflow_score * self.W_ORDERFLOW +
            ai_score * self.W_TRANSFORMER
        )
        composite = round(composite, 4)

        all_reasons = trend_reasons + of_reasons + ai_reasons

        if self.min_orderflow_imbalance > 0 and abs(float(norm_imb)) < self.min_orderflow_imbalance:
            signal.metadata = {
                "reject_reason": (
                    f"orderflow_imbalance_too_low ({abs(float(norm_imb)):.3f} < {self.min_orderflow_imbalance:.3f})"
                ),
                "composite_score": composite,
                "trend_score": round(trend_score, 3),
                "orderflow_score": round(orderflow_score, 3),
                "ai_score": round(ai_score, 3),
            }
            return signal

        # Determine side from strongest signals
        bull_signals = (1 if htf_4h_trend > 0 else 0) + (1 if norm_imb > 0.05 else 0) + \
                       (1 if transformer_prediction.prob_up > transformer_prediction.prob_down else 0)
        bear_signals = (1 if htf_4h_trend < 0 else 0) + (1 if norm_imb < -0.05 else 0) + \
                       (1 if transformer_prediction.prob_down > transformer_prediction.prob_up else 0)

        if bull_signals > bear_signals:
            is_long = True
        elif bear_signals > bull_signals:
            is_long = False
        elif htf_4h_trend != 0:
            is_long = htf_4h_trend > 0
        else:
            signal.metadata = {
                "reject_reason": f"no_direction_consensus (score={composite})",
                "composite_score": composite,
                "trend_score": round(trend_score, 3),
                "orderflow_score": round(orderflow_score, 3),
                "ai_score": round(ai_score, 3),
            }
            return signal

        side = "BUY" if is_long else "SELL"

        # =====================================================
        # EXHAUSTION GUARD: reject if entering at end of move
        # Check last 5 candles — if all moved in signal direction,
        # the move is likely exhausting and about to reverse.
        # =====================================================
        if len(klines) >= 7:
            last_candles = klines[-7:]
            consecutive_dir = 0
            for k in last_candles:
                c_open = float(k.get("open", 0))
                c_close = float(k.get("close", 0))
                if is_long and c_close > c_open:
                    consecutive_dir += 1
                elif not is_long and c_close < c_open:
                    consecutive_dir += 1
            if consecutive_dir >= 6:
                # 6+ candles already moved in our direction = exhaustion
                signal.metadata = {
                    "reject_reason": f"exhaustion_guard ({consecutive_dir}/7 candles same dir)",
                    "composite_score": composite,
                    "side": side,
                }
                return signal

        # =====================================================
        # CONTRA-TREND GUARD: reject if short-term price is
        # clearly moving AGAINST the signal direction.
        # If 9+ of last 10 candles are bullish and signal is SELL → reject
        # =====================================================
        if len(klines) >= 11:
            last_10 = klines[-10:]
            contra = 0
            for k in last_10:
                c_open = float(k.get("open", 0))
                c_close = float(k.get("close", 0))
                if is_long and c_close < c_open:
                    contra += 1
                elif not is_long and c_close > c_open:
                    contra += 1
            if contra >= 9:
                signal.metadata = {
                    "reject_reason": f"contra_trend_guard ({contra}/10 candles oppose {side})",
                    "composite_score": composite,
                    "side": side,
                }
                return signal

        # =====================================================
        # COUNTER-FLOW GUARD: reject if recent trades contradict signal
        # If signal is SELL but aggressive buying in last 30 trades (absorption)
        # =====================================================
        of_buy_vol = getattr(orderflow_snapshot, 'buy_volume', 0)
        of_sell_vol = getattr(orderflow_snapshot, 'sell_volume', 0)
        if is_long and of_sell_vol > 0 and of_sell_vol > of_buy_vol * 1.4:
            # Want to BUY but heavy selling — counter-flow
            signal.metadata = {
                "reject_reason": f"counter_flow_guard (BUY but sell_vol {of_sell_vol:.0f} > buy_vol {of_buy_vol:.0f} * 1.4)",
                "composite_score": composite,
                "side": side,
            }
            return signal
        if not is_long and of_buy_vol > 0 and of_buy_vol > of_sell_vol * 1.4:
            # Want to SELL but heavy buying — absorption / counter-flow
            signal.metadata = {
                "reject_reason": f"counter_flow_guard (SELL but buy_vol {of_buy_vol:.0f} > sell_vol {of_sell_vol:.0f} * 1.4)",
                "composite_score": composite,
                "side": side,
            }
            return signal

        # =====================================================
        # EMA TREND GUARD: reject if EMA(20) vs EMA(50) disagrees
        # with the signal direction on current timeframe candles
        # =====================================================
        if self.ema_trend_filter and len(klines) >= self.ema_slow_period + 5:
            closes, volumes = self._extract_closes_and_volumes(klines)
            ema_fast = self._compute_ema(closes, self.ema_fast_period)
            ema_slow = self._compute_ema(closes, self.ema_slow_period)
            ema_f = ema_fast[-1]
            ema_s = ema_slow[-1]
            if not np.isnan(ema_f) and not np.isnan(ema_s):
                ema_diff_pct = abs(ema_f - ema_s) / ema_s * 100 if ema_s > 0 else 0
                # Skip guard in flat EMA spread or for high-conviction setups.
                if ema_diff_pct >= self.ema_guard_min_diff_pct and composite < self.guard_confidence_bypass:
                    if is_long and ema_f < ema_s:
                        signal.metadata = {
                            "reject_reason": f"ema_trend_guard (BUY but EMA{self.ema_fast_period}={ema_f:.2f} < EMA{self.ema_slow_period}={ema_s:.2f}, diff={ema_diff_pct:.2f}%)",
                            "composite_score": composite,
                            "side": side,
                        }
                        return signal
                    if not is_long and ema_f > ema_s:
                        signal.metadata = {
                            "reject_reason": f"ema_trend_guard (SELL but EMA{self.ema_fast_period}={ema_f:.2f} > EMA{self.ema_slow_period}={ema_s:.2f}, diff={ema_diff_pct:.2f}%)",
                            "composite_score": composite,
                            "side": side,
                        }
                        return signal

        # =====================================================
        # MOMENTUM GUARD: reject if price momentum (close[-1] vs
        # close[-N]) contradicts the signal direction
        # =====================================================
        if self.momentum_filter and len(klines) >= self.momentum_lookback + 1:
            closes_m, _ = self._extract_closes_and_volumes(klines)
            momentum = closes_m[-1] - closes_m[-self.momentum_lookback]
            # Ignore micro-momentum noise below configured threshold.
            price_ref = closes_m[-1] if closes_m[-1] > 0 else 1
            momentum_pct = abs(momentum) / price_ref * 100
            if momentum_pct > self.momentum_guard_min_pct and is_long and momentum < 0 and composite < self.guard_confidence_bypass:
                signal.metadata = {
                    "reject_reason": f"momentum_guard (BUY but momentum={momentum:.4f} ({momentum_pct:.2f}%) over {self.momentum_lookback} bars)",
                    "composite_score": composite,
                    "side": side,
                }
                return signal
            if momentum_pct > self.momentum_guard_min_pct and not is_long and momentum > 0 and composite < self.guard_confidence_bypass:
                signal.metadata = {
                    "reject_reason": f"momentum_guard (SELL but momentum={momentum:+.4f} ({momentum_pct:.2f}%) over {self.momentum_lookback} bars)",
                    "composite_score": composite,
                    "side": side,
                }
                return signal

        # =====================================================
        # VOLUME GUARD: reject if current volume is below
        # average of last N candles (no conviction behind move)
        # =====================================================
        if self.volume_filter and len(klines) >= self.volume_lookback + 1:
            _, vols = self._extract_closes_and_volumes(klines)
            avg_vol = np.mean(vols[-self.volume_lookback - 1:-1])  # avg of prev N candles
            cur_vol = vols[-1]
            if avg_vol > 0 and cur_vol < avg_vol * self.volume_guard_min_ratio and composite < self.guard_confidence_bypass:
                signal.metadata = {
                    "reject_reason": f"volume_guard (vol={cur_vol:.0f} < avg{self.volume_lookback}={avg_vol:.0f})",
                    "composite_score": composite,
                    "side": side,
                }
                return signal

        # =====================================================
        # THRESHOLD CHECK
        # =====================================================
        if composite < self.entry_threshold:
            signal.metadata = {
                "reject_reason": f"score_below_threshold ({composite:.3f} < {self.entry_threshold})",
                "composite_score": composite,
                "trend_score": round(trend_score, 3),
                "orderflow_score": round(orderflow_score, 3),
                "ai_score": round(ai_score, 3),
                "details": " | ".join(all_reasons),
            }
            return signal

        # =====================================================
        # ZONE CHECK — need a valid FVG/OB for SL/TP levels
        # (soft requirement: enhances score but not hard block)
        # =====================================================
        active_zone = None
        if zone_context is not None:
            if is_long:
                active_zone = zone_context.price_in_bullish_zone(current_price) or \
                              zone_context.price_near_bullish_zone(current_price, self.zone_proximity_pct)
            else:
                active_zone = zone_context.price_in_bearish_zone(current_price) or \
                              zone_context.price_near_bearish_zone(current_price, self.zone_proximity_pct)

        # Anti-chase: skip entries that are too far from zone (often late and stop-prone)
        if active_zone is not None and self.max_entry_extension_atr > 0 and atr_value > 0:
            if is_long:
                extension = max(0.0, current_price - float(active_zone.high))
            else:
                extension = max(0.0, float(active_zone.low) - current_price)
            if extension > atr_value * self.max_entry_extension_atr:
                signal.metadata = {
                    "reject_reason": "entry_too_extended_from_zone",
                    "extension_atr": round(extension / atr_value, 3),
                    "composite_score": composite,
                }
                return signal

        # =====================================================
        # COMPUTE SL / TP
        # =====================================================
        bos = structure.last_bos if has_structure else None
        tp_confirmed_by_structure = False

        if is_long:
            if has_structure and structure.sweep_low > 0:
                sl = structure.sweep_low - atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_long(current_price, atr_value)
            else:
                sl = current_price - atr_value * 2.5
            if has_structure and structure.previous_high > current_price:
                tp1 = structure.previous_high
                tp_confirmed_by_structure = True
            else:
                tp1 = current_price + atr_value * 3.0
            if zone_context:
                has_struct_targets = bool(
                    [z.low for z in zone_context.all_bearish_zones if not z.mitigated and z.low > current_price]
                    or [r for r in zone_context.resistance_levels if r > current_price]
                )
                _, struct_tp2 = zone_context.structural_tp_long(current_price, atr_value)
                if has_struct_targets:
                    tp_confirmed_by_structure = True
                tp2 = max(struct_tp2, tp1)
            else:
                tp2 = tp1 + atr_value * 2.0
            if liq_analysis.target_level > current_price and liq_analysis.signal > 0:
                tp2 = max(tp2, liq_analysis.target_level)
        else:
            if has_structure and getattr(structure, 'sweep_high', 0) > 0:
                sl = structure.sweep_high + atr_value * self.sl_buffer_atr_mult
            elif zone_context:
                sl = zone_context.structural_sl_short(current_price, atr_value)
            else:
                sl = current_price + atr_value * 2.5
            if has_structure and structure.previous_low < current_price:
                tp1 = structure.previous_low
                tp_confirmed_by_structure = True
            else:
                tp1 = current_price - atr_value * 3.0
            if zone_context:
                has_struct_targets = bool(
                    [z.high for z in zone_context.all_bullish_zones if not z.mitigated and z.high < current_price]
                    or [s for s in zone_context.support_levels if s < current_price]
                )
                _, struct_tp2 = zone_context.structural_tp_short(current_price, atr_value)
                if has_struct_targets:
                    tp_confirmed_by_structure = True
                tp2 = min(struct_tp2, tp1)
            else:
                tp2 = tp1 - atr_value * 2.0
            if liq_analysis.target_level > 0 and liq_analysis.target_level < current_price and liq_analysis.signal < 0:
                tp2 = min(tp2, liq_analysis.target_level)

        if self.require_structural_tp and not tp_confirmed_by_structure:
            signal.metadata = {
                "reject_reason": "tp_not_structural",
                "composite_score": composite,
            }
            return signal

        # Enforce minimum stop distance (price% floor + ATR floor)
        min_stop_dist = max(
            current_price * (self.min_stop_distance_pct / 100),
            atr_value * self.min_stop_atr_mult,
        )
        if abs(current_price - sl) < min_stop_dist:
            sl = current_price - min_stop_dist if is_long else current_price + min_stop_dist

        if is_long and sl >= current_price:
            signal.metadata["reject_reason"] = "invalid_sl_long"
            return signal
        if not is_long and sl <= current_price:
            signal.metadata["reject_reason"] = "invalid_sl_short"
            return signal

        risk = abs(current_price - sl)
        if risk <= 0:
            signal.metadata["reject_reason"] = "zero_risk"
            return signal

        # TP must satisfy min RR
        take_profit = tp2
        rr_min_tp = current_price + risk * self.min_rr_ratio if is_long else current_price - risk * self.min_rr_ratio
        if is_long:
            take_profit = max(take_profit, rr_min_tp)
        else:
            take_profit = min(take_profit, rr_min_tp)

        min_target_dist = current_price * (self.min_target_profit_pct / 100)
        if abs(take_profit - current_price) < min_target_dist:
            take_profit = current_price + min_target_dist if is_long else current_price - min_target_dist

        reward = abs(take_profit - current_price)
        rr_ratio = reward / risk if risk > 0 else 0.0

        # =====================================================
        # HARD CHECK: RISK/REWARD >= min_rr_ratio
        # =====================================================
        if rr_ratio + 1e-6 < self.min_rr_ratio:
            signal.metadata = {
                "reject_reason": f"rr_too_low ({rr_ratio:.4f} < {self.min_rr_ratio})",
                "composite_score": composite,
            }
            return signal

        trained_win_prob = self._predict_trained_win_prob(
            composite_score=composite,
            trend_score=trend_score,
            orderflow_score=orderflow_score,
            ai_score=ai_score,
            normalized_imbalance=norm_imb,
            rr_ratio=rr_ratio,
            htf_4h_trend=htf_4h_trend,
        )
        if trained_win_prob is not None and trained_win_prob < self.trained_model_min_prob:
            signal.metadata = {
                "reject_reason": (
                    f"trained_model_low_prob ({trained_win_prob:.3f} < {self.trained_model_min_prob:.3f})"
                ),
                "composite_score": composite,
                "trained_model_prob": round(trained_win_prob, 4),
            }
            return signal

        blended_confidence = composite
        if trained_win_prob is not None:
            blend = self._clamp(self.trained_model_blend, 0.0, 1.0)
            blended_confidence = composite * (1.0 - blend) + trained_win_prob * blend

        # =====================================================
        # ENTRY — all checks passed
        # =====================================================
        side = "BUY" if is_long else "SELL"
        all_reasons.append(f"RR={rr_ratio:.1f}")
        all_reasons.append(f"score={composite:.2f}")
        struct_trend = structure.trend.value if has_structure else "range"

        # Entry range hint for manual execution (signal-only)
        range_mult = max(float(self.entry_range_atr_mult), 0.0)
        if is_long:
            entry_range_low = max(current_price - atr_value * range_mult, 0.0)
            entry_range_high = current_price
        else:
            entry_range_low = current_price
            entry_range_high = current_price + atr_value * range_mult

        signal.should_enter = True
        signal.side = side
        signal.confidence = round(blended_confidence, 4)
        signal.stop_loss = round(sl, 8)
        signal.take_profit = round(take_profit, 8)
        signal.rr_ratio = round(rr_ratio, 2)
        signal.capital_score = round(blended_confidence * rr_ratio, 4)
        signal.reasons = all_reasons

        # A/B/C signal grading
        entry_zone_str = f"{active_zone.kind}_{active_zone.bias}" if active_zone else "no_zone"
        htf_aligned = (htf_4h_trend > 0 and is_long) or (htf_4h_trend < 0 and not is_long)
        signal.grade = classify_signal_grade(
            confidence=blended_confidence,
            rr_ratio=rr_ratio,
            has_sweep=sweep is not None,
            has_bos=bos is not None,
            htf_aligned=htf_aligned,
            entry_zone=entry_zone_str,
        )
        all_reasons.append(f"grade={signal.grade}")

        signal.metadata = {
            "composite_score": composite,
            "smc_score": composite,
            "trend_score": round(trend_score, 3),
            "orderflow_score": round(orderflow_score, 3),
            "ai_score": round(ai_score, 3),
            "normalized_imbalance": norm_imb,
            "target_level": tp2,
            "protective_liq_level": round(sl, 8),
            "transformer_prob_up": transformer_prediction.prob_up,
            "transformer_prob_down": transformer_prediction.prob_down,
            "transformer_prob_flat": transformer_prediction.prob_flat,
            "regime": regime_prediction.regime.value,
            "spread_pct": spread_pct,
            "liq_distance_pct": liq_analysis.distance_to_target_pct,
            "liq_signal": liq_analysis.signal,
            "liq_magnet": liq_analysis.magnet_direction,
            "tp1_level": tp1,
            "tp2_level": tp2,
            "tp_confirmed_by_structure": tp_confirmed_by_structure,
            "entry_zone": entry_zone_str,
            "struct_trend": struct_trend,
            "has_bos": bos is not None,
            "has_sweep": sweep is not None,
            "sweep_direction": sweep.direction if sweep else "none",
            "funding_rate": funding_rate,
            "htf_4h_trend": htf_4h_trend,
            "trained_model_prob": round(trained_win_prob, 4) if trained_win_prob is not None else None,
            "trained_model_applied": trained_win_prob is not None,
            "blended_confidence": round(blended_confidence, 4),
            "entry_range_low": round(entry_range_low, 8),
            "entry_range_high": round(entry_range_high, 8),
            "signal_grade": signal.grade,
        }

        if self.require_sweep and not signal.metadata.get("has_sweep", False):
            signal.should_enter = False
            signal.metadata["reject_reason"] = "require_sweep_missing"
            return signal

        if self.min_smc_score > 0 and float(signal.metadata.get("smc_score", 0.0) or 0.0) < self.min_smc_score:
            signal.should_enter = False
            signal.metadata["reject_reason"] = (
                f"smc_score_too_low ({float(signal.metadata.get('smc_score', 0.0) or 0.0):.3f} < {self.min_smc_score:.3f})"
            )
            return signal

        return signal
