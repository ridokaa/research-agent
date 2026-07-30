"""Basic smoke tests for PROMPT 0 deliverables."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    """GET /health must return {"status": "ok"}."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
