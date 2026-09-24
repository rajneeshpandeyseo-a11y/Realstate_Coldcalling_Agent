"""Public meta/status endpoints for the operator dashboard - /api/v1/meta.

Exposes ONLY non-secret configuration, readiness checks and aggregate
statistics so the static dashboard (served from the same FastAPI app) can
render the Settings page, the test-call readiness panel and the KPI cards
without ever touching API keys or tokens.
"""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.logging_config import get_logger
from app.models.call import Call
from app.models.cost_record import CostRecord
from app.models.enums import CallStatus, LeadStatus, SiteVisitStatus
from app.models.follow_up import FollowUp
from app.models.lead import Lead
from app.models.site_visit import SiteVisit
from app.providers import get_telephony_provider
from app.providers.base import ProviderKind

log = get_logger("app.api.v1.meta")

router = APIRouter(prefix="/meta", tags=["meta"])


def _status_view() -> dict:
    """Non-secret runtime configuration summary for the dashboard."""
    return {
        "environment": settings.ENVIRONMENT,
        "mock_mode": settings.is_mock,
        "live_calls_enabled": settings.LIVE_CALLS_ENABLED,
        "auto_retry_enabled": settings.AUTO_RETRY_ENABLED,
        "recording_enabled": settings.RECORDING_ENABLED,
        "transcript_storage_enabled": settings.TRANSCRIPT_STORAGE_ENABLED,
        "public_base_url": settings.PUBLIC_BASE_URL,
        "providers": {
            "telephony": {
                "name": settings.TELEPHONY_PROVIDER,
                "configured": bool(
                    settings.TELEPHONY_PROVIDER == "mock"
                    or (
                        settings.TELEPHONY_PROVIDER == "plivo"
                        and settings.PLIVO_AUTH_ID
                        and settings.PLIVO_AUTH_TOKEN
                        and settings.PLIVO_PHONE_NUMBER
                    )
                ),
                "cost_per_minute": settings.PLIVO_COST_PER_MINUTE,
                "endpoint": settings.PLIVO_ENDPOINT,
            },
            "stt": {
                "name": settings.STT_PROVIDER,
                "configured": bool(
                    settings.STT_PROVIDER == "mock" or bool(settings.SARVAM_API_KEY)
                ),
                "model": settings.STT_MODEL,
                "cost_per_minute": settings.STT_COST_PER_MINUTE,
            },
            "tts": {
                "name": settings.TTS_PROVIDER,
                "configured": bool(
                    settings.TTS_PROVIDER == "mock" or bool(settings.SARVAM_API_KEY)
                ),
                "model": getattr(settings, "TTS_MODEL", None),
                "voice": getattr(settings, "SARVAM_TTS_VOICE", None),
                "cost_per_char": settings.TTS_COST_PER_CHAR,
            },
            "llm": {
                "name": settings.LLM_PROVIDER,
                "configured": bool(
                    settings.LLM_PROVIDER in ("fixed", "mock")
                    or bool(settings.GEMINI_API_KEY and settings.GEMINI_MODEL)
                ),
                "model": settings.GEMINI_MODEL if settings.LLM_PROVIDER == "gemini" else None,
                "cost_per_1m_input": settings.LLM_COST_PER_1M_INPUT,
                "cost_per_1m_output": settings.LLM_COST_PER_1M_OUTPUT,
            },
        },
        "websocket": {
            "path": "/api/v1/ws/calls/{call_id}",
            "token_required": bool(settings.WEBHOOK_TOKEN),
        },
    }


async def _checks(db: AsyncSession) -> list[dict]:
    """Run the operator readiness checks and return PASS/FAIL rows."""
    checks = []

    checks.append({"key": "api", "label": "API", "status": "PASS", "detail": "responding"})

    try:
        await db.execute(select(1))
        checks.append({"key": "database", "label": "Database", "status": "PASS", "detail": "connected"})
    except Exception as exc:  # pragma: no cover
        checks.append({"key": "database", "label": "Database", "status": "FAIL", "detail": str(exc)})

    plivo_ok = (
        settings.TELEPHONY_PROVIDER == "mock"
        or all([settings.PLIVO_AUTH_ID, settings.PLIVO_AUTH_TOKEN, settings.PLIVO_PHONE_NUMBER])
    )
    checks.append({
        "key": "plivo", "label": "Plivo configuration",
        "status": "PASS" if plivo_ok else "FAIL",
        "detail": f"provider={settings.TELEPHONY_PROVIDER}"
        if plivo_ok else "auth/bundle/from-number missing",
    })

    checks.append({
        "key": "stt", "label": "STT",
        "status": "PASS" if (settings.STT_PROVIDER == "mock" or settings.SARVAM_API_KEY) else "FAIL",
        "detail": f"{settings.STT_PROVIDER} ({getattr(settings, 'STT_MODEL', '')})",
    })
    checks.append({
        "key": "tts", "label": "TTS",
        "status": "PASS" if (settings.TTS_PROVIDER == "mock" or settings.SARVAM_API_KEY) else "FAIL",
        "detail": f"{settings.TTS_PROVIDER} ({getattr(settings, 'SARVAM_TTS_VOICE', '')})",
    })
    llm_ok = (
        settings.LLM_PROVIDER in ("fixed", "mock")
        or bool(settings.GEMINI_API_KEY and settings.GEMINI_MODEL)
    )
    checks.append({
        "key": "llm", "label": "LLM",
        "status": "PASS" if llm_ok else "FAIL",
        "detail": f"{settings.LLM_PROVIDER} ({settings.GEMINI_MODEL})"
        if settings.LLM_PROVIDER == "gemini" else settings.LLM_PROVIDER,
    })

    try:
        provider = get_telephony_provider()
        takes_hangup = provider.kind == ProviderKind.TELEPHONY and hasattr(provider, "hangup") and callable(provider.hangup)
        checks.append({
            "key": "hangup", "label": "Hangup path",
            "status": "PASS" if takes_hangup else "FAIL",
            "detail": f"provider={settings.TELEPHONY_PROVIDER}",
        })
    except Exception as exc:  # pragma: no cover
        checks.append({"key": "hangup", "label": "Hangup path", "status": "FAIL", "detail": str(exc)})

    checks.append({
        "key": "recording", "label": "Recording",
        "status": "PASS" if settings.RECORDING_ENABLED else "FAIL",
        "detail": "requested on outbound calls" if settings.RECORDING_ENABLED else "disabled",
    })

    if settings.PUBLIC_BASE_URL.startswith(("http://", "https://")):
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{settings.PUBLIC_BASE_URL}/health")
            ok = resp.status_code == 200
            checks.append({
                "key": "public_https", "label": "Public HTTPS",
                "status": "PASS" if ok else "FAIL",
                "detail": f"HTTP {resp.status_code}",
            })
        except Exception as exc:  # pragma: no cover
            checks.append({
                "key": "public_https", "label": "Public HTTPS",
                "status": "FAIL", "detail": str(exc).splitlines()[0][:120],
            })
    else:
        checks.append({"key": "public_https", "label": "Public HTTPS", "status": "FAIL", "detail": "PUBLIC_BASE_URL not set"})

    try:
        await db.execute(select(Lead.id).limit(1))
        checks.append({
            "key": "persistence", "label": "DB persistence",
            "status": "PASS", "detail": "lead/call tables reachable",
        })
    except Exception as exc:  # pragma: no cover
        checks.append({"key": "persistence", "label": "DB persistence", "status": "FAIL", "detail": str(exc)})

    return checks


@router.get("/status", tags=["meta-public"])
async def meta_status():
    """Non-secret runtime configuration for the dashboard Settings page."""
    return _status_view()


@router.get("/readiness", tags=["meta-public"])
async def meta_readiness(db: AsyncSession = Depends(get_db)):
    """Server-side operator readiness checks (dashboard pre-call panel)."""
    checks = await _checks(db)
    any_fail = any(c["status"] != "PASS" for c in checks)
    return {
        "ready": not any_fail,
        "checks": checks,
        "live_calls_enabled": settings.LIVE_CALLS_ENABLED,
    }


@router.get("/stats", tags=["meta-public"])
async def meta_stats(
    db: AsyncSession = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Aggregate KPI counts and estimated cost for the dashboard."""
    lead_count = (await db.execute(select(func.count(Lead.id)))).scalar_one()
    call_rows = (
        await db.execute(
            select(Call.status, func.count(Call.id), func.sum(Call.duration_seconds))
            .group_by(Call.status)
        )
    ).all()
    visit_count = (
        await db.execute(
            select(func.count(SiteVisit.id)).where(
                SiteVisit.status == SiteVisitStatus.CONFIRMED.value
            )
        )
    ).scalar_one()
    followup_count = (await db.execute(select(func.count(FollowUp.id)))).scalar_one()
    qualified_count = (
        await db.execute(
            select(func.count(Lead.id)).where(Lead.status == LeadStatus.QUALIFIED.value)
        )
    ).scalar_one()

    total_calls = sum(row[1] for row in call_rows or [])
    completed_calls = sum(
        row[1] for row in (call_rows or []) if row[0] == CallStatus.COMPLETED.value
    )
    failed_calls = sum(
        row[1]
        for row in (call_rows or [])
        if row[0] in (CallStatus.FAILED.value, CallStatus.NO_ANSWER.value, CallStatus.NO_RESPONSE.value, CallStatus.BUSY.value)
    )
    total_seconds = sum((row[2] or 0) for row in (call_rows or []))

    registered_cost = (
        await db.execute(
            select(func.coalesce(func.sum(CostRecord.amount), 0.0))
        )
    ).scalar_one()
    telephony_estimate = total_seconds / 60.0 * settings.PLIVO_COST_PER_MINUTE
    estimated_cost = round(
        float(registered_cost) if float(registered_cost) > 0 else float(telephony_estimate),
        4,
    )

    try:
        transcript_count = 0
        from app.models.conversation_message import ConversationMessage

        transcript_count = (
            await db.execute(select(func.count(ConversationMessage.id)))
        ).scalar_one()
    except Exception:  # pragma: no cover
        transcript_count = 0

    return {
        "days": days,
        "totals": {
            "leads": lead_count,
            "calls": total_calls,
            "completed_calls": completed_calls,
            "failed_calls": failed_calls,
            "qualified_leads": qualified_count,
            "site_visits": visit_count,
            "follow_ups": followup_count,
            "transcript_messages": transcript_count,
        },
        "cost": {
            "currency": "INR",
            "estimated_total": estimated_cost,
            "registered_ledger": round(float(registered_cost), 4),
            "telephony_estimate": round(float(telephony_estimate), 4),
        },
        "recent_activity": {
            "new_leads_last_7d": await _count_since(db, Lead, days=7),
            "calls_last_7d": await _count_since(db, Call, days=7),
        },
    }


async def _count_since(db: AsyncSession, model, days: int) -> int:
    from datetime import datetime, timedelta, timezone

    since = datetime.now(timezone.utc) - timedelta(days=days)
    return (
        await db.execute(select(func.count(model.id)).where(model.created_at >= since))
    ).scalar_one()


_ = uuid  # keep uuid import for any future schema usage