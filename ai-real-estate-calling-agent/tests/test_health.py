"""Basic API tests for PHASE 1 project foundation."""

import pytest


@pytest.mark.asyncio
async def test_health_ok(client):
    """GET /health returns ok status."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "service" in data
    assert "version" in data


@pytest.mark.asyncio
async def test_root(client):
    """GET / returns service metadata."""
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"]
    assert data["health"] == "/health"
    assert data["ready"] == "/ready"


@pytest.mark.asyncio
async def test_ready_structure(client):
    """GET /ready returns a structured dependency map."""
    resp = await client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "dependencies" in data
    assert "postgresql" in data["dependencies"]
