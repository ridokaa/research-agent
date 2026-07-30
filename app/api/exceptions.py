"""Global exception handlers enforcing consistent error response structure."""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


def _get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _format_error_response(
    code: str | int,
    message: str,
    request_id: str,
    status_code: int,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "error": {
            "code": str(code),
            "message": message,
        },
        "meta": {
            "request_id": request_id,
        },
    }
    return JSONResponse(status_code=status_code, content=payload)


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle standard FastAPI/Starlette HTTPExceptions."""
    req_id = _get_request_id(request)
    logger.warning("http.exception", status_code=exc.status_code, detail=exc.detail, request_id=req_id)
    return _format_error_response(
        code=exc.status_code,
        message=str(exc.detail),
        request_id=req_id,
        status_code=exc.status_code,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic/FastAPI request body/query validation errors."""
    req_id = _get_request_id(request)
    logger.warning("validation.error", errors=exc.errors(), request_id=req_id)
    return _format_error_response(
        code="VALIDATION_ERROR",
        message=f"Validation error: {exc.errors()}",
        request_id=req_id,
        status_code=422,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected server exceptions."""
    req_id = _get_request_id(request)
    logger.exception("unhandled.exception", error=str(exc), request_id=req_id)
    return _format_error_response(
        code="INTERNAL_SERVER_ERROR",
        message="An unexpected server error occurred.",
        request_id=req_id,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on the FastAPI app instance."""
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
