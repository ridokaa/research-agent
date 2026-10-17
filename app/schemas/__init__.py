"""Schemas package — Pydantic request/response models."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agent.state import Message


def __getattr__(name: str) -> Any:
    if name == "Message":
        from app.agent.state import Message
        return Message
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


__all__ = ["Message"]
