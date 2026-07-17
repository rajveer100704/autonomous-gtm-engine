"""
utils/retry.py — Typed retry decorator with exponential backoff + jitter.

Reviewer spec:
  Retryable:     Timeout, 429, 500, 502, 503, 504
  Non-retryable: 400, 401, 403, 404, 422

Usage:
    @retry(max_attempts=3, retryable_status={429, 500, 502, 503, 504})
    def call_api() -> dict:
        ...

    @retry_async(max_attempts=3)
    async def async_call() -> dict:
        ...
"""
from __future__ import annotations

import asyncio
import functools
import logging
import math
import random
import time
from typing import Callable, Iterable, Type

import requests

logger = logging.getLogger(__name__)

# ── Status codes ──────────────────────────────────────────────────────────
RETRYABLE_STATUS:    frozenset[int] = frozenset({429, 500, 502, 503, 504})
NON_RETRYABLE_STATUS: frozenset[int] = frozenset({400, 401, 403, 404, 422})

# ── Exceptions considered always retryable ────────────────────────────────
RETRYABLE_EXCEPTIONS: tuple[Type[Exception], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    TimeoutError,
    ConnectionResetError,
    ConnectionAbortedError,
)


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    # requests.HTTPError: inspect status code
    if isinstance(exc, requests.exceptions.HTTPError):
        try:
            code = exc.response.status_code
            if code in NON_RETRYABLE_STATUS:
                return False
            return code in RETRYABLE_STATUS
        except AttributeError:
            return True  # no response object → treat as retryable
    return False


def _backoff_seconds(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    """Full jitter exponential backoff: uniform(0, min(cap, base * 2^attempt))."""
    ceiling = min(cap, base * math.pow(2, attempt))
    return random.uniform(0, ceiling)


def retry(
    max_attempts: int = 3,
    base_backoff: float = 1.0,
    cap_backoff: float = 60.0,
    retryable_status: Iterable[int] = RETRYABLE_STATUS,
    retryable_exc: tuple[Type[Exception], ...] = RETRYABLE_EXCEPTIONS,
) -> Callable:
    """
    Synchronous retry decorator.

    Example::

        @retry(max_attempts=3)
        def fetch_leads() -> list:
            ...
    """
    retryable_set = frozenset(retryable_status)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    # Check if retryable
                    retryable = False
                    if isinstance(exc, retryable_exc):
                        retryable = True
                    elif isinstance(exc, requests.exceptions.HTTPError):
                        try:
                            code = exc.response.status_code
                            retryable = code in retryable_set and code not in NON_RETRYABLE_STATUS
                        except AttributeError:
                            retryable = True

                    if not retryable:
                        logger.debug(
                            "retry: non-retryable error on %s attempt %d/%d: %s",
                            fn.__name__, attempt + 1, max_attempts, exc,
                        )
                        raise

                    last_exc = exc
                    if attempt + 1 < max_attempts:
                        wait = _backoff_seconds(attempt, base_backoff, cap_backoff)
                        logger.warning(
                            "retry: %s failed (attempt %d/%d), retrying in %.1fs: %s",
                            fn.__name__, attempt + 1, max_attempts, wait, exc,
                        )
                        time.sleep(wait)

            logger.error(
                "retry: %s exhausted %d attempts. Last error: %s",
                fn.__name__, max_attempts, last_exc,
            )
            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator


def retry_async(
    max_attempts: int = 3,
    base_backoff: float = 1.0,
    cap_backoff: float = 60.0,
    retryable_status: Iterable[int] = RETRYABLE_STATUS,
    retryable_exc: tuple[Type[Exception], ...] = RETRYABLE_EXCEPTIONS,
) -> Callable:
    """
    Async retry decorator — mirrors retry() for async functions.

    Example::

        @retry_async(max_attempts=3)
        async def fetch_page(url: str) -> str:
            ...
    """
    retryable_set = frozenset(retryable_status)

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    retryable = False
                    if isinstance(exc, retryable_exc):
                        retryable = True
                    elif isinstance(exc, requests.exceptions.HTTPError):
                        try:
                            code = exc.response.status_code
                            retryable = code in retryable_set and code not in NON_RETRYABLE_STATUS
                        except AttributeError:
                            retryable = True

                    if not retryable:
                        raise

                    last_exc = exc
                    if attempt + 1 < max_attempts:
                        wait = _backoff_seconds(attempt, base_backoff, cap_backoff)
                        logger.warning(
                            "retry_async: %s failed (attempt %d/%d), retrying in %.1fs: %s",
                            fn.__name__, attempt + 1, max_attempts, wait, exc,
                        )
                        await asyncio.sleep(wait)

            raise last_exc  # type: ignore[misc]

        return wrapper
    return decorator
