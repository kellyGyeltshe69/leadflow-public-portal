from __future__ import annotations

import hashlib
import time
import uuid
from collections import defaultdict, deque
from contextvars import ContextVar

from fastapi import Request
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import get_settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
REQUESTS = Counter("leadflow_http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("leadflow_http_request_seconds", "HTTP request latency", ["method", "path"])


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")[:64] or uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = request_id
        path = request.url.path
        metric_path = path
        for prefix, template in (
            ("/r/", "/r/{token}"),
            ("/go/", "/go/{token}"),
            ("/u/", "/u/{token}"),
            ("/unsubscribe/", "/unsubscribe/{token}"),
        ):
            if path.startswith(prefix):
                metric_path = template
                break
        if not path.startswith(("/static/", "/email-assets/")):
            REQUESTS.labels(request.method, metric_path, response.status_code).inc()
            LATENCY.labels(request.method, metric_path).observe(time.perf_counter() - started)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Small local limiter with Redis support delegated to production workers.

    It protects authentication and JSON API routes. Public audit landing pages
    receive the general limit. Proxy/CDN deployments must preserve a trusted
    client IP header at the edge; this middleware deliberately ignores
    X-Forwarded-For to avoid spoofing when run directly.
    """

    def __init__(self, app):
        super().__init__(app)
        self.buckets: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        path = request.url.path
        if not (
            path.startswith("/api/")
            or path.startswith("/r/")
            or path.startswith("/go/")
            or path.startswith("/u/")
            or path.startswith("/unsubscribe/")
        ):
            return await call_next(request)
        limit = settings.auth_rate_limit_per_minute if path.startswith("/api/v1/auth/") else settings.api_rate_limit_per_minute
        client = request.client.host if request.client else "unknown"
        key = hashlib.sha256(f"{client}:{path.split('?')[0]}".encode()).hexdigest()
        if settings.redis_url:
            try:
                from redis.asyncio import Redis

                redis = Redis.from_url(settings.redis_url, socket_timeout=2)
                bucket_key = f"leadflow:rate:{key}:{int(time.time() // 60)}"
                count = await redis.incr(bucket_key)
                if count == 1:
                    await redis.expire(bucket_key, 70)
                await redis.aclose()
                if count > limit:
                    return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
                return await call_next(request)
            except Exception:
                # Availability is preferable to a global outage if Redis is
                # briefly unavailable; the in-process limiter remains active.
                pass
        now = time.monotonic()
        bucket = self.buckets[key]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        bucket.append(now)
        if len(self.buckets) > 10_000:
            self.buckets = defaultdict(deque, {k: v for k, v in self.buckets.items() if v and v[-1] > now - 60})
        return await call_next(request)
