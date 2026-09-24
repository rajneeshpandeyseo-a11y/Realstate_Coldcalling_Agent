"""LeadRequirement persistence - snapshot of the latest captured requirements.

The conversation engine collects slots (property_type, bhk, location, budget,
timeline, purpose) in memory. On a successful (qualified) call we persist the
latest snapshot onto the lead so downstream consumers (CRM, reporting) can read
the final requirements without parsing transcripts.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lead_requirement import LeadRequirement


def _budget_range(raw: str | None) -> tuple[float | None, float | None]:
    """Best-effort parse of a raw budget string into (min, max) in lakhs/INR.

    Handles forms like "40 lakh", "1 crore", "50 lakh - 1 crore". Pure string
    logic; the raw text is always stored so nothing is lost on a miss.
    """
    if not raw:
        return None, None
    text = raw.lower().strip()

    def to_units(num: str, crore: bool = False) -> float:
        value = float(num)
        return value * 100 if crore else value

    numbers = re.findall(r"(\d+(?:\.\d+)?)", text)
    if not numbers:
        return None, None
    is_crore = "crore" in text or "cr" in text.split() or "koti" in text
    vals = [to_units(float(n), is_crore) for n in numbers]
    if "crore" in text or "cr" in text or "koti" in text:
        vals = [v * 100 for v in vals]
    return min(vals), max(vals)


async def upsert_requirements(
    db: AsyncSession,
    *,
    lead_id: uuid.UUID,
    call_id: uuid.UUID | None = None,
    slots: dict | None = None,
    lead_score: str | None = None,
) -> LeadRequirement:
    """Replace the latest requirement snapshot for a lead (or create it)."""
    slots = dict(slots or {})
    stmt = select(LeadRequirement).where(LeadRequirement.lead_id == str(lead_id))
    result = await db.execute(stmt)
    existing = result.scalars().first()

    budget_raw = slots.get("budget")
    budget_min, budget_max = _budget_range(budget_raw)

    fields = dict(
        call_id=str(call_id) if call_id else None,
        property_type=slots.get("property_type"),
        bhk=slots.get("bhk"),
        location=slots.get("location") or slots.get("preferred_location"),
        city=slots.get("city"),
        budget_min=budget_min,
        budget_max=budget_max,
        budget_raw=budget_raw,
        purpose=slots.get("purpose"),
        timeline=slots.get("timeline") or slots.get("purchase_timeline"),
        lead_score=lead_score,
    )

    if existing is not None:
        for key, value in fields.items():
            if value is not None:
                setattr(existing, key, value)
        record = existing
    else:
        record = LeadRequirement(lead_id=str(lead_id), **fields)
        db.add(record)
    await db.flush()
    return record
