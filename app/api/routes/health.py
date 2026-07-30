"""Health check route."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str


@router.get("/health", response_model=HealthResponse, summary="Basic health check")
async def health() -> HealthResponse:
    """Returns `{"status": "ok"}` when the server is running."""
    return HealthResponse(status="ok")
