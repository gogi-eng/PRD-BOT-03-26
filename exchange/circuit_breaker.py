"""Circuit breaker для Bybit REST (429 / 5xx)."""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ApiCircuitBreaker:
    failure_threshold: int = 5
    open_sec: float = 45.0
    _failures: int = 0
    _opened_at: float = 0.0

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = 0.0

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    def is_open(self) -> bool:
        if self._opened_at <= 0:
            return False
        if time.monotonic() - self._opened_at >= self.open_sec:
            self._opened_at = 0.0
            self._failures = 0
            return False
        return True

    def seconds_until_retry(self) -> float:
        if self._opened_at <= 0:
            return 0.0
        return max(0.0, self.open_sec - (time.monotonic() - self._opened_at))
