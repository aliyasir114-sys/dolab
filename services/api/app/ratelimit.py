"""Slows down password guessing.

In-memory, so it protects a single process only. Behind several workers or servers this
must move to a shared store (Redis or the database) before launch.
"""
import time
from collections import defaultdict, deque


class FailureLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 900) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, key: str) -> deque[float]:
        attempts = self._failures[key]
        cutoff = time.monotonic() - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._recent(key).append(time.monotonic())

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)

    def clear_prefix(self, prefix: str) -> None:
        for key in [k for k in self._failures if k.startswith(prefix)]:
            del self._failures[key]

    def reset(self) -> None:
        self._failures.clear()


class LoginLimiter:
    """Two limits on failed sign-ins (plan 4.7).

    - Per email and client address: 5 failures in 15 minutes block that address for that email.
    - Per email from anywhere: 20 failures in an hour block the email everywhere.
    Someone who only knows an email address therefore cannot lock its owner out with a few
    guesses from another address; it takes a sustained attack, which the email-wide limit stops.
    """

    def __init__(self) -> None:
        self.per_address = FailureLimiter(max_failures=5, window_seconds=900)
        self.per_email = FailureLimiter(max_failures=20, window_seconds=3600)

    def blocked(self, email: str, ip: str) -> int | None:
        """Seconds to wait if blocked, else None."""
        if self.per_address.blocked(f"{email}|{ip}"):
            return self.per_address.window_seconds
        if self.per_email.blocked(email):
            return self.per_email.window_seconds
        return None

    def record_failure(self, email: str, ip: str) -> None:
        self.per_address.record_failure(f"{email}|{ip}")
        self.per_email.record_failure(email)

    def clear_address(self, email: str, ip: str) -> None:
        """After a successful sign-in from this address. The email-wide count stays."""
        self.per_address.clear(f"{email}|{ip}")

    def clear_email(self, email: str) -> None:
        """After a password reset: everything for this email."""
        self.per_email.clear(email)
        self.per_address.clear_prefix(f"{email}|")

    def reset(self) -> None:
        self.per_address.reset()
        self.per_email.reset()


login_limiter = LoginLimiter()
# Reset emails per address: at most 3 an hour, so the endpoint cannot be used to flood an inbox.
reset_limiter = FailureLimiter(max_failures=3, window_seconds=3600)
