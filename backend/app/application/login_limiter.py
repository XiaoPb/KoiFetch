"""Small, process-local login attempt limiter."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Callable

__all__ = ["LoginAttempt", "LoginLimiter", "normalize_username"]


def normalize_username(username: str) -> str:
    """Return the canonical username used for authentication throttling."""
    return username.strip().casefold()


@dataclass
class _Bucket:
    failures: deque[float] = field(default_factory=deque)
    reservations: dict[int, float] = field(default_factory=dict)
    last_used: float = 0.0
    generation: int = 0


@dataclass(frozen=True)
class LoginAttempt:
    """Opaque reservation ticket returned by :meth:`LoginLimiter.begin_attempt`."""

    key: tuple[str, str]
    generation: int
    token: int


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
        self._next_token = 0
        self._next_generation = 0

    @property
    def key_count(self) -> int:
        with self._lock:
            self._prune(self._clock())
            return len(self._buckets)

    def check(self, client_ip: str, username: str) -> bool:
        """Return whether an attempt may proceed without reserving a slot.

        Kept as a read-only compatibility helper. Callers that will perform
        authentication must use :meth:`begin_attempt` so concurrent requests
        cannot all pass a check before recording their failures.
        """
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(self._key(client_ip, username))
            if bucket is None:
                return True
            bucket.last_used = now
            return (
                len(bucket.failures) + len(bucket.reservations)
                < self.max_attempts
            )

    def begin_attempt(self, client_ip: str, username: str) -> LoginAttempt | None:
        """Atomically reserve one authentication slot, or return ``None``.

        The reservation is intentionally short-lived: bcrypt runs after this
        method returns and does not hold the limiter lock. The caller must
        always finalize the returned ticket. An unexpected authentication
        exception should finalize with ``success=False, error=True``; that
        conservative policy counts the attempt as a failure rather than
        allowing errors to bypass throttling.
        """
        with self._lock:
            now = self._clock()
            self._prune(now)
            key = self._key(client_ip, username)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._create_bucket(key, now)
            if len(bucket.failures) + len(bucket.reservations) >= self.max_attempts:
                bucket.last_used = now
                return None
            self._next_token += 1
            ticket = LoginAttempt(key, bucket.generation, self._next_token)
            bucket.reservations[ticket.token] = now
            bucket.last_used = now
            return ticket

    def finalize(
        self,
        ticket: LoginAttempt,
        *,
        success: bool,
        error: bool = False,
    ) -> None:
        """Complete a reservation and count failures without stale-ticket races.

        A successful completion clears the entire bucket, invalidating all
        reservations in that generation. A failed or exceptional completion
        consumes one failure slot. ``error`` documents the conservative
        exception path and is accepted for call-site clarity.
        """
        del error  # The conservative failure policy treats both paths alike.
        with self._lock:
            now = self._clock()
            self._prune(now)
            bucket = self._buckets.get(ticket.key)
            if bucket is None or bucket.generation != ticket.generation:
                return
            if ticket.token not in bucket.reservations:
                return
            del bucket.reservations[ticket.token]
            bucket.last_used = now
            if success:
                del self._buckets[ticket.key]
                return
            bucket.failures.append(now)

    def record_failure(self, client_ip: str, username: str) -> None:
        """Record one failed authentication attempt."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            key = self._key(client_ip, username)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = self._create_bucket(key, now)
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
            if bucket is None or (not bucket.failures and not bucket.reservations):
                return 0
            starts = list(bucket.failures) + list(bucket.reservations.values())
            return max(1, math.ceil(self.window_seconds - (now - min(starts))))

    def _key(self, client_ip: str, username: str) -> tuple[str, str]:
        return (str(client_ip).strip(), normalize_username(username))

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, bucket in list(self._buckets.items()):
            while bucket.failures and bucket.failures[0] <= cutoff:
                bucket.failures.popleft()
            for token, started in list(bucket.reservations.items()):
                if started <= cutoff:
                    del bucket.reservations[token]
            if not bucket.failures and not bucket.reservations:
                del self._buckets[key]

    def _create_bucket(self, key: tuple[str, str], now: float) -> _Bucket:
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
        self._next_generation += 1
        bucket = _Bucket(last_used=now, generation=self._next_generation)
        self._buckets[key] = bucket
        return bucket
