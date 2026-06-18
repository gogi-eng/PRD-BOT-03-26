"""Ступенчатый скор наблюдения MARKET SCANNER (0–100). Не OpenRouter и не «уверенность AI»."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class ScannerScoreInput:
    range_pct: float
    atr_pct: float
    confirmed_bos: bool
    volume_ratio: float
    turnover_24h: float
    has_fvg: bool
    max_range_pct: float
    max_atr_pct: float
    min_volume_ratio: float
    min_24h_volume_usdt: float
    consolidation_bars: int = 36
    scenario: str = "PUMP"
    bos_level: float = 0.0
    fvg_detail: str = ""


def compute_market_scanner_observation_score(inp: ScannerScoreInput) -> Tuple[int, List[str]]:
    """
    Дискретные «корзины» очков. Разные монеты с похожей структурой часто получают
    одинаковый итог (например 80/100) — это ожидаемо, не баг округления AI.
    """
    score = 0
    reasons: List[str] = []
    bars = int(inp.consolidation_bars or 36)

    compact_range = inp.range_pct <= inp.max_range_pct
    compact_atr = inp.atr_pct <= inp.max_atr_pct
    if compact_range:
        score += 22
        reasons.append(f"консолидация {bars} свечей, диапазон {inp.range_pct:.2f}%")
    elif inp.range_pct <= inp.max_range_pct * 1.4:
        score += 10
        reasons.append(f"умеренный диапазон {inp.range_pct:.2f}%")
    if compact_atr:
        score += 14
        reasons.append(f"ATR сжат до {inp.atr_pct:.2f}%")
    if inp.confirmed_bos:
        score += 25
        direction = "вверх" if str(inp.scenario).upper() == "PUMP" else "вниз"
        if inp.bos_level > 0:
            reasons.append(f"BOS {direction} через {inp.bos_level:.8g}")
        else:
            reasons.append(f"BOS {direction}")
    else:
        score += 12
        if inp.bos_level > 0:
            reasons.append(
                f"цена у границы диапазона {inp.bos_level:.8g}, ждём подтверждение BOS"
            )
        else:
            reasons.append("цена у границы диапазона, ждём подтверждение BOS")
    if inp.volume_ratio >= inp.min_volume_ratio:
        score += 15
        reasons.append(f"объём {inp.volume_ratio:.2f}x к среднему")
    elif inp.volume_ratio >= 1.1:
        score += 6
        reasons.append(f"объём слегка выше среднего: {inp.volume_ratio:.2f}x")
    if inp.has_fvg:
        score += 12
        reasons.append(inp.fvg_detail or "FVG в структуре")
    if inp.turnover_24h >= inp.min_24h_volume_usdt * 3:
        score += 5
        reasons.append(f"оборот 24ч {inp.turnover_24h / 1_000_000:.1f}M USDT")

    final = max(0, min(100, int(round(score))))
    return final, reasons
