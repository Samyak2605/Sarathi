"""Per-provider circuit breaker with half-open probing.

Why breakers and not just retries: retrying a provider that's actually
down burns the whole request's latency budget on doomed attempts. Failing
fast once a provider crosses its failure-rate threshold, then trying a
single half-open probe periodically, recovers automatically without
hammering a dead endpoint.

Uses a fixed-SIZE (count-based) sliding window, not a time-based one.
An earlier version kept every event from the last `window_seconds` --
under sustained traffic that meant hundreds of stale successes from
before an outage stayed in the window and diluted the failure ratio, so
the breaker never tripped during a 10s outage in
benchmarks/chaos/run_chaos_test.py even though every request was
failing. A count-based window (last N requests, period) doesn't have
that failure mode: N consecutive failures always trips it regardless of
how much traffic came before.
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_rate_threshold: float = 0.5,
        window_size: int = 10,
        open_seconds: float = 15.0,
    ):
        self.failure_rate_threshold = failure_rate_threshold
        self.window_size = window_size
        self.open_seconds = open_seconds

        self._events: deque[bool] = deque(maxlen=window_size)
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False

    @property
    def state(self) -> CircuitState:
        self._maybe_transition_to_half_open()
        return self._state

    def _maybe_transition_to_half_open(self) -> None:
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.time() - self._opened_at >= self.open_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_probe_in_flight = False

    def allow_request(self) -> bool:
        self._maybe_transition_to_half_open()
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_probe_in_flight:
                return False
            self._half_open_probe_in_flight = True
            return True
        return False  # OPEN, still cooling down

    def record_success(self) -> None:
        self._events.append(True)
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
            self._events.clear()
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        self._events.append(False)
        if self._state == CircuitState.HALF_OPEN:
            self._open(time.time())
            return
        if len(self._events) >= self.window_size:
            failures = sum(1 for ok in self._events if not ok)
            if failures / len(self._events) >= self.failure_rate_threshold:
                self._open(time.time())

    def _open(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = now
        self._half_open_probe_in_flight = False

    def snapshot(self) -> dict:
        return {
            "state": self.state.value,
            "events_in_window": len(self._events),
            "opened_at": self._opened_at,
        }
