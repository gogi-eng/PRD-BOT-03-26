"""
Метрики качества портфеля по закрытым сделкам (аналог empyrical/pyfolio, без внешних зависимостей).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    if den == 0:
        return default
    return num / den


def compute_portfolio_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Считает Sharpe, max drawdown, profit factor, expectancy по списку closed-сделок."""
    if not rows:
        return {
            "n": 0,
            "total_pnl": 0.0,
            "winrate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown_usdt": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
        }

    pnls = [float(r.get("pnl", 0) or 0) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    n = len(pnls)
    winrate = len(wins) / n * 100.0 if n else 0.0
    avg_win = _safe_div(gross_profit, len(wins), 0.0)
    avg_loss = _safe_div(gross_loss, len(losses), 0.0)
    profit_factor = _safe_div(gross_profit, gross_loss, 0.0 if gross_loss else float("inf"))
    if math.isinf(profit_factor):
        profit_factor = 99.99
    loss_rate = len(losses) / n if n else 0.0
    win_rate_frac = len(wins) / n if n else 0.0
    expectancy = win_rate_frac * avg_win - loss_rate * avg_loss

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
    max_dd_pct = _safe_div(max_dd, peak, 0.0) * 100.0 if peak > 0 else 0.0

    mean_pnl = sum(pnls) / n
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / max(n - 1, 1)
    std_pnl = math.sqrt(variance) if variance > 0 else 0.0
    sharpe = _safe_div(mean_pnl, std_pnl, 0.0)

    downside = [min(0.0, p - mean_pnl) for p in pnls]
    down_var = sum(d * d for d in downside) / max(n, 1)
    down_std = math.sqrt(down_var) if down_var > 0 else 0.0
    sortino = _safe_div(mean_pnl, down_std, 0.0)

    return {
        "n": n,
        "total_pnl": sum(pnls),
        "winrate": winrate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "max_drawdown_usdt": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "sharpe": sharpe,
        "sortino": sortino,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "largest_win": max(pnls) if pnls else 0.0,
        "largest_loss": min(pnls) if pnls else 0.0,
    }


def format_portfolio_quality_telegram(
    metrics: Dict[str, Any],
    *,
    hours: float,
) -> str:
    if metrics.get("n", 0) == 0:
        return (
            f"<b>📊 Качество сделок ({hours:.0f} ч)</b>\n\n"
            "Закрытых сделок в журнале нет.\n"
            "<i>Журнал: data/trades/trade_history.jsonl</i>"
        )
    pf = metrics["profit_factor"]
    pf_txt = f"{pf:.2f}" if pf < 99 else "∞"
    lines = [
        f"<b>📊 Качество сделок ({hours:.0f} ч)</b>",
        "",
        f"Сделок: <b>{metrics['n']}</b> | Winrate: <b>{metrics['winrate']:.1f}%</b>",
        f"PnL суммарно: <b>{metrics['total_pnl']:+.2f}</b> USDT",
        "",
        "<b>Риск и доходность</b>",
        f"• Profit factor: <b>{pf_txt}</b> (прибыль/убыток)",
        f"• Expectancy: <b>{metrics['expectancy']:+.2f}</b> USDT/сделка",
        f"• Max drawdown: <b>{metrics['max_drawdown_usdt']:.2f}</b> USDT "
        f"({metrics['max_drawdown_pct']:.1f}% от пика)",
        f"• Sharpe (по сделкам): <b>{metrics['sharpe']:.2f}</b>",
        f"• Sortino: <b>{metrics['sortino']:.2f}</b>",
        "",
        "<b>Средние значения</b>",
        f"• Средний win: <b>{metrics['avg_win']:+.2f}</b> | "
        f"Средний loss: <b>{metrics['avg_loss']:.2f}</b>",
        f"• Крупнейший win: <b>{metrics['largest_win']:+.2f}</b> | "
        f"Крупнейший loss: <b>{metrics['largest_loss']:.2f}</b>",
        "",
        "<i>Метрики по закрытым сделкам бота (без pyfolio-зависимостей).</i>",
    ]
    return "\n".join(lines)
