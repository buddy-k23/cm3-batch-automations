from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def execute_with_retries(fn: Callable[[], T], *, max_attempts: int = 3, base_delay_seconds: float = 0.25) -> T:
    """Execute callable with exponential backoff retries.

    Args:
        fn: Callable to execute.
        max_attempts: Maximum attempts before raising.
        base_delay_seconds: Initial delay for backoff.

    Returns:
        Function result.

    Raises:
        Exception: Last exception from callable after retries exhausted.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception:
            if attempt >= max_attempts:
                raise
            delay = base_delay_seconds * (2 ** (attempt - 1))
            time.sleep(delay)
