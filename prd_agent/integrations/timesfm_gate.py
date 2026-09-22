"""
TimesFM 2.5: калибровка по символу (5 сигналов, 80% точность) → фильтр входа.

- Калибровка: на каждый сигнал бота фиксируем прогноз TimesFM и через horizon свечей
  сверяем с фактическим движением.
- После 5 проверенных сигналов по символу, если ≥80% совпадений — TimesFM блокирует
  входы против прогноза только для этого символа.
- Глобальный ВКЛ/ВЫКЛ — кнопка Telegram (state: data/timesfm/gate_state.json).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger("prd_agent.timesfm_gate")

_MODEL_SINGLETON: Dict[str, Any] = {"model": None, "compiled": False}


def side_to_direction(side: str) -> str:
    s = str(side or "").upper()
    if s in ("BUY", "LONG"):
        return "up"
    if s in ("SELL", "SHORT"):
        return "down"
    return "flat"


def direction_from_delta(delta_pct: float, eps: float = 0.02) -> str:
    if delta_pct > eps:
        return "up"
    if delta_pct < -eps:
        return "down"
    return "flat"


def actual_move_pct(closes: Sequence[float], horizon: int) -> float:
    if len(closes) < horizon + 1:
        return 0.0
    base = float(closes[0])
    future = float(closes[horizon])
    if base <= 0:
        return 0.0
    return (future - base) / base * 100.0


def evaluate_calibration(
    samples: Sequence[Dict[str, Any]],
    *,
    required: int,
    min_accuracy: float,
) -> Tuple[bool, float]:
    resolved = [s for s in samples if s.get("resolved")]
    if len(resolved) < required:
        return False, 0.0
    last = resolved[-required:]
    hits = sum(1 for s in last if s.get("correct"))
    acc = hits / required
    return acc >= min_accuracy, acc


@dataclass
class TimesFMGateConfig:
    enabled: bool
    calibration_samples: int
    min_accuracy: float
    horizon: int
    context_len: int
    kline_interval: str
    model_id: str
    direction_eps_pct: float

    @classmethod
    def from_cfg(cls, cfg: Dict[str, Any]) -> TimesFMGateConfig:
        block = cfg.get("timesfm_gate") if isinstance(cfg.get("timesfm_gate"), dict) else {}
        return cls(
            enabled=bool(block.get("enabled", False)),
            calibration_samples=int(block.get("calibration_samples", 5)),
            min_accuracy=float(block.get("min_accuracy", 0.8)),
            horizon=int(block.get("horizon", 12)),
            context_len=int(block.get("context_len", 512)),
            kline_interval=str(block.get("kline_interval", "15")),
            model_id=str(block.get("model_id", "google/timesfm-2.5-200m-pytorch")),
            direction_eps_pct=float(block.get("direction_eps_pct", 0.02)),
        )


class TimesFMGate:
    def __init__(self, cfg: Dict[str, Any], data_dir: Path):
        self.cfg = TimesFMGateConfig.from_cfg(cfg)
        self.data_dir = data_dir
        self.store_dir = data_dir / "timesfm"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.store_dir / "gate_state.json"
        self._state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {
                "global_enabled": bool(self.cfg.enabled),
                "symbols": {},
            }
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("global_enabled", bool(self.cfg.enabled))
        raw.setdefault("symbols", {})
        if not isinstance(raw["symbols"], dict):
            raw["symbols"] = {}
        return raw

    def _save_state(self) -> None:
        self.state_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def feature_enabled(self) -> bool:
        return bool(self.cfg.enabled)

    def get_global_enabled(self) -> bool:
        return bool(self._state.get("global_enabled", False))

    def toggle_global(self) -> bool:
        new_val = not self.get_global_enabled()
        self._state["global_enabled"] = new_val
        self._save_state()
        logger.info("TimesFM global toggled: %s", "ON" if new_val else "OFF")
        return new_val

    def _sym_state(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.upper()
        symbols = self._state.setdefault("symbols", {})
        if sym not in symbols or not isinstance(symbols[sym], dict):
            symbols[sym] = {
                "samples": [],
                "trading_enabled": False,
                "last_accuracy": 0.0,
            }
        return symbols[sym]

    def is_trading_enabled_for(self, symbol: str) -> bool:
        if not self.feature_enabled() or not self.get_global_enabled():
            return False
        return bool(self._sym_state(symbol).get("trading_enabled"))

    @staticmethod
    def _kline_ts_ms(k: Dict[str, Any]) -> int:
        raw = k.get("timestamp") or k.get("startTime") or k.get("open_time") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _kline_close(k: Dict[str, Any]) -> float:
        try:
            return float(k.get("close", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def _find_kline_index(self, klines: Sequence[Dict[str, Any]], ts_ms: int) -> int:
        idx = -1
        for i, k in enumerate(klines):
            if self._kline_ts_ms(k) <= ts_ms:
                idx = i
            else:
                break
        return idx

    def _build_context(self, klines: Sequence[Dict[str, Any]], idx: int) -> np.ndarray:
        start = max(0, idx + 1 - self.cfg.context_len)
        closes = [self._kline_close(klines[i]) for i in range(start, idx + 1)]
        return np.asarray(closes, dtype=np.float64)

    def _forecast_direction_sync(self, context: np.ndarray) -> Tuple[str, float]:
        try:
            import timesfm  # type: ignore
            import torch  # type: ignore
        except ImportError as exc:
            raise RuntimeError(f"timesfm/torch не установлены: {exc}") from exc

        if _MODEL_SINGLETON["model"] is None or not _MODEL_SINGLETON["compiled"]:
            torch.set_float32_matmul_precision("high")
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                self.cfg.model_id,
                force_download=False,
            )
            model.compile(
                timesfm.ForecastConfig(
                    max_context=max(self.cfg.context_len, 512),
                    max_horizon=max(self.cfg.horizon, 16),
                    normalize_inputs=True,
                    use_continuous_quantile_head=True,
                    force_flip_invariance=True,
                    infer_is_positive=True,
                    fix_quantile_crossing=True,
                    per_core_batch_size=1,
                )
            )
            _MODEL_SINGLETON["model"] = model
            _MODEL_SINGLETON["compiled"] = True

        model = _MODEL_SINGLETON["model"]
        point, _q = model.forecast(
            horizon=self.cfg.horizon,
            inputs=[context],
        )
        last = float(context[-1])
        pred = float(point[0, self.cfg.horizon - 1])
        if last <= 0:
            return "flat", 0.0
        delta_pct = (pred - last) / last * 100.0
        return direction_from_delta(delta_pct, self.cfg.direction_eps_pct), round(
            delta_pct, 4
        )

    async def _forecast_direction(self, context: np.ndarray) -> Tuple[str, float]:
        return await asyncio.to_thread(self._forecast_direction_sync, context)

    async def register_signal(
        self,
        exchange: Any,
        symbol: str,
        side: str,
        *,
        source: str = "orchestrator",
    ) -> None:
        if not self.feature_enabled():
            return
        sym = symbol.upper()
        sym_st = self._sym_state(sym)
        if sym_st.get("trading_enabled"):
            return
        pending = [
            s
            for s in sym_st.get("samples", [])
            if isinstance(s, dict) and not s.get("resolved")
        ]
        resolved = [
            s
            for s in sym_st.get("samples", [])
            if isinstance(s, dict) and s.get("resolved")
        ]
        if len(pending) + len(resolved) >= self.cfg.calibration_samples * 3:
            sym_st["samples"] = (resolved[-self.cfg.calibration_samples :] + pending)[
                -self.cfg.calibration_samples * 2 :
            ]
        try:
            klines = await exchange.get_klines(
                sym,
                interval=self.cfg.kline_interval,
                limit=min(self.cfg.context_len + self.cfg.horizon + 32, 1000),
            )
        except Exception as exc:
            logger.warning("TimesFM register %s: klines failed: %s", sym, exc)
            return
        if not klines or len(klines) < 32:
            logger.warning("TimesFM register %s: мало свечей (%s)", sym, len(klines or []))
            return
        ts_ms = self._kline_ts_ms(klines[-1])
        idx = len(klines) - 1
        ctx = self._build_context(klines, idx)
        if len(ctx) < 32:
            return
        try:
            tfm_dir, tfm_delta = await self._forecast_direction(ctx)
        except Exception as exc:
            logger.warning("TimesFM register %s: forecast failed: %s", sym, exc)
            return
        row = {
            "signal_at_ms": ts_ms,
            "signal_at": datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat(),
            "side": side,
            "source": source,
            "tfm_direction": tfm_dir,
            "tfm_change_pct": tfm_delta,
            "signal_direction": side_to_direction(side),
            "resolved": False,
            "correct": None,
            "actual_direction": "",
            "actual_move_pct": 0.0,
        }
        samples: List[Dict[str, Any]] = list(sym_st.get("samples") or [])
        samples.append(row)
        sym_st["samples"] = samples
        self._save_state()
        logger.info(
            "TimesFM calib %s %s: tfm=%s (Δ=%.3f%%) pending=%d/%d",
            sym,
            side,
            tfm_dir,
            tfm_delta,
            sum(1 for s in samples if not s.get("resolved")),
            self.cfg.calibration_samples,
        )

    async def resolve_pending(self, exchange: Any) -> None:
        if not self.feature_enabled():
            return
        horizon_ms = self.cfg.horizon * int(self.cfg.kline_interval) * 60 * 1000
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        changed = False
        for sym, sym_st in list((self._state.get("symbols") or {}).items()):
            if not isinstance(sym_st, dict):
                continue
            samples: List[Dict[str, Any]] = list(sym_st.get("samples") or [])
            need_klines = any(
                isinstance(s, dict)
                and not s.get("resolved")
                and now_ms - int(s.get("signal_at_ms", 0) or 0) >= horizon_ms
                for s in samples
            )
            if not need_klines:
                continue
            try:
                klines = await exchange.get_klines(
                    sym,
                    interval=self.cfg.kline_interval,
                    limit=min(1000, self.cfg.context_len + self.cfg.horizon + 64),
                )
            except Exception as exc:
                logger.warning("TimesFM resolve %s: klines: %s", sym, exc)
                continue
            for s in samples:
                if not isinstance(s, dict) or s.get("resolved"):
                    continue
                sig_ms = int(s.get("signal_at_ms", 0) or 0)
                if now_ms - sig_ms < horizon_ms:
                    continue
                idx = self._find_kline_index(klines, sig_ms)
                if idx < 0 or idx + self.cfg.horizon >= len(klines):
                    continue
                closes = [self._kline_close(klines[i]) for i in range(idx, idx + self.cfg.horizon + 1)]
                act_pct = actual_move_pct(closes, self.cfg.horizon)
                act_dir = direction_from_delta(act_pct, self.cfg.direction_eps_pct)
                tfm_dir = str(s.get("tfm_direction", ""))
                s["resolved"] = True
                s["actual_move_pct"] = round(act_pct, 4)
                s["actual_direction"] = act_dir
                s["correct"] = tfm_dir == act_dir and act_dir != "flat"
                changed = True
                logger.info(
                    "TimesFM resolved %s: tfm=%s fact=%s move=%.2f%% correct=%s",
                    sym,
                    tfm_dir,
                    act_dir,
                    act_pct,
                    s["correct"],
                )
            sym_st["samples"] = samples
            resolved = [s for s in samples if s.get("resolved")]
            if len(resolved) >= self.cfg.calibration_samples and not sym_st.get(
                "trading_enabled"
            ):
                ok, acc = evaluate_calibration(
                    resolved,
                    required=self.cfg.calibration_samples,
                    min_accuracy=self.cfg.min_accuracy,
                )
                sym_st["last_accuracy"] = round(acc * 100, 1)
                if ok:
                    sym_st["trading_enabled"] = True
                    logger.info(
                        "TimesFM TRADING ON for %s (accuracy %.0f%% on last %d)",
                        sym,
                        acc * 100,
                        self.cfg.calibration_samples,
                    )
                else:
                    sym_st["trading_enabled"] = False
                    sym_st["samples"] = resolved[-self.cfg.calibration_samples :]
                    logger.info(
                        "TimesFM calib reset %s: accuracy %.0f%% < %.0f%%",
                        sym,
                        acc * 100,
                        self.cfg.min_accuracy * 100,
                    )
        if changed:
            self._save_state()

    async def check_entry(
        self,
        exchange: Any,
        symbol: str,
        side: str,
    ) -> Tuple[bool, str]:
        if not self.is_trading_enabled_for(symbol):
            return True, ""
        sym = symbol.upper()
        try:
            klines = await exchange.get_klines(
                sym,
                interval=self.cfg.kline_interval,
                limit=min(self.cfg.context_len + 8, 600),
            )
        except Exception as exc:
            logger.warning("TimesFM check %s: klines: %s — пропускаем фильтр", sym, exc)
            return True, ""
        if not klines:
            return True, ""
        idx = len(klines) - 1
        ctx = self._build_context(klines, idx)
        if len(ctx) < 32:
            return True, ""
        try:
            tfm_dir, tfm_delta = await self._forecast_direction(ctx)
        except Exception as exc:
            logger.warning("TimesFM check %s: forecast: %s — пропускаем фильтр", sym, exc)
            return True, ""
        sig_dir = side_to_direction(side)
        if tfm_dir == sig_dir or tfm_dir == "flat":
            logger.info(
                "TimesFM pass %s %s: tfm=%s (Δ=%.3f%%)",
                sym,
                side,
                tfm_dir,
                tfm_delta,
            )
            return True, ""
        reason = (
            f"timesfm: direction mismatch (tfm={tfm_dir} Δ={tfm_delta:.3f}% "
            f"signal={sig_dir})"
        )
        logger.info("TimesFM block %s %s: %s", sym, side, reason)
        return False, reason

    def build_telegram_report(self, header: str = "") -> str:
        lines = []
        if header:
            lines.append(f"<b>{header}</b>\n")
        if not self.feature_enabled():
            lines.append("TimesFM в config: <b>выключен</b> (timesfm_gate.enabled)")
            return "\n".join(lines)
        g = "ВКЛ" if self.get_global_enabled() else "ВЫКЛ"
        lines.append(f"📈 TimesFM глобально: <b>{g}</b>")
        lines.append(
            f"Калибровка: {self.cfg.calibration_samples} сигналов, "
            f"порог {self.cfg.min_accuracy * 100:.0f}%, горизонт {self.cfg.horizon}×{self.cfg.kline_interval}m"
        )
        symbols = self._state.get("symbols") or {}
        if not symbols:
            lines.append("\n<i>Пока нет данных калибровки.</i>")
            return "\n".join(lines)
        lines.append("\n<b>По символам:</b>")
        for sym in sorted(symbols.keys()):
            st = symbols[sym]
            if not isinstance(st, dict):
                continue
            samples = [s for s in (st.get("samples") or []) if isinstance(s, dict)]
            resolved = [s for s in samples if s.get("resolved")]
            pending = len(samples) - len(resolved)
            hits = sum(1 for s in resolved if s.get("correct"))
            acc = st.get("last_accuracy", 0.0)
            trade = "✅ фильтр ON" if st.get("trading_enabled") else "⏳ калибровка"
            lines.append(
                f"• <b>{sym}</b> {trade} — resolved {len(resolved)}/{self.cfg.calibration_samples}, "
                f"pending {pending}, hits {hits}, acc {acc}%"
            )
        lines.append(
            "\n<i>Фильтр блокирует вход, если прогноз TimesFM не совпадает с направлением сигнала.</i>"
        )
        return "\n".join(lines)
