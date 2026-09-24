"""Shared FastAPI security dependencies (Phase 16).

Admin/management routes are protected with a simple API key sent in the
``X-API-Key`` header and compared (constant-time) to the configured
``ADMIN_API_KEY``. Telephony webhooks are protected with a shared webhook token
in the ``X-Webhook-Token`` header. Both keep the deterministic, cost-free
provider story intact - they gate the HTTP surface, not the provider mocks.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Header, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from starlette.requests import Request

from app.config import settings


# Declaring the API-key scheme makes FastAPI/Swagger expose an Authorize button.
# The actual validation remains the same constant-time comparison below.
admin_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _secure_compare(a: str, b: str) -> bool:
    """Constant-time-ish comparison to avoid leaking timing differences."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


async def require_admin_api_key(
    request: Request,
    x_api_key: Optional[str] = Security(admin_api_key_header),
) -> None:
    """Protect management routes while keeping local Swagger friction-free.

    In development, requests originating from the local machine are allowed
    without a key so Swagger's generated Execute request works immediately.
    Any non-local request, and every request in non-development environments,
    still requires the configured X-API-Key.
    """
    if settings.ENVIRONMENT == "development":
        client_host = request.client.host if request.client else ""
        if client_host in {"127.0.0.1", "localhost", "::1"}:
            return

    if not settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin api key not configured",
        )
    if not x_api_key or not _secure_compare(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing admin api key",
        )


async def require_webhook_token(
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None),
) -> None:
    """FastAPI dependency: verify telephony webhook calls.

    Only enforced when WEBHOOK_TOKEN is configured. The token may arrive via the
    ``X-Webhook-Token`` header or the ``token`` query parameter (some providers
    only let webhook destinations carry a query token).
    """
    if not settings.WEBHOOK_TOKEN:
        return
    provided = x_webhook_token or request.query_params.get("token") or ""
    if not _secure_compare(provided, settings.WEBHOOK_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook token",
        )


async def limit_request_body(request: Request) -> None:
    """Reject request bodies larger than MAX_REQUEST_BYTES (hardening)."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.MAX_REQUEST_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="request body too large",
                )
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid content-length",
            )
