"""Health and dependency status check endpoints."""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_session
from app.mcp.manager import get_mcp_manager

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


class HealthDepsResponse(BaseModel):
    status: str
    db: str
    llm: str
    mcp: str
    details: dict[str, Any] | None = None


@router.get("/health", response_model=HealthResponse, summary="Basic health check")
async def health() -> HealthResponse:
    """Returns `{"status": "ok"}` when the server is running."""
    return HealthResponse(status="ok")


@router.get(
    "/health/deps",
    response_model=HealthDepsResponse,
    summary="Detailed dependency health check (DB, LLM, MCP)",
)
async def health_deps(
    session: AsyncSession = Depends(get_session),
) -> HealthDepsResponse:
    """Inspect status of core dependencies (database pool, LLM config, MCP servers)."""
    settings = get_settings()
    db_status = "ok"
    llm_status = "ok"
    mcp_status = "ok"
    details: dict[str, Any] = {}

    # 1. DB Check
    try:
        await session.execute(text("SELECT 1"))
        details["db"] = "connected"
    except Exception as exc:  # noqa: BLE001
        db_status = "error"
        details["db"] = f"failed: {exc}"

    # 2. LLM Config Check
    if not settings.LLM_API_KEY or not settings.LLM_BASE_URL:
        llm_status = "error"
        details["llm"] = "missing API key or base URL"
    else:
        details["llm"] = f"configured ({settings.LLM_MODEL})"

    # 3. MCP Manager Check
    if settings.VEROSIGHT_ENABLED and settings.VEROSIGHT_MODE in ("mcp", "both"):
        try:
            mgr = get_mcp_manager()
            mcp_status = "ok" if mgr is not None else "error"
            details["mcp"] = f"active connections: {len(mgr._clients)}" if mgr else "manager unavailable"
        except Exception as exc:  # noqa: BLE001
            mcp_status = "error"
            details["mcp"] = f"error: {exc}"
    else:
        mcp_status = "disabled"
        details["mcp"] = "disabled by configuration"

    overall_status = (
        "ok"
        if (db_status == "ok" and llm_status == "ok" and mcp_status in ("ok", "disabled"))
        else "degraded"
    )

    return HealthDepsResponse(
        status=overall_status,
        db=db_status,
        llm=llm_status,
        mcp=mcp_status,
        details=details,
    )
