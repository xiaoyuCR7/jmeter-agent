"""Token-bucket rate limiter middleware for FastAPI.

Lightweight, no external dependency. Uses in-memory token buckets
per route pattern. Supports global + per-route rate limits.

Usage:
    from app.middleware.rate_limiter import create_rate_limiter
    limiter = create_rate_limiter()
    app.add_middleware(limiter)
"""

import time
import asyncio
import logging
from collections import defaultdict
from typing import Optional, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)


class TokenBucket:
    """Token bucket rate limiter for a single route/client."""

    def __init__(self, rate: str):
        """
        Args:
            rate: "N/period" string, e.g. "30/minute" or "200/hour".
        """
        self.max_tokens, self.period_seconds = self._parse_rate(rate)
        self.tokens = float(self.max_tokens)
        self.last_refill = time.monotonic()

    @staticmethod
    def _parse_rate(rate: str) -> tuple[int, float]:
        try:
            count_str, period = rate.split("/")
            count = int(count_str)
            seconds = {
                "second": 1, "minute": 60, "hour": 3600, "day": 86400,
            }.get(period, 60)
            return count, float(seconds)
        except Exception:
            return 60, 60.0  # default: 60/minute

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * (self.max_tokens / self.period_seconds))
        self.last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False

    @property
    def remaining(self) -> float:
        self._refill()
        return self.tokens


class RateLimiter:
    """Manages multiple token buckets keyed by client IP + route pattern."""

    def __init__(self):
        self._buckets: dict[str, TokenBucket] = {}
        self._global_bucket: Optional[TokenBucket] = None
        self._route_limits: dict[str, str] = {}
        self._lock = asyncio.Lock()

        # Default per-route limits
        self._route_limits = {
            "/api/v1/auth/login": settings.RATE_LIMIT_AUTH,
            "/api/v1/agent": settings.RATE_LIMIT_AI,
            "/api/v1/mcp": settings.RATE_LIMIT_MCP,
        }
        if settings.RATE_LIMIT_GLOBAL:
            self._global_bucket = TokenBucket(settings.RATE_LIMIT_GLOBAL)

    def _get_bucket_key(self, request: Request) -> str:
        client_ip = request.client.host if request.client else "unknown"
        path = request.url.path
        # Match specific routes first, then fall back to prefix match
        for pattern in sorted(self._route_limits.keys(), key=len, reverse=True):
            if path.startswith(pattern):
                return f"{client_ip}:{pattern}"
        return f"{client_ip}:global"

    def _get_limit_for_key(self, key: str) -> Optional[str]:
        for pattern, limit in self._route_limits.items():
            if key.endswith(pattern):
                return limit
        return settings.RATE_LIMIT_GLOBAL

    async def is_allowed(self, request: Request) -> tuple[bool, dict]:
        """Check if request is within rate limits. Returns (allowed, headers)."""
        if not settings.RATE_LIMIT_ENABLED:
            return True, {}

        async with self._lock:
            # Global bucket check first
            if self._global_bucket:
                if not self._global_bucket.consume():
                    return False, {
                        "X-RateLimit-Limit": str(self._global_bucket.max_tokens),
                        "X-RateLimit-Remaining": "0",
                        "Retry-After": str(int(self._global_bucket.period_seconds)),
                    }

            # Per-route bucket check
            key = self._get_bucket_key(request)
            limit_str = self._get_limit_for_key(key) or "60/minute"

            if key not in self._buckets:
                self._buckets[key] = TokenBucket(limit_str)

            bucket = self._buckets[key]
            allowed = bucket.consume()

            # Clean up stale buckets (keep under 10k entries)
            if len(self._buckets) > 10000:
                stale_keys = sorted(self._buckets.keys())[:1000]
                for k in stale_keys:
                    del self._buckets[k]

            headers = {
                "X-RateLimit-Limit": str(bucket.max_tokens),
                "X-RateLimit-Remaining": str(int(bucket.remaining)),
            }
            if not allowed:
                headers["Retry-After"] = str(int(bucket.period_seconds))

            return allowed, headers

    def get_stats(self) -> dict:
        """Return rate limiter statistics."""
        return {
            "active_buckets": len(self._buckets),
            "global_remaining": int(self._global_bucket.remaining) if self._global_bucket else -1,
            "route_limits": dict(self._route_limits),
        }


# ── FastAPI Middleware ──────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware for rate limiting."""

    def __init__(self, app, limiter: Optional[RateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for WebSocket upgrade, static files, health
        if request.url.path in ("/health", "/vite.svg") or request.url.path.startswith("/reports/") or request.url.path.startswith("/assets/"):
            return await call_next(request)
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        allowed, headers = await self.limiter.is_allowed(request)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please slow down."},
                headers=headers,
            )

        response = await call_next(request)
        for key, value in headers.items():
            response.headers[key] = value
        return response


# Singleton
_rate_limiter: Optional[RateLimiter] = None


def create_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
