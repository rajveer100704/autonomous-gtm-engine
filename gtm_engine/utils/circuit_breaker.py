"""
utils/circuit_breaker.py — Per-service circuit breaker.

States:
  CLOSED    Normal. Requests pass through. Failure counter increments on error.
  OPEN      Service is down. All calls fail fast. Timer starts.
  HALF_OPEN Recovery probe. One request allowed. If it succeeds → CLOSED. If it fails → OPEN.

Reviewer spec:
  Gmail down → failures → circuit OPEN → stop sending → wait → half-open → recover.

Usage:
    cb = CircuitBreaker(service="gmail", failure_threshold=5, recovery_timeout=60)

    try:
        with cb:
            send_email(...)
    except CircuitOpenError:
        # Fast fail — don't call the service
        ...

    # Decorator form:
    @cb.guard
    def send_email(...):
        ...
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED    = "closed"     # healthy
    OPEN      = "open"       # failing fast
    HALF_OPEN = "half_open"  # recovery probe


class CircuitOpenError(RuntimeError):
    """Raised when a circuit is OPEN and the call is rejected."""
    def __init__(self, service: str, resets_in: float):
        self.service   = service
        self.resets_in = resets_in
        super().__init__(
            f"Circuit for '{service}' is OPEN — "
            f"requests blocked for {resets_in:.0f}s more"
        )


# ── Global registry (singleton per service) ───────────────────────────────
_registry: dict[str, "CircuitBreaker"] = {}
_registry_lock = threading.Lock()


def get_circuit(
    service: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
    half_open_calls: int = 1,
) -> "CircuitBreaker":
    """Return the singleton CircuitBreaker for `service`, creating if needed."""
    with _registry_lock:
        if service not in _registry:
            _registry[service] = CircuitBreaker(
                service=service,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                half_open_calls=half_open_calls,
            )
        return _registry[service]


@dataclass
class CircuitBreaker:
    """Thread-safe circuit breaker."""

    service:           str
    failure_threshold: int   = 5     # failures before OPEN
    recovery_timeout:  float = 60.0  # seconds in OPEN before HALF_OPEN
    half_open_calls:   int   = 1     # probe calls allowed in HALF_OPEN

    # internal
    _state:           CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count:   int          = field(default=0,    init=False)
    _success_count:   int          = field(default=0,    init=False)  # in half_open
    _opened_at:       float        = field(default=0.0,  init=False)
    _half_open_sent:  int          = field(default=0,    init=False)
    _lock:            threading.Lock = field(default_factory=threading.Lock, init=False)

    # ── Public API ─────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._evaluate_state()

    @property
    def status(self) -> dict:
        with self._lock:
            state = self._evaluate_state()
            resets_in = max(0, self.recovery_timeout - (time.monotonic() - self._opened_at)) \
                        if state == CircuitState.OPEN else 0
            return {
                "service":         self.service,
                "state":           state.value,
                "failure_count":   self._failure_count,
                "failure_threshold": self.failure_threshold,
                "resets_in_s":     round(resets_in, 1),
            }

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through."""
        with self._lock:
            state = self._evaluate_state()
            if state == CircuitState.CLOSED:
                return True
            if state == CircuitState.OPEN:
                return False
            # HALF_OPEN — allow up to half_open_calls probes
            if self._half_open_sent < self.half_open_calls:
                self._half_open_sent += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            state = self._evaluate_state()
            if state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.half_open_calls:
                    self._reset()
                    logger.info("circuit_breaker: '%s' recovered → CLOSED", self.service)
            elif state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            state = self._evaluate_state()
            if state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._trip()

    def reset(self) -> None:
        """Manually reset the circuit (e.g., after maintenance window)."""
        with self._lock:
            self._reset()

    # ── Context manager ────────────────────────────────────────────────────

    def __enter__(self) -> "CircuitBreaker":
        if not self.allow_request():
            state_info = self.status
            raise CircuitOpenError(
                self.service,
                state_info["resets_in_s"],
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self.record_success()
        elif exc_type is not CircuitOpenError:
            self.record_failure()
        return False  # don't suppress exceptions

    # ── Decorator ──────────────────────────────────────────────────────────

    def guard(self, fn: Callable) -> Callable:
        """Decorator form: @cb.guard"""
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with self:
                return fn(*args, **kwargs)

        return wrapper

    # ── Internal ───────────────────────────────────────────────────────────

    def _evaluate_state(self) -> CircuitState:
        """Must be called with _lock held."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_sent = 0
                self._success_count  = 0
                logger.info(
                    "circuit_breaker: '%s' → HALF_OPEN (elapsed %.0fs)",
                    self.service, elapsed,
                )
        return self._state

    def _trip(self) -> None:
        """Trip the circuit → OPEN. Must be called with _lock held."""
        self._state     = CircuitState.OPEN
        self._opened_at = time.monotonic()
        logger.warning(
            "circuit_breaker: '%s' tripped → OPEN after %d failures",
            self.service, self._failure_count,
        )

    def _reset(self) -> None:
        """Reset to CLOSED. Must be called with _lock held."""
        self._state          = CircuitState.CLOSED
        self._failure_count  = 0
        self._success_count  = 0
        self._half_open_sent = 0
        self._opened_at      = 0.0
