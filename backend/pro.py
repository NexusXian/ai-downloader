"""Pro entitlement and rate limiting, without a database.

What is genuinely enforced server-side: a redeem code is validated against a
server-side list and exchanged for an HMAC-signed, expiring token. The client
cannot forge or extend one, and every Pro request re-verifies the signature.

What this is NOT: identity. Tokens are bearer credentials, so a user can share one,
and free-tier limits keyed on IP reset when the IP changes. Real accounts,
revocation and per-seat billing all need persistent storage. This is an honest
demonstration of a paid tier, not a billing system.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import config

_serializer = URLSafeTimedSerializer(config.PRO_SECRET, salt="video-downloader-pro")


class RedeemError(ValueError):
    """Invalid redeem code."""


def redeem(code: str) -> dict[str, object]:
    """Exchange a redeem code for a signed Pro token."""
    normalized = (code or "").strip().upper()
    if not normalized:
        raise RedeemError("请输入兑换码")
    if normalized not in config.REDEEM_CODES:
        raise RedeemError("兑换码无效或已被使用")

    token = _serializer.dumps({"code": normalized, "tier": "pro"})
    return {
        "token": token,
        "tier": "pro",
        "expires_in": config.PRO_TOKEN_TTL_SECONDS,
        "expires_at": time.time() + config.PRO_TOKEN_TTL_SECONDS,
    }


def verify(token: str | None) -> bool:
    """True if the token is a valid, unexpired, untampered Pro token."""
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=config.PRO_TOKEN_TTL_SECONDS)
    except (SignatureExpired, BadSignature):
        return False
    return isinstance(data, dict) and data.get("tier") == "pro"


def limits_for(is_pro: bool) -> dict[str, int]:
    return {
        "max_height": config.PRO_MAX_HEIGHT if is_pro else config.FREE_MAX_HEIGHT,
        "batch_limit": config.PRO_BATCH_LIMIT if is_pro else config.FREE_BATCH_LIMIT,
    }


def clamp_height(requested: int | None, is_pro: bool) -> int:
    """Clamp a requested resolution to the caller's tier.

    Server-side because a client-side check is only a suggestion.
    """
    ceiling = limits_for(is_pro)["max_height"]
    if not requested or requested <= 0:
        return ceiling
    return min(requested, ceiling)


class RateLimiter:
    """Fixed-window per-IP limiter.

    Hand-rolled rather than pulling in slowapi: this is ~20 lines, has no extra
    dependency, and each job costs real bandwidth/CPU/disk, so the limit is
    load-bearing rather than decorative.
    """

    def __init__(self, max_events: int, window_seconds: int) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.time()
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < now - self.window:
                bucket.popleft()
            if len(bucket) >= self.max_events:
                return False, int(bucket[0] + self.window - now) + 1
            bucket.append(now)

            # Opportunistic prune so idle keys don't accumulate forever.
            if len(self._hits) > 4096:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True, 0


limiter = RateLimiter(config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW_SECONDS)
