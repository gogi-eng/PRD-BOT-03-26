#!/usr/bin/env python3
from __future__ import annotations

from typing import List

from .genome import Genome


def generate_population(n: int = 20) -> List[Genome]:
    return [Genome() for _ in range(max(1, int(n)))]
