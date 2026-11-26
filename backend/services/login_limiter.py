"""Process-local IP throttling and temporary account lockout for login."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from math import ceil
from typing import Callable, Deque, Dict, Optional


class LoginRateLimitExceeded(Exception):
    """Raised when an IP or account is temporarily blocked from login."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__("Login temporarily unavailable.")


class LoginRateLimiter:
    """Bounded, process-local sliding-window login failure tracker."""

    def __init__(
        self,
        ip_max_failures: int = 20,
        account_max_failures: int = 5,
        failure_window_seconds: int = 900,
        account_lockout_seconds: int = 900,
        max_tracked_keys: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(
            ip_max_failures,
            account_max_failures,
            failure_window_seconds,
            account_lockout_seconds,
            max_tracked_keys,
        ) < 1:
            raise ValueError("Login limiter values must be positive.")
        self.ip_max_failures = ip_max_failures
        self.account_max_failures = account_max_failures
        self.failure_window_seconds = failure_window_seconds
        self.account_lockout_seconds = account_lockout_seconds
        self.max_tracked_keys = max_tracked_keys
        self._clock = clock
        self._key_salt = secrets.token_bytes(32)
        self._ip_failures: OrderedDict[bytes, Deque[float]] = OrderedDict()
        self._account_failures: OrderedDict[bytes, Deque[float]] = OrderedDict()
        self._account_lockouts: Dict[bytes, float] = {}
        self._lock = threading.Lock()

    def check(self, ip_address: str, account_identifier: str) -> None:
        """Raise a generic throttle error if either limit is currently active."""
        now = self._clock()
        ip_key = self._key("ip", ip_address)
        account_key = self._key("account", account_identifier)
        with self._lock:
            lockout_until = self._account_lockouts.get(account_key)
            if lockout_until is not None:
                if now < lockout_until:
                    raise LoginRateLimitExceeded(ceil(lockout_until - now))
                self._account_lockouts.pop(account_key, None)
                self._account_failures.pop(account_key, None)

            ip_attempts = self._attempts(self._ip_failures, ip_key, now)
            if len(ip_attempts) >= self.ip_max_failures:
                retry_after = self.failure_window_seconds - (now - ip_attempts[0])
                raise LoginRateLimitExceeded(ceil(retry_after))

    def record_failure(self, ip_address: str, account_identifier: str) -> None:
        """Record a failed authentication attempt for both rate-limit scopes."""
        now = self._clock()
        ip_key = self._key("ip", ip_address)
        account_key = self._key("account", account_identifier)
        with self._lock:
            ip_attempts = self._attempts(self._ip_failures, ip_key, now)
            ip_attempts.append(now)

            account_attempts = self._attempts(
                self._account_failures, account_key, now, account=True
            )
            account_attempts.append(now)
            if len(account_attempts) >= self.account_max_failures:
                self._account_lockouts[account_key] = (
                    now + self.account_lockout_seconds
                )

    def record_success(self, account_identifier: str) -> None:
        """Clear failures and lockout for the successfully authenticated account."""
        account_key = self._key("account", account_identifier)
        with self._lock:
            self._account_failures.pop(account_key, None)
            self._account_lockouts.pop(account_key, None)

    def _key(self, namespace: str, value: str) -> bytes:
        message = f"{namespace}:{value}".encode("utf-8", errors="replace")
        return hmac.new(self._key_salt, message, hashlib.sha256).digest()

    def _attempts(
        self,
        buckets: OrderedDict[bytes, Deque[float]],
        key: bytes,
        now: float,
        account: bool = False,
    ) -> Deque[float]:
        attempts = buckets.get(key)
        if attempts is None:
            if len(buckets) >= self.max_tracked_keys:
                evicted_key, _ = buckets.popitem(last=False)
                if account:
                    self._account_lockouts.pop(evicted_key, None)
            attempts = deque()
            buckets[key] = attempts
        else:
            buckets.move_to_end(key)

        cutoff = now - self.failure_window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts


_login_rate_limiter: Optional[LoginRateLimiter] = None


def get_login_rate_limiter() -> LoginRateLimiter:
    """Return the process-wide limiter configured from application settings."""
    global _login_rate_limiter
    if _login_rate_limiter is None:
        from backend.config import settings

        _login_rate_limiter = LoginRateLimiter(
            ip_max_failures=settings.LOGIN_IP_MAX_FAILURES,
            account_max_failures=settings.LOGIN_ACCOUNT_MAX_FAILURES,
            failure_window_seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS,
            account_lockout_seconds=settings.LOGIN_ACCOUNT_LOCKOUT_SECONDS,
        )
    return _login_rate_limiter
