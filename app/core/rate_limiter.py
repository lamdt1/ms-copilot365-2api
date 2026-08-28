import time
import asyncio
from collections import defaultdict
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings

# Slide window configuration
class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self.requests = defaultdict(list)

    def allow_request(self, ip: str) -> tuple[bool, int]:
        now = time.time()

        # Periodic leak cleanup (randomly 1% of calls or if size > 1000)
        if len(self.requests) > 1000 or (int(now) % 100 == 0):
            stale_ips = [k for k, v in self.requests.items() if not v or now - v[-1] > self.window]
            for stale_ip in stale_ips:
                self.requests.pop(stale_ip, None)

        # Clean older requests
        self.requests[ip] = [t for t in self.requests[ip] if now - t < self.window]

        if len(self.requests[ip]) >= self.limit:
            if self.requests[ip]:
                retry_after = int(self.window - (now - self.requests[ip][0]))
            else:
                retry_after = int(self.window)
            return False, max(1, retry_after)

        self.requests[ip].append(now)
        return True, 0

# Limit sliding window RPM
limiter = SlidingWindowLimiter(limit=settings.RATE_LIMIT_RPM)

# WebSocket connection gate
websocket_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_WS)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Allow health checks
        if request.url.path == "/healthz":
            return await call_next(request)

        # Resolve client IP — prefer X-Forwarded-For for reverse proxy setups
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow_request(ip)

        if not allowed:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "message": "Rate limit exceeded. Too many requests.",
                        "type": "rate_limit_exceeded",
                        "code": 429,
                        "retry_after": retry_after
                    }
                },
                headers={"Retry-After": str(retry_after)}
            )

        return await call_next(request)
