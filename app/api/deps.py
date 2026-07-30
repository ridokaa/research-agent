"""API dependencies — injected via FastAPI Depends()."""
from __future__ import annotations

from app.db.session import get_session

__all__ = ["get_session"]
