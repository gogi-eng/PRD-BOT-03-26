import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _strip_docstring_and_future_import(source: str) -> str:
    """Remove module docstring and ``from __future__ import annotations`` so chunks can be concatenated."""
    text = source.lstrip("\ufeff")
    tree = ast.parse(text)
    lines = text.splitlines(True)
    cut_line_idx = 0  # 0-based: first line to keep
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                cut_line_idx = node.end_lineno
                continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            cut_line_idx = node.end_lineno
            continue
        break
    return "".join(lines[cut_line_idx:]).lstrip("\n")


def write_bot_main_shim_for_tests() -> None:
    """Some tests open ``bot/main.py`` and grep for implementation strings."""
    parts: list[str] = [
        '"""AUTO-GENERATED aggregate of TradingBot sources for static tests; do not edit.\n'
        "Real entry: repo root ``main.py``, implementation: ``bot/trading_bot.py`` + ``bot/mixins/``.\n"
        '"""\n'
        "from __future__ import annotations\n\n",
    ]
    mix = sorted((ROOT / "bot" / "mixins").glob("*.py"))
    for p in [ROOT / "bot" / "trading_bot.py", *mix]:
        parts.append(f"# === {p.relative_to(ROOT)} ===\n")
        parts.append(_strip_docstring_and_future_import(p.read_text(encoding="utf-8")))
        parts.append("\n\n")
    (ROOT / "bot" / "main.py").write_text("".join(parts), encoding="utf-8")


header = '''"""TradingBot orchestrator — composed from mixins (legacy main.TradingBot)."""
from __future__ import annotations

from bot.state import BasketProfitState
from bot.trading_bot_imports import *  # noqa: F403

from bot.mixins.helpers_mixin import TradingBotHelpersMixin
from bot.mixins.regime_mixin import TradingBotRegimeMixin
from bot.mixins.notify_symbols_mixin import TradingBotNotifySymbolsMixin
from bot.mixins.lifecycle_mixin import TradingBotLifecycleMixin
from bot.mixins.position_loop_mixin import TradingBotPositionLoopMixin
from bot.mixins.scanning_mixin import TradingBotScanningMixin
from bot.mixins.analyze_entry_mixin import TradingBotAnalyzeEntryMixin
from bot.mixins.entry_exec_mixin import TradingBotEntryExecMixin
from bot.mixins.correlation_mixin import TradingBotCorrelationMixin
from bot.mixins.feedback_mixin import TradingBotFeedbackMixin
from bot.mixins.liquidation_mixin import TradingBotLiquidationMixin
from bot.mixins.sync_manual_mixin import TradingBotSyncManualMixin
from bot.mixins.guards_mixin import TradingBotGuardsMixin
from bot.mixins.closes_mixin import TradingBotClosesMixin
from bot.mixins.exchange_closed_mixin import TradingBotExchangeClosedMixin


class TradingBot(
    TradingBotHelpersMixin,
    TradingBotRegimeMixin,
    TradingBotNotifySymbolsMixin,
    TradingBotLifecycleMixin,
    TradingBotPositionLoopMixin,
    TradingBotScanningMixin,
    TradingBotAnalyzeEntryMixin,
    TradingBotEntryExecMixin,
    TradingBotCorrelationMixin,
    TradingBotFeedbackMixin,
    TradingBotLiquidationMixin,
    TradingBotSyncManualMixin,
    TradingBotGuardsMixin,
    TradingBotClosesMixin,
    TradingBotExchangeClosedMixin,
):
    """Main trading bot orchestrator."""

'''
init_body = (ROOT / "bot/trading_bot_init_body.py").read_text(encoding="utf-8")
(ROOT / "bot/trading_bot.py").write_text(header + init_body + "\n", encoding="utf-8")
write_bot_main_shim_for_tests()
print("OK", ROOT / "bot/trading_bot.py")
