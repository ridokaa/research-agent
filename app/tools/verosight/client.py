"""Verosight REST client with authentication, envelope parsing, and ErrorReport handling."""
from __future__ import annotations

import time
from typing import Any, NamedTuple

import httpx
import structlog

from app.config import get_settings
from app.schemas.errors import ErrorReport

log = structlog.get_logger(__name__)

_PLACEHOLDER_KEY = "vlt_live_xxx"


class VerosightResult(NamedTuple):
    data: Any | None
    meta: dict[str, Any] | None
    full_envelope: dict[str, Any] | None
    credits_used: int
    error: ErrorReport | None


class VerosightClient:
    """HTTP client for Verosight REST API (https://api.verosight.com)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.VEROSIGHT_API_BASE).rstrip("/")
        self.api_key = (api_key if api_key is not None else settings.VEROSIGHT_API_KEY).strip()
        self.timeout = timeout
        self._jwt_token: str | None = None
        self._jwt_expires_at: float = 0.0

    async def _ensure_jwt(self) -> ErrorReport | None:
        """Attempt to exchange API key for JWT token via POST /v1/auth/token if needed."""
        if self._jwt_token and time.time() < self._jwt_expires_at:
            return None

        url = f"{self.base_url}/v1/auth/token"
        headers = {"X-API-Key": self.api_key, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, headers=headers, json={"api_key": self.api_key})
                if resp.status_code in (401, 403):
                    return ErrorReport(
                        component="verosight",
                        stage="auth_token",
                        message="Failed to exchange API key for JWT: HTTP 401/403 Unauthorized.",
                        http_status=resp.status_code,
                        endpoint="/v1/auth/token",
                        retryable=False,
                    )
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        token = None
                        if isinstance(body, dict):
                            token = body.get("token") or body.get("access_token")
                            if not token and isinstance(body.get("data"), dict):
                                token = body["data"].get("token") or body["data"].get("access_token")
                        if token:
                            self._jwt_token = str(token)
                            self._jwt_expires_at = time.time() + 86400 - 300
                    except Exception:
                        pass
        except (httpx.RequestError, httpx.TimeoutException, OSError):
            # If JWT exchange endpoint is unavailable or fails, fallback to direct X-API-Key header
            pass

        return None

    async def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> VerosightResult:
        """Execute a request against Verosight API with explicit ErrorReport stage mapping."""
        # 1. Missing API Key check (explicit fail without mock)
        if not self.api_key or self.api_key == _PLACEHOLDER_KEY:
            report = ErrorReport(
                component="verosight",
                stage="missing_api_key",
                message="VEROSIGHT_API_KEY is missing or unconfigured placeholder ('vlt_live_xxx').",
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        # 2. JWT Auto-refresh check
        jwt_error = await self._ensure_jwt()
        if jwt_error is not None:
            return VerosightResult(None, None, None, 0, jwt_error)

        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
        }
        if self._jwt_token:
            headers["Authorization"] = f"Bearer {self._jwt_token}"

        url = f"{self.base_url}{endpoint}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        clean_json = {k: v for k, v in (json_data or {}).items() if v is not None} if json_data else None

        # 3. HTTP Request
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.request(
                    method=method.upper(),
                    url=url,
                    params=clean_params if clean_params else None,
                    json=clean_json if clean_json else None,
                    headers=headers,
                )
        except (httpx.RequestError, httpx.TimeoutException, OSError) as exc:
            report = ErrorReport(
                component="verosight",
                stage="http_request",
                message=f"HTTP connection failure to Verosight API ({endpoint}): {exc}",
                endpoint=endpoint,
                retryable=True,
            )
            return VerosightResult(None, None, None, 0, report)

        status_code = resp.status_code
        req_id = resp.headers.get("X-Request-ID")

        # 4. HTTP Error Handling
        if status_code in (401, 403):
            report = ErrorReport(
                component="verosight",
                stage="auth_token",
                message=f"Authentication failed ({status_code}) — check VEROSIGHT_API_KEY.",
                http_status=status_code,
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        if status_code == 402:
            remaining = resp.headers.get("X-Credits-Remaining", "unknown")
            charged = resp.headers.get("X-Credits-Charged", "unknown")
            report = ErrorReport(
                component="verosight",
                stage="insufficient_credits",
                message=(
                    f"Insufficient Verosight credits (402). Required: {charged}, "
                    f"remaining: {remaining}. Stop calling paid tools."
                ),
                http_status=402,
                provider_code="insufficient_credits",
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        if status_code == 429:
            reset_secs = resp.headers.get("X-RateLimit-Reset", resp.headers.get("Retry-After", "60"))
            report = ErrorReport(
                component="verosight",
                stage="rate_limited",
                message=f"Verosight rate limit exceeded (429). Reset in {reset_secs}s.",
                http_status=429,
                provider_code="rate_limited",
                request_id=req_id,
                endpoint=endpoint,
                retryable=True,
            )
            return VerosightResult(None, None, None, 0, report)

        if status_code == 404:
            report = ErrorReport(
                component="verosight",
                stage="not_found",
                message=f"Verosight endpoint/resource not found (404): {endpoint}.",
                http_status=404,
                provider_code="not_found",
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        if status_code >= 500:
            report = ErrorReport(
                component="verosight",
                stage="server_error",
                message=f"Verosight server error ({status_code}): {resp.text[:200]}.",
                http_status=status_code,
                provider_code="server_error",
                request_id=req_id,
                endpoint=endpoint,
                retryable=True,
            )
            return VerosightResult(None, None, None, 0, report)

        if status_code < 200 or status_code >= 300:
            report = ErrorReport(
                component="verosight",
                stage="http_request",
                message=f"Unexpected status code {status_code} from {endpoint}.",
                http_status=status_code,
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        # 5. Envelope parsing
        try:
            payload = resp.json()
        except Exception as exc:
            report = ErrorReport(
                component="verosight",
                stage="envelope_parse",
                message=f"Failed to parse JSON response from {endpoint}: {exc}",
                http_status=status_code,
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, None, 0, report)

        if not isinstance(payload, dict) or "data" not in payload or "meta" not in payload or not isinstance(payload.get("meta"), dict):
            report = ErrorReport(
                component="verosight",
                stage="envelope_parse",
                message=f"Invalid response envelope structure from {endpoint}: expected dict with 'data' and 'meta' keys.",
                http_status=status_code,
                request_id=req_id,
                endpoint=endpoint,
                retryable=False,
            )
            return VerosightResult(None, None, payload if isinstance(payload, dict) else None, 0, report)

        meta = payload["meta"]
        req_id = meta.get("request_id", req_id)
        credits_used = meta.get("credits_used", 0)
        data = payload["data"]

        return VerosightResult(
            data=data,
            meta=meta,
            full_envelope=payload,
            credits_used=credits_used,
            error=None,
        )
