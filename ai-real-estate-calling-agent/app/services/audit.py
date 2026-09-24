"""Audit logging for significant actions (append-only)."""

from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.services.pii import redact_pii_value


async def record_audit(
    db: AsyncSession,
    *,
    action: str,
    actor_type: str = "system",
    actor_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict | None = None,
) -> None:
    """Append an audit entry without triggering a separate commit."""
    db.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else None,
            details=json.dumps(redact_pii_value(details)) if details else None,
        )
    )
    await db.flush()
