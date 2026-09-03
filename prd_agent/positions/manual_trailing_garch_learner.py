"""
Наблюдение ручных переносов SL на бирже → правила GARCH-трейлинга по режимам calm/normal/storm.

Когда вы двигаете стоп вручную (трейлинг бота выключен или origin=manual),
модуль записывает дистанцию SL от цены и режим GARCH в state JSON.
После min_samples на режим — подстраивает distance_mult (с clamp).
"""
from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from prd_agent.positions.exit_management import profit_pct
from prd_agent.positions.trailing_volatility_regime import (
    TrailingVolatilityRegimeConfig,
    compute_trailing_garch_distance_factor,
    regime_distance_mult,
)
from prd_agent.positions.garch_tp_peak_retrace import (
    GarchTpPeakRetraceConfig,
    format_telegram_tp_retrace_summary,
)
from prd_agent.risk.volatility_regime_sizing import closes_from_klines

logger = logging.getLogger("prd_agent.manual_trailing_garch")


def _trail_cfg_from_root(root_cfg: Mapping[str, Any]) -> TrailingVolatilityRegimeConfig:
    positions = root_cfg.get("positions") if isinstance(root_cfg.get("positions"), dict) else {}
    if isinstance(positions, dict) and positions.get("trailing_volatility_regime"):
        return TrailingVolatilityRegimeConfig.from_cfg(positions)
    return TrailingVolatilityRegimeConfig.from_cfg(root_cfg)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sl_distance_pct(side: str, mark: float, sl: float) -> float:
    if mark <= 0 or sl <= 0:
        return 0.0
    if str(side).lower() in ("buy", "long"):
        return max(0.0, (mark - sl) / mark * 100.0)
    return max(0.0, (sl - mark) / mark * 100.0)


def _is_tightening(side: str, old_sl: float, new_sl: float) -> bool:
    if old_sl <= 0 or new_sl <= 0:
        return False
    if str(side).lower() in ("buy", "long"):
        return new_sl > old_sl
    return new_sl < old_sl


@dataclass
class ManualTrailingGarchConfig:
    enabled: bool = True
    learn_from_manual_only: bool = True
    min_sl_move_pct: float = 0.03
    min_profit_pct: float = 0.05
    min_samples_per_regime: int = 5
    auto_apply_learned_mult: bool = True
    blend_weight: float = 0.45
    state_path: str = "data/garch/manual_trailing_rules.json"
    log_marker: str = "Trailing GARCH learn"

    @classmethod
    def from_cfg(cls, root_cfg: Mapping[str, Any]) -> "ManualTrailingGarchConfig":
        raw = root_cfg.get("manual_trailing_garch_learning")
        if not isinstance(raw, dict):
            return cls(enabled=False)
        return cls(
            enabled=bool(raw.get("enabled", True)),
            learn_from_manual_only=bool(raw.get("learn_from_manual_only", True)),
            min_sl_move_pct=float(raw.get("min_sl_move_pct", 0.03) or 0.03),
            min_profit_pct=float(raw.get("min_profit_pct", 0.05) or 0.05),
            min_samples_per_regime=int(raw.get("min_samples_per_regime", 5) or 5),
            auto_apply_learned_mult=bool(raw.get("auto_apply_learned_mult", True)),
            blend_weight=float(raw.get("blend_weight", 0.45) or 0.45),
            state_path=str(raw.get("state_path", "data/garch/manual_trailing_rules.json")),
        )


@dataclass
class RegimeRuleStats:
    samples: List[float] = field(default_factory=list)
    distance_pcts: List[float] = field(default_factory=list)
    profit_pcts: List[float] = field(default_factory=list)
    learned_mult: float = 1.0
    baseline_mult: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "samples": len(self.samples),
            "learned_mult": round(self.learned_mult, 4),
            "baseline_mult": round(self.baseline_mult, 4),
            "median_distance_pct": round(statistics.median(self.distance_pcts), 4)
            if self.distance_pcts
            else 0.0,
            "median_profit_pct": round(statistics.median(self.profit_pcts), 4)
            if self.profit_pcts
            else 0.0,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RegimeRuleStats":
        node = cls(
            learned_mult=float(data.get("learned_mult", 1.0) or 1.0),
            baseline_mult=float(data.get("baseline_mult", 1.0) or 1.0),
        )
        for _ in range(int(data.get("samples", 0) or 0)):
            node.samples.append(1.0)
        med_d = float(data.get("median_distance_pct", 0) or 0)
        med_p = float(data.get("median_profit_pct", 0) or 0)
        if med_d > 0:
            node.distance_pcts.append(med_d)
        if med_p > 0:
            node.profit_pcts.append(med_p)
        return node


class ManualTrailingGarchLearner:
    def __init__(self, root_cfg: Mapping[str, Any], data_dir: Path):
        self.root_cfg = dict(root_cfg)
        self.cfg = ManualTrailingGarchConfig.from_cfg(root_cfg)
        root = Path(str(root_cfg.get("_root", data_dir.parent)))
        rel = self.cfg.state_path
        self.state_path = Path(rel) if Path(rel).is_absolute() else root / rel
        self.trail_cfg = _trail_cfg_from_root(root_cfg)
        self._regimes: Dict[str, RegimeRuleStats] = {
            k: RegimeRuleStats() for k in ("calm", "normal", "storm")
        }
        self._last_sl_by_symbol: Dict[str, float] = {}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        rules = data.get("regimes")
        if isinstance(rules, dict):
            for key in ("calm", "normal", "storm"):
                if isinstance(rules.get(key), dict):
                    self._regimes[key] = RegimeRuleStats.from_dict(rules[key])

    def save(self) -> None:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "regimes": {k: v.to_dict() for k, v in self._regimes.items()},
            "notes": (
                "Правила из ручных переносов SL пользователя на бирже. "
                "GARCH regime → learned distance_mult."
            ),
        }
        self.state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def observe_exchange_sl(
        self,
        *,
        symbol: str,
        side: str,
        origin: str,
        mark: float,
        exchange_sl: float,
        entry: float,
        klines: Sequence[Mapping[str, Any]],
        bot_sent_sl: float,
        trailing_bot_enabled: bool,
    ) -> Optional[str]:
        """Записать ручной перенос SL (не от бота). Возвращает строку для лога или None."""
        if not self.cfg.enabled:
            return None
        sym = str(symbol or "").upper()
        if exchange_sl <= 0 or mark <= 0:
            return None
        if self.cfg.learn_from_manual_only and str(origin).lower() != "manual":
            return None

        prev = self._last_sl_by_symbol.get(sym, exchange_sl)
        self._last_sl_by_symbol[sym] = exchange_sl

        if prev <= 0:
            return None
        move_pct = abs(exchange_sl - prev) / max(prev, 1e-12) * 100.0
        if move_pct < self.cfg.min_sl_move_pct:
            return None
        if not _is_tightening(side, prev, exchange_sl):
            return None
        if bot_sent_sl > 0 and abs(exchange_sl - bot_sent_sl) / max(bot_sent_sl, 1e-12) < 0.0005:
            return None
        if trailing_bot_enabled and abs(exchange_sl - bot_sent_sl) / max(exchange_sl, 1e-12) < 0.001:
            return None

        p_pct = profit_pct(side, entry, mark)
        if p_pct < self.cfg.min_profit_pct:
            return None

        _mult, regime, note = compute_trailing_garch_distance_factor(
            klines=klines,
            trail_cfg=self.trail_cfg,
            root_cfg=self.root_cfg,
        )
        if regime not in self._regimes:
            regime = "normal"

        dist_pct = _sl_distance_pct(side, mark, exchange_sl)
        if dist_pct <= 0:
            return None

        stats = self._regimes[regime]
        stats.samples.append(dist_pct)
        stats.distance_pcts.append(dist_pct)
        stats.profit_pcts.append(p_pct)
        baseline = regime_distance_mult(regime, self.trail_cfg)
        stats.baseline_mult = baseline
        ref_dist = 0.35
        if stats.distance_pcts:
            ref_dist = max(0.08, statistics.median(stats.distance_pcts))
        ratio = _clamp(dist_pct / ref_dist, 0.5, 2.0)
        stats.learned_mult = _clamp(baseline * ratio, self.trail_cfg.clamp_min, self.trail_cfg.clamp_max)

        self.save()
        msg = (
            f"{self.cfg.log_marker}: {sym} {side} regime={regime} "
            f"ручной SL dist={dist_pct:.2f}% pnl={p_pct:.2f}% "
            f"learned_mult={stats.learned_mult:.2f} (n={len(stats.samples)})"
        )
        logger.info(msg)
        return msg

    def effective_regime_mult(self, regime: str) -> Tuple[float, str]:
        """Множитель для GARCH с учётом выученных правил."""
        key = str(regime or "normal").lower()
        if key not in self._regimes:
            key = "normal"
        baseline = regime_distance_mult(key, self.trail_cfg)
        stats = self._regimes[key]
        n = len(stats.samples)
        if (
            not self.cfg.auto_apply_learned_mult
            or n < self.cfg.min_samples_per_regime
            or stats.learned_mult <= 0
        ):
            return baseline, f"config n={n}"
        w = _clamp(float(self.cfg.blend_weight), 0.0, 1.0)
        blended = baseline * (1.0 - w) + stats.learned_mult * w
        blended = _clamp(blended, self.trail_cfg.clamp_min, self.trail_cfg.clamp_max)
        return blended, f"learned blend w={w:.2f} n={n}"

    def telegram_rules_summary(self) -> str:
        lines = ["<b>📐 GARCH — ваши правила трейлинга</b>", ""]
        for key in ("calm", "normal", "storm"):
            st = self._regimes[key]
            eff, src = self.effective_regime_mult(key)
            lines.append(
                f"<b>{key}</b>: mult=<code>{eff:.2f}</code> "
                f"(config {st.baseline_mult:.2f}, learned {st.learned_mult:.2f}, "
                f"samples={len(st.samples)}) — {src}"
            )
        lines.append("")
        lines.append(format_telegram_tp_retrace_summary(GarchTpPeakRetraceConfig.from_cfg(self.root_cfg)))
        lines.append("")
        lines.append(
            "<i>Бот учится, когда вы двигаете SL на Bybit вручную при включённом GARCH.</i>"
        )
        return "\n".join(lines)
