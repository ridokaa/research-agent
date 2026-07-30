"""current_time tool implementation."""
from __future__ import annotations

import zoneinfo
from datetime import UTC, datetime

from app.tools.registry import register_tool


@register_tool(
    name="current_time",
    description="Get the current system time in ISO 8601 format.",
    source="local",
    cost_credits=0,
    timeout_s=5.0,
    tags=["utility", "time"],
)
async def current_time(tz: str = "UTC") -> str:
    """Return the current system time in ISO 8601 format for a given timezone."""
    try:
        tz_obj = zoneinfo.ZoneInfo(tz)
    except Exception:
        tz_obj = UTC
    return datetime.now(tz=tz_obj).isoformat()
