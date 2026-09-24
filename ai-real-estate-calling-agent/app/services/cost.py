"""Business logic for call cost records and aggregation (Phase 15).

The orchestrator computes a per-call `CostLedger` (telephony, STT, TTS, LLM).
This service persists each non-trivial component as a durable `CostRecord`
row and re-aggregates them, so costs are auditable after the fact and a future
billing/dashboard layer can query them without recomputation.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import cost as cost_crud
from app.models.enums import CostComponent
from app.schemas.cost import CostRecordCreate, CostRecordOut, CallCostSummary

# Component -> (unit, per-unit rate, quantity) metadata kept for auditability.
_COMPONENT_META: dict[str, tuple[str | None, str, float | None]] = {
    "telephony": ("minutes", None, None),
    "stt": ("seconds", "audio", None),
    "tts": ("chars", None, None),
    "llm": ("tokens", None, None),
}


async def persist_ledger(db: AsyncSession, call_id, ledger) -> list:
    """Persist one CostRecord per component present in the ledger.

    Caller owns the commit. Returns the list of created CostRecord rows.
    """
    per_component = {
        "telephony": ledger.telephony,
        "stt": ledger.stt,
        "tts": ledger.tts,
        "llm": ledger.llm,
    }
    records = []
    for component, amount in per_component.items():
        unit, _unit_meta, rate = _COMPONENT_META.get(component, (None, None, None))
        record = await cost_crud.create_cost_record(
            db,
            CostRecordCreate(
                call_id=call_id,
                component=CostComponent(component),
                currency=ledger.currency,
                amount=round(float(amount), 8),
                quantity=float(amount) if unit != "minutes" else None,
                unit=unit,
                rate=rate,
            ),
        )
        records.append(record)
    return records


async def get_records_for_call(db: AsyncSession, call_id: uuid.UUID) -> list[dict]:
    """Return persisted cost records for a call as dicts (for REST)."""
    rows = await cost_crud.get_cost_records_for_call(db, call_id)
    return [
        CostRecordOut.model_validate(r).model_dump(mode="json") for r in rows
    ]


async def summary_for_call(db: AsyncSession, call_id: uuid.UUID) -> CallCostSummary:
    """Aggregate persisted cost records into a per-component summary."""
    rows = await cost_crud.get_cost_records_for_call(db, call_id)
    components: dict[str, float] = {}
    for component in CostComponent:
        components[component.value] = 0.0
    for r in rows:
        comp = getattr(r.component, "value", r.component)
        components[comp] = components.get(comp, 0.0) + float(r.amount)
    total = round(sum(components.values()), 8)
    return CallCostSummary(
        call_id=call_id,
        currency="INR",
        components={k: round(v, 8) for k, v in components.items()},
        total=total,
    )


# ---------------------------------------------------------------------------
# Cost policy helpers (deterministic-first + phrase-cache savings). Pure functions
# so the strategy is cheap to reason about and fully testable at zero cost.
# ---------------------------------------------------------------------------


def llm_avoidance_ratio(total_turns: int, llm_turns: int) -> float:
    """Fraction of conversation turns handled WITHOUT the LLM fallback.

    The engine routes via deterministic rules by default and only invokes the
    LLM for low-confidence inputs, so in practice most turns avoid LLM cost.
    """
    if total_turns <= 0:
        return 0.0
    return round(max(0.0, min(1.0, 1.0 - llm_turns / total_turns)), 4)


def tts_cache_hit_ratio(total_phrases: int, cached_phrases: int) -> float:
    """Fraction of TTS phrases served from the audio cache (no re-synthesis).

    Repeated canned replies hit the phrase cache, saving TTS characters/cost.
    """
    if total_phrases <= 0:
        return 0.0
    return round(max(0.0, min(1.0, cached_phrases / total_phrases)), 4)


def projected_call_cost(
    *,
    duration_minutes: float,
    stt_cost=None,
    tts_cost=None,
    llm_cost=None,
    telephony_per_minute: float = 0.0,
    stt_per_minute: float = 0.0,
    tts_per_char: float = 0.0,
    tts_chars: int = 0,
    llm_tokens: int = 0,
) -> dict[str, float]:
    """Project a call's cost breakdown from usage + rates (policy budget).

    Accepts either precomputed component costs (from the ledger) or raw usage
    that is multiplied by the configured rates. Returns a per-component dict.
    """
    telephony = telephony_per_minute * duration_minutes
    stt = stt_cost if stt_cost is not None else stt_per_minute * duration_minutes
    tts = tts_cost if tts_cost is not None else tts_per_char * tts_chars
    llm = llm_cost if llm_cost is not None else 0.0
    if llm_cost is None and llm_tokens > 0:
        from app.config import settings

        llm = (
            settings.LLM_COST_PER_1M_INPUT * llm_tokens / 1_000_000
            + settings.LLM_COST_PER_1M_OUTPUT * llm_tokens / 1_000_000
        )
    components = {
        "telephony": round(telephony, 8),
        "stt": round(stt, 8),
        "tts": round(tts, 8),
        "llm": round(llm, 8),
    }
    components["total"] = round(sum(components.values()), 8)
    return components
