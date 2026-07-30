"""Middleware for request ID tracking, duration logging, and rate limiting."""
from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from app.config import get_settings

logger = structlog.get_logger(__name__)

# Simple in-memory sliding window rate limiter per IP for rate-limited endpoints
_IP_REQUEST_TIMES: dict[str, list[float]] = {}


def clear_rate_limit_records() -> None:
    """Utility function to reset rate limit records (used in tests)."""
    _IP_REQUEST_TIMES.clear()


async def rate_limit_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    """Rate limit requests on /v1/agent/query per client IP."""
    if request.url.path.startswith("/v1/agent/query"):
        settings = get_settings()
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        window_start = now - 60.0

        timestamps = _IP_REQUEST_TIMES.get(client_ip, [])
        # Prune timestamps older than window
        timestamps = [t for t in timestamps if t > window_start]

        if len(timestamps) >= settings.RATE_LIMIT_PER_MINUTE:
            logger.warning("rate_limit.exceeded", client_ip=client_ip)
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": 429,
                        "message": f"Rate limit exceeded. Maximum {settings.RATE_LIMIT_PER_MINUTE} requests per minute.",
                    },
                    "meta": {"request_id": getattr(request.state, "request_id", None)},
                },
            )

        timestamps.append(now)
        _IP_REQUEST_TIMES[client_ip] = timestamps

    return await call_next(request)


async def request_id_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    """Extract or generate X-Request-ID and attach to structlog context and response headers."""
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = req_id

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=req_id)

    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


async def logging_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    """Log request method, path, response status, and duration in milliseconds."""
    t0 = time.perf_counter()
    response = await call_next(request)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    logger.info(
        "http.request.completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=latency_ms,
    )
    return response
