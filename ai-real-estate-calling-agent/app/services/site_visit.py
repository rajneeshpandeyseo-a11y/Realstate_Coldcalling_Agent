"""Business logic for site-visit bookings (Phase 13).

Turns the natural-language visit-booking captured during a call into a durable
`SiteVisit` record, guards against duplicates, keeps the audit trail, and
promotes the lead to QUALIFIED when a visit is successfully scheduled.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import site_visit as visit_crud
from app.crud.lead import get_lead as crud_get_lead
from app.logging_config import get_logger
from app.models.enums import LeadStatus, SiteVisitStatus
from app.schemas.site_visit import (
    SiteVisitCreate,
    SiteVisitUpdate,
)
from app.services import audit

log = get_logger("app.services.site_visit")


class SiteVisitError(Exception):
    """Raised for invalid site-visit operations."""


class MultipleActiveVisitsError(SiteVisitError):
    """Raised when a lead would end up with more than one open visit."""


# Days of the week recognised in Hindi/Hinglish.
_DAY_NAMES = {
    "aarambh": None,
    "somwaar": "monday", "somvar": "monday", "monday": "monday",
    "mangalwaar": "tuesday", "mangalvar": "tuesday", "tuesday": "tuesday",
    "budhwaar": "wednesday", "budhvar": "wednesday", "wednesday": "wednesday",
    "guruwaar": "thursday", "guruvara": "thursday", "thursday": "thursday",
    "shukrawaar": "friday", "shukravar": "friday", "friday": "friday",
    "shanivaar": "saturday", "shanivar": "saturday", "saturday": "saturday",
    "ravivaar": "sunday", "ravivar": "sunday", "sunday": "sunday",
}

_ACTIVE_VISIT_STATUSES = {
    SiteVisitStatus.REQUESTED.value,
    SiteVisitStatus.CONFIRMED.value,
    SiteVisitStatus.RESCHEDULED.value,
}


def _parse_weekday(text: str) -> str | None:
    """Best-effort weekday extraction (Hinglish days + English)."""
    lowered = text.lower()
    for key, value in _DAY_NAMES.items():
        if key in lowered and value is not None:
            return value
    # "aaj", "kal", "parso" relative dates.
    if any(d in lowered for d in ("parso", "parson")):
        return "day_after_tomorrow"
    if "kal" in lowered and "kal subah" not in lowered:
        return "tomorrow"
    if "aaj" in lowered:
        return "today"
    return None


def _parse_time(text: str) -> str | None:
    """Best-effort time-of-day extraction (English + Hinglish)."""
    lowered = text.lower()
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?", text, re.IGNORECASE)
    if m:
        hour = int(m.group(1))
        minute = m.group(2)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute}"
    for period in ("morning", "subah", "savare", "sava"):
        if period in lowered:
            return "morning"
    for period in ("afternoon", "dopahar", "do pahar"):
        if period in lowered:
            return "afternoon"
    for period in ("evening", "shaam", "sham"):
        if period in lowered:
            return "evening"
    if re.search(r"night|\braat\b", lowered):
        return "evening"
    return None


def capture_booking_from_text(text: str) -> dict[str, str | None]:
    """Extract the visit preferences from a natural-language booking utterance.

    Returns a dict with optional ``preferred_time``, ``alternate_time`` and a
    ``preferred_date`` description (relative weekday). Best-effort and
    deterministic; the raw text is always preserved on the visit notes so a
    human can reconcile anything the parser misses.
    """
    return {
        "preferred_time": _parse_time(text),
        "alternate_time": None,
        "preferred_date": _parse_weekday(text),
    }


def capture_booking_from_slots(text: str, slots: dict) -> dict[str, str | None]:
    """Merge rule-captured booking data with engine slots (alias helper)."""
    captured = capture_booking_from_text(text)
    for key in ("preferred_time", "preferred_date"):
        if not captured.get(key):
            captured[key] = slots.get(key)
    return captured


def _next_occurrence(weekday: str, base: date | None = None) -> date:
    """Return the next date matching ``weekday`` (monday=0 ... sunday=6)."""
    base = base or date.today()
    target = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }[weekday]
    days_ahead = (target - base.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return base + timedelta(days=days_ahead)


def resolve_preferred_date(weekday_desc: str | None) -> date | None:
    """Turn a weekday descriptor into an actual upcoming date (best-effort)."""
    if not weekday_desc:
        return None
    if weekday_desc == "date":
        return None
    today = date.today()
    if weekday_desc == "today":
        return today
    if weekday_desc == "tomorrow":
        return today + timedelta(days=1)
    if weekday_desc == "day_after_tomorrow":
        return today + timedelta(days=2)
    if weekday_desc in _DAY_NAMES.values():
        return _next_occurrence(weekday_desc)
    return None


async def create_site_visit(db: AsyncSession, data: SiteVisitCreate) -> "object":
    """Create a site visit, guarding against duplicate open visits for a lead."""
    lead = await crud_get_lead(db, data.lead_id)
    if lead is None:
        raise KeyError("lead not found")

    active = await visit_crud.get_site_visits_for_lead(db, data.lead_id)
    if any(
        getattr(v.status, "value", v.status) in _ACTIVE_VISIT_STATUSES for v in active
    ):
        raise MultipleActiveVisitsError(
            f"lead {data.lead_id} already has an active site-visit"
        )

    visit = await visit_crud.create_site_visit(db, data)
    await audit.record_audit(
        db, action="site_visit.create", resource_type="site_visit",
        resource_id=visit.id,
        details={"lead_id": str(data.lead_id), "call_id": str(data.call_id) if data.call_id else None},
    )
    return visit


async def confirm_site_visit(db: AsyncSession, visit_id: uuid.UUID) -> "object":
    """Mark a site visit as confirmed by the customer."""
    visit = await visit_crud.get_site_visit(db, visit_id)
    if visit is None:
        raise KeyError("site visit not found")
    visit = await visit_crud.update_site_visit(
        db, visit,
        SiteVisitUpdate(
            status=SiteVisitStatus.CONFIRMED, confirmed_by_customer=True
        ),
    )
    await _promote_lead(db, visit.lead_id)
    await audit.record_audit(
        db, action="site_visit.confirm", resource_type="site_visit", resource_id=visit.id
    )
    return visit


async def cancel_site_visit(db: AsyncSession, visit_id: uuid.UUID) -> "object":
    """Cancel a site visit."""
    visit = await visit_crud.get_site_visit(db, visit_id)
    if visit is None:
        raise KeyError("site visit not found")
    visit = await visit_crud.update_site_visit(
        db, visit, SiteVisitUpdate(status=SiteVisitStatus.CANCELLED)
    )
    await audit.record_audit(
        db, action="site_visit.cancel", resource_type="site_visit", resource_id=visit.id
    )
    return visit


async def update_site_visit(
    db: AsyncSession, visit_id: uuid.UUID, data: SiteVisitUpdate
) -> "object":
    """Update a site visit, tracking status transitions safely."""
    visit = await visit_crud.get_site_visit(db, visit_id)
    if visit is None:
        raise KeyError("site visit not found")
    visit = await visit_crud.update_site_visit(db, visit, data)
    if data.status == SiteVisitStatus.CONFIRMED and data.confirmed_by_customer:
        await _promote_lead(db, visit.lead_id)
    await audit.record_audit(
        db, action="site_visit.update", resource_type="site_visit", resource_id=visit.id
    )
    return visit


async def create_booking_for_call(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    call_id: uuid.UUID,
    booking_text: str | None = None,
    slots: dict | None = None,
    location: str | None = None,
) -> "object":
    """Create a SiteVisit after a successful (qualified) call.

    Best-effort: parses the captured booking utterance into preferred
    date/time, and promotes the lead to QUALIFIED. If the lead already has an
    open visit the duplicate guard refreshes it instead of failing.
    """
    from app.crud import site_visit as visit_crud

    captured = capture_booking_from_slots(booking_text or "", slots or {})
    resolved = resolve_preferred_date(captured.get("preferred_date"))
    data = SiteVisitCreate(
        lead_id=lead_id,
        call_id=call_id,
        preferred_date=resolved,
        preferred_time=captured.get("preferred_time"),
        alternate_time=captured.get("alternate_time"),
        location=location,
        notes=(booking_text or "").strip() or None,
    )

    active = await visit_crud.get_site_visits_for_lead(db, lead_id)
    if any(
        getattr(v.status, "value", v.status) in _ACTIVE_VISIT_STATUSES for v in active
    ):
        visit = active[0]
        await visit_crud.update_site_visit(
            db, visit,
            SiteVisitUpdate(
                preferred_date=data.preferred_date,
                preferred_time=data.preferred_time,
                location=data.location or visit.location,
                notes=data.notes or visit.notes,
            ),
        )
        visit = await visit_crud.get_site_visit(db, visit.id)
    else:
        visit = await create_site_visit(db, data)

    # Persist the latest captured requirements snapshot onto the lead so the
    # qualification details (property type, budget, location, purpose, timeline)
    # are stored alongside the visit for CRM/reporting.
    from app.services.lead_requirement import upsert_requirements

    await upsert_requirements(
        db,
        lead_id=lead_id,
        call_id=call_id,
        slots=slots or {},
        lead_score="qualified",
    )

    await _promote_lead(db, lead_id)
    return visit


async def _promote_lead(db: AsyncSession, lead_id: uuid.UUID) -> None:
    """Promote the lead to QUALIFIED (caller owns the transaction/commit)."""
    lead = await crud_get_lead(db, lead_id)
    if lead is not None and getattr(lead.status, "value", lead.status) != LeadStatus.QUALIFIED.value:
        lead.status = LeadStatus.QUALIFIED
        await db.flush()
