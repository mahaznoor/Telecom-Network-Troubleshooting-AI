"""Bounded process-local rate limiting for verification email delivery."""

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from typing import Callable, Deque, Optional

from backend.config import settings


class VerificationEmailRateLimiter:
    """Limit account email requests and sensitive token submissions."""

    def __init__(
        self,
        ip_max_per_hour: int = 10,
        address_max_per_hour: int = 3,
        window_seconds: int = 3600,
        max_tracked_keys: int = 10000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if min(
            ip_max_per_hour,
            address_max_per_hour,
            window_seconds,
            max_tracked_keys,
        ) < 1:
            raise ValueError("Verification limiter values must be positive.")
        self.ip_max_per_hour = ip_max_per_hour
        self.address_max_per_hour = address_max_per_hour
        self.window_seconds = window_seconds
        self.max_tracked_keys = max_tracked_keys
        self._clock = clock
        self._key_salt = secrets.token_bytes(32)
        self._ip_attempts: OrderedDict[bytes, Deque[float]] = OrderedDict()
        self._address_attempts: OrderedDict[bytes, Deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, ip_address: str, email: str) -> bool:
        """Record an attempt and indicate whether an email may be sent."""
        now = self._clock()
        ip_key = self._key("ip", ip_address)
        address_key = self._key("address", email)
        with self._lock:
            ip_attempts = self._get_attempts(self._ip_attempts, ip_key, now)
            address_attempts = self._get_attempts(
                self._address_attempts,
                address_key,
                now,
            )
            if (
                len(ip_attempts) >= self.ip_max_per_hour
                or len(address_attempts) >= self.address_max_per_hour
            ):
                return False
            ip_attempts.append(now)
            address_attempts.append(now)
            return True

    def _key(self, namespace: str, value: str) -> bytes:
        payload = f"{namespace}:{value}".encode("utf-8", errors="replace")
        return hmac.new(self._key_salt, payload, hashlib.sha256).digest()

    def _get_attempts(
        self,
        buckets: OrderedDict[bytes, Deque[float]],
        key: bytes,
        now: float,
    ) -> Deque[float]:
        attempts = buckets.get(key)
        if attempts is None:
            if len(buckets) >= self.max_tracked_keys:
                buckets.popitem(last=False)
            attempts = deque()
            buckets[key] = attempts
        else:
            buckets.move_to_end(key)

        cutoff = now - self.window_seconds
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return attempts


_verification_email_limiter: Optional[VerificationEmailRateLimiter] = None


def get_verification_email_limiter() -> VerificationEmailRateLimiter:
    """Return the process-wide verification-email limiter."""
    global _verification_email_limiter
    if _verification_email_limiter is None:
        _verification_email_limiter = VerificationEmailRateLimiter(
            ip_max_per_hour=settings.VERIFICATION_EMAIL_IP_MAX_PER_HOUR,
            address_max_per_hour=settings.VERIFICATION_EMAIL_ADDRESS_MAX_PER_HOUR,
        )
    return _verification_email_limiter
