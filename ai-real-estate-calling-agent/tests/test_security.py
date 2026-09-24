"""Phase 16 - Security tests (auth, webhook token, PII redaction, body limit)."""

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.services.pii import redact_pii, redact_pii_value


# ---------------------------------------------------------------------------
# PII redaction (pure unit tests - no DB / network needed).
# ---------------------------------------------------------------------------
def test_redact_phone():
    out = redact_pii("call me at +91 98765 43210 now")
    assert "43210" not in out
    assert "8765" not in out


def test_redact_masks_middle_digits_only():
    out = redact_pii("+919876543210")
    assert "*" in out
    assert out.startswith("+98")
    assert out.endswith("10")


def test_redact_email():
    out = redact_pii("contact ram@example.in please")
    assert "ram@example.in" not in out
    assert "@example.in" in out


def test_redact_nested_structure():
    data = {"phone": "+91 98765 43210", "emails": ["a@b.co", "x@y.z"], "n": 42}
    out = redact_pii_value(data)
    assert "98765" not in str(out)
    assert "a@b.co" not in str(out)
    assert out["n"] == 42


def test_redact_plain_text_without_pii_unchanged():
    assert redact_pii("hello this is fine") == "hello this is fine"


# ---------------------------------------------------------------------------
# Header-less client to exercise auth rejections.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def bare_client(db_sessionmaker):
    """Async client WITHOUT default auth headers (for 401 assertions)."""
    from app.main import app
    from app.db.session import get_db

    async def _dep():
        async with db_sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = _dep
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_admin_routes_require_key(bare_client):
    resp = await bare_client.get("/api/v1/leads")
    assert resp.status_code == 401


async def test_admin_routes_reject_wrong_key(bare_client):
    resp = await bare_client.get("/api/v1/leads", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


async def test_admin_routes_accept_valid_key(bare_client):
    resp = await bare_client.get(
        "/api/v1/leads", headers={"X-API-Key": os.environ["ADMIN_API_KEY"]}
    )
    assert resp.status_code == 200


async def test_webhook_status_rejects_missing_token(bare_client):
    resp = await bare_client.post("/api/v1/webhooks/plivo/status")
    assert resp.status_code == 401


async def test_webhook_status_rejects_wrong_token(bare_client):
    resp = await bare_client.post(
        "/api/v1/webhooks/plivo/status", headers={"X-Webhook-Token": "nope"}
    )
    assert resp.status_code == 401


async def test_webhook_status_accepts_valid_token(bare_client):
    resp = await bare_client.post(
        "/api/v1/webhooks/plivo/status",
        headers={"X-Webhook-Token": os.environ["WEBHOOK_TOKEN"]},
    )
    # Auth passes; the empty/malformed payload just yields a bad-request, not 401.
    assert resp.status_code != 401


async def test_answer_route_open(bare_client):
    # /answer must stay reachable from the telephony provider without a token.
    resp = await bare_client.post("/api/v1/webhooks/plivo/answer")
    assert resp.status_code != 401


async def test_body_size_limit(bare_client):
    big = {"name": "x" * 1_200_000, "phone": "+91 98765 43210"}
    resp = await bare_client.post("/api/v1/leads", json=big)
    assert resp.status_code == 413
