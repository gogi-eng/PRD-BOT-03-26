"""Self-evolving strategy population with sandbox + allocator guardrails."""
from __future__ import annotations

from .allocator import Allocator
from .backtester import Backtester
from .generator import generate_population
from .genome import Genome
from .mutator import mutate
from .orchestrator import EvolutionEngine
from .sandbox import Sandbox
from .selector import reproduce, select

__all__ = [
    "Allocator",
    "Backtester",
    "EvolutionEngine",
    "Genome",
    "Sandbox",
    "generate_population",
    "mutate",
    "reproduce",
    "select",
]
