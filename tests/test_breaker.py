from __future__ import annotations

import time

from gateway.providers.breaker import CircuitBreaker, CircuitState


def test_breaker_opens_after_failure_threshold():
    breaker = CircuitBreaker(failure_rate_threshold=0.5, window_size=4, open_seconds=1)
    assert breaker.state == CircuitState.CLOSED
    for _ in range(4):
        assert breaker.allow_request()
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert not breaker.allow_request()


def test_breaker_ignores_stale_successes_outside_the_window():
    # Regression test: an earlier time-window implementation let a long
    # run of prior successes dilute the failure ratio, so N consecutive
    # failures right now could fail to trip it. A count-based window must
    # trip on N consecutive failures regardless of history before them.
    breaker = CircuitBreaker(failure_rate_threshold=0.5, window_size=4, open_seconds=1)
    for _ in range(50):
        breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    for _ in range(4):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_breaker_recovers_via_half_open_probe():
    breaker = CircuitBreaker(failure_rate_threshold=0.5, window_size=2, open_seconds=0.05)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    time.sleep(0.06)
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.allow_request()  # the single probe
    assert not breaker.allow_request()  # no second concurrent probe

    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request()


def test_breaker_reopens_if_half_open_probe_fails():
    breaker = CircuitBreaker(failure_rate_threshold=0.5, window_size=2, open_seconds=0.05)
    breaker.record_failure()
    breaker.record_failure()
    time.sleep(0.06)
    assert breaker.state == CircuitState.HALF_OPEN
    breaker.allow_request()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_low_failure_rate_does_not_open_breaker():
    breaker = CircuitBreaker(failure_rate_threshold=0.5, window_size=4, open_seconds=1)
    breaker.record_success()
    breaker.record_success()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED
