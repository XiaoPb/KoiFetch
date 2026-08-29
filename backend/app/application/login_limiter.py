"""Small, process-local login attempt limiter."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable

__all__ = ["LoginLimiter", "normalize_username"]


def normalize_username(username: str) -> str:
    """Return the canonical username used for authentication throttling."""
    return username.strip().casefold()


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    last_used: float = 0.0


class LoginLimiter:
    """Bounded, thread-safe failed-login limiter keyed by IP and username."""

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: float = 300,
        max_keys: int = 10_000,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_attempts < 1 or window_seconds <= 0 or max_keys < 1:
            raise ValueError("limiter bounds must be positive")
        self.max_attempts = max_attempts
        self.window_seconds = float(window_seconds)
        self.max_keys = max_keys
        self._clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = RLock()

    @property
    def key_count(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._buckets)

    def check(self, client_ip: str, username: str) -> bool:
        """Return whether an authentication attempt may proceed."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(self._key(client_ip, username))
            if bucket is None:
                return True
            bucket.last_used = now
            return len(bucket.failures) < self.max_attempts

    def record_failure(self, client_ip: str, username: str) -> None:
        """Record one failed authentication attempt."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            key = self._key(client_ip, username)
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_keys:
                    victim = min(
                        self._buckets,
                        key=lambda item: (
                            self._buckets[item].last_used,
                            item[0],
                            item[1],
                        ),
                    )
                    del self._buckets[victim]
                bucket = _Bucket(last_used=now)
                self._buckets[key] = bucket
            bucket.failures.append(now)
            bucket.last_used = now

    def clear(self, client_ip: str, username: str) -> None:
        """Clear failed attempts for exactly one canonical bucket."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._buckets.pop(self._key(client_ip, username), None)

    def retry_after(self, client_ip: str, username: str) -> int:
        """Return integer seconds until the oldest failure leaves the window."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(self._key(client_ip, username))
            if bucket is None or not bucket.failures:
                return 0
            return max(1, math.ceil(self.window_seconds - (now - bucket.failures[0])))

    def _key(self, client_ip: str, username: str) -> tuple[str, str]:
        return (str(client_ip).strip(), normalize_username(username))

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, bucket in list(self._buckets.items()):
            while bucket.failures and bucket.failures[0] <= cutoff:
                bucket.failures.popleft()
            if not bucket.failures:
                del self._buckets[key]
