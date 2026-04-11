#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_allocator_class():
    module_name = "capital_allocator_module_for_tests"
    module_path = Path(__file__).resolve().parents[2] / "bot" / "engine" / "capital_allocator.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.MultiSymbolCapitalAllocator


MultiSymbolCapitalAllocator = _load_allocator_class()


def test_allocator_can_renormalize_weights_for_selected_top_n():
    allocator = MultiSymbolCapitalAllocator()
    candidates = [
        {"symbol": "A", "signal_strength": 0.9, "liquidity": 100, "volatility": 1.0, "spread": 0.01},
        {"symbol": "B", "signal_strength": 0.8, "liquidity": 90, "volatility": 1.0, "spread": 0.01},
        {"symbol": "C", "signal_strength": 0.7, "liquidity": 80, "volatility": 1.0, "spread": 0.01},
        {"symbol": "D", "signal_strength": 0.6, "liquidity": 70, "volatility": 1.0, "spread": 0.01},
    ]

    ranked = allocator.allocate(candidates, selected_count=2)
    top2 = ranked[:2]

    assert len(top2) == 2
    total_weight = sum(float(item.get("capital_weight", 0.0) or 0.0) for item in top2)
    assert abs(total_weight - 1.0) < 1e-6
    assert top2[0]["capital_weight"] > top2[1]["capital_weight"]

