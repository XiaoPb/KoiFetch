import threading

from app.application.login_limiter import LoginLimiter


def test_limiter_blocks_sixth_failure_and_expires_with_monotonic_clock():
    now = [100.0]
    limiter = LoginLimiter(max_attempts=5, window_seconds=300, clock=lambda: now[0])

    for _ in range(5):
        assert limiter.check("127.0.0.1", " Admin ")
        limiter.record_failure("127.0.0.1", "admin")
    assert not limiter.check("127.0.0.1", "admin")
    assert limiter.retry_after("127.0.0.1", "admin") == 300

    now[0] = 401.0
    assert limiter.check("127.0.0.1", "ADMIN")


def test_limiter_clear_and_buckets_are_isolated():
    limiter = LoginLimiter(max_attempts=1, window_seconds=30, clock=lambda: 10.0)
    limiter.record_failure("127.0.0.1", "Admin")
    assert not limiter.check("127.0.0.1", " admin ")
    assert limiter.check("127.0.0.2", "admin")
    limiter.clear("127.0.0.1", "ADMIN")
    assert limiter.check("127.0.0.1", "admin")


def test_limiter_evicts_oldest_key_and_is_thread_safe():
    now = [0.0]
    limiter = LoginLimiter(max_attempts=2, window_seconds=100, max_keys=2, clock=lambda: now[0])
    limiter.record_failure("1.1.1.1", "a")
    now[0] = 1.0
    limiter.record_failure("2.2.2.2", "b")
    now[0] = 2.0
    limiter.record_failure("3.3.3.3", "c")
    assert limiter.key_count == 2
    assert limiter.check("1.1.1.1", "a")

    errors = []

    def worker():
        try:
            for _ in range(100):
                limiter.record_failure("4.4.4.4", "d")
                limiter.check("4.4.4.4", "d")
        except Exception as exc:  # pragma: no cover - assertion aid
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert limiter.key_count <= 2
