#!/usr/bin/env python3
"""One-off helper: split TradingBot from main.py into bot/mixins/*.py (run from repo root)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
OUT_DIR = ROOT / "bot" / "mixins"

MIXIN_GROUPS: dict[str, list[str]] = {
    "helpers_mixin": [
        "_interval_to_seconds",
        "_last_closed_kline_ts",
        "_parse_iso_dt",
        "_unique_symbols",
        "_candle_dir",
        "_candle_body",
        "_ema",
        "_determine_4h_trend",
    ],
    "regime_mixin": [
        "_apply_tf_preset",
        "_resolve_regime_preset",
        "_detect_profile_regime",
        "_maybe_apply_regime_preset",
        "_switch_signal_mode",
    ],
    "notify_symbols_mixin": ["_notify_tg", "get_trade_symbols"],
    "lifecycle_mixin": ["run", "stop", "_should_scan_entries_now", "_get_cycle_sleep_sec"],
    "position_loop_mixin": ["_manage_positions"],
    "scanning_mixin": ["_scan_entries"],
    "analyze_entry_mixin": [
        "_analyze_symbol",
        "_passes_volatility_floor",
        "_resolve_signal_atr_pct",
        "_passes_strict_htf_mode",
        "_passes_impulse_retest_confirmation",
        "_build_ai_payload",
        "_build_claude_payload",
        "_passes_signal_quality_gate",
        "_zone_matches_side",
    ],
    "entry_exec_mixin": [
        "_execute_entry",
        "_maybe_pyramid_add",
        "_compute_partial_tp_price",
        "_same_side_cooldown_remaining",
        "_register_signal_timestamp",
    ],
    "correlation_mixin": [
        "_update_correlation_cache",
        "_passes_correlation_filter",
        "_same_side_peer_symbols",
    ],
    "feedback_mixin": [
        "_process_signal_feedback_loop",
        "_run_feedback_daily_retrain",
        "_is_quality_feedback_record",
        "_build_retrain_dataset",
    ],
    "liquidation_mixin": [
        "_resolve_liquidation_context",
        "_build_quasi_liquidation_model",
        "_build_synthetic_liquidation_events",
        "_build_directional_liq_fallback",
        "_heatmap_to_liq_analysis",
    ],
    "sync_manual_mixin": [
        "_sync_exchange_position",
        "_derive_manual_position_levels",
        "_apply_manual_trailing_profile",
        "_apply_profit_drawdown_profile",
        "_check_profit_drawdown_guard",
        "_notify_manual_sl_move",
        "_maybe_execute_partial_tp",
    ],
    "guards_mixin": [
        "_check_portfolio_take_profit",
        "_reset_basket_profit_state",
        "_check_basket_profit_guard",
        "_update_basket_histories",
        "_find_falling_symbol",
    ],
    "closes_mixin": [
        "_finalize_full_close",
        "_finalize_partial_close",
        "_calc_pnl",
        "_calc_pnl_pct",
        "_save_trade",
    ],
    "exchange_closed_mixin": [
        "_should_finalize_exchange_closed",
        "_filter_recent_closed_pnl",
        "_classify_exchange_closed_reason",
        "_set_exchange_close_meta",
        "_pop_exchange_close_meta",
        "_can_finalize_exchange_closed",
        "_set_exchange_closed_reentry_block",
        "_exchange_closed_reentry_remaining",
        "_exchange_closed_sync_pause_remaining",
    ],
}

# Flatten and validate
all_methods: set[str] = set()
for names in MIXIN_GROUPS.values():
    for n in names:
        if n in all_methods:
            raise SystemExit(f"duplicate method: {n}")
        all_methods.add(n)


def parse_class_methods(text: str) -> dict[str, str]:
    """Map method_name -> full source (4-space indented body including def line)."""
    lines = text.splitlines()
    if not lines[0].startswith("class TradingBot"):
        raise SystemExit("expected class TradingBot")
    i = 1
    # Skip class docstring (one-line or multi-line)
    if i < len(lines) and '"""' in lines[i]:
        if lines[i].count('"""') >= 2:
            i += 1
        else:
            i += 1
            while i < len(lines) and '"""' not in lines[i]:
                i += 1
            i += 1
    # Skip blank lines after docstring
    while i < len(lines) and lines[i].strip() == "":
        i += 1

    methods: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    pending_decorators: list[str] = []

    def flush():
        nonlocal current_name, current_lines
        if current_name and current_lines:
            methods[current_name] = "\n".join(current_lines)
        current_name = None
        current_lines = []

    while i < len(lines):
        line = lines[i]
        if re.match(r"^    @", line):
            pending_decorators.append(line)
            i += 1
            continue
        m = re.match(r"^    (async )?def (__init__|_?\w+)\(", line)
        if m:
            flush()
            current_name = m.group(2)
            current_lines = pending_decorators + [line]
            pending_decorators = []
        elif current_name is not None:
            current_lines.append(line)
        i += 1
    flush()
    if pending_decorators:
        raise SystemExit(f"trailing decorators without function: {pending_decorators}")
    return methods


def main() -> None:
    text = MAIN.read_text(encoding="utf-8")
    m = re.search(
        r"^class TradingBot:.*?(?=^async def main\(\):)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not m:
        raise SystemExit("TradingBot class not found")
    block = m.group(0)
    methods = parse_class_methods(block)

    if "__init__" not in methods:
        raise SystemExit("missing __init__")

    assigned: set[str] = set()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    mixin_class_names: dict[str, str] = {}
    init_body = methods["__init__"]

    for fname, mnames in MIXIN_GROUPS.items():
        cname = "".join(w.capitalize() for w in fname.split("_"))  # helpers_mixin -> HelpersMixin
        cname = "TradingBot" + cname.replace("Mixin", "") + "Mixin"
        if not cname.endswith("Mixin"):
            cname += "Mixin"
        mixin_class_names[fname] = cname
        parts: list[str] = [
            '"""Auto-split from main.TradingBot — see package bot.trading_bot."""',
            "from __future__ import annotations",
            "",
            "from bot.trading_bot_imports import *  # noqa: F401,F403",
            "",
            f"class {cname}:",
        ]
        for mn in mnames:
            if mn not in methods:
                raise SystemExit(f"method not found: {mn}")
            assigned.add(mn)
            parts.append(methods[mn])
            parts.append("")
        out_path = OUT_DIR / f"{fname}.py"
        out_path.write_text("\n".join(parts).rstrip() + "\n", encoding="utf-8")
        print("wrote", out_path.relative_to(ROOT))

    missing = set(methods.keys()) - assigned - {"__init__"}
    extra = assigned - set(methods.keys())
    if extra:
        raise SystemExit(f"internal error extra: {extra}")
    if missing:
        raise SystemExit(f"methods not assigned to any mixin: {sorted(missing)}")

    # __init__ goes to trading_bot.py manually — write stub
    init_path = ROOT / "bot" / "trading_bot_init_body.py"
    init_path.write_text(init_body + "\n", encoding="utf-8")
    print("wrote", init_path.relative_to(ROOT), "(paste into TradingBot.__init__)")


if __name__ == "__main__":
    main()
