"""Call lifecycle endpoints - /api/v1/calls."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_api_key
from app.crud import call as call_crud
from app.crud import call_event as event_crud
from app.crud import conversation as conv_crud
from app.db.session import get_db
from app.models.enums import CallStatus
from app.schemas.call import CallCreate, CallOut
from app.services import call as call_service
from app.services.call_orchestrator import CallOrchestrator
from app.services.lead import DNCError

router = APIRouter(
    prefix="/calls",
    tags=["calls"],
    dependencies=[Depends(require_admin_api_key)],
)

class TransitionPayload(BaseModel):
    """Payload to drive a call through its lifecycle (Phase 4)."""

    status: CallStatus
    provider_call_id: str | None = Field(default=None, max_length=100)
    provider_session_id: str | None = Field(default=None, max_length=100)
    failure_reason: str | None = Field(default=None, max_length=255)
    recording_url: str | None = None


class SimulatedTurnOut(BaseModel):
    index: int
    user_text: str = ""
    reply: str = ""
    state: str | None = None
    terminal: bool = False
    used_llm: bool = False


class CostLedgerOut(BaseModel):
    currency: str
    stt: float
    tts: float
    llm: float
    telephony: float
    total: float
    components: dict[str, float]


class SimulateCallOut(BaseModel):
    call_id: str
    session_id: str | None = None
    provider: str
    turns: list[SimulatedTurnOut]
    final_call_status: str | None = None
    final_lead_status: str | None = None
    site_visit_id: str | None = None
    duration_seconds: int
    cost: CostLedgerOut
    simulated: bool

    @classmethod
    def from_result(cls, r) -> "SimulateCallOut":
        return cls(
            call_id=r.call_id,
            session_id=r.session_id,
            provider=r.provider,
            turns=[
                SimulatedTurnOut(
                    index=t.index,
                    user_text=t.user_text,
                    reply=t.reply,
                    state=t.state,
                    terminal=t.terminal,
                    used_llm=t.used_llm,
                )
                for t in r.turns
            ],
            final_call_status=r.final_call_status,
            final_lead_status=r.final_lead_status,
            site_visit_id=r.site_visit_id,
            duration_seconds=r.duration_seconds,
            cost=CostLedgerOut(
                currency=r.cost.currency,
                stt=r.cost.stt,
                tts=r.cost.tts,
                llm=r.cost.llm,
                telephony=r.cost.telephony,
                total=r.cost.total,
                components=r.cost.components,
            ),
            simulated=r.simulated,
        )


@router.post("", response_model=CallOut, status_code=status.HTTP_201_CREATED)
async def create_call(
    payload: CallCreate,
    db: AsyncSession = Depends(get_db),
) -> "CallOut":
    """Create a queued outbound call for a lead."""
    try:
        call = await call_service.create_call(db, payload)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except DNCError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return CallOut.model_validate(call)


@router.get("", response_model=None)
async def list_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: CallStatus | None = Query(None, alias="status"),
    lead_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List calls with optional filters."""
    rows, total = await call_crud.list_calls(
        db,
        page=page,
        page_size=page_size,
        status=status_filter.value if status_filter else None,
        lead_id=lead_id,
    )
    return {
        "items": [CallOut.model_validate(r).model_dump(mode="json") for r in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": math.ceil(total / page_size) if total else 0,
    }


@router.get("/{call_id}", response_model=CallOut)
async def get_call(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> "CallOut":
    """Fetch a single call."""
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    return CallOut.model_validate(call)


@router.post("/{call_id}/transition", response_model=CallOut)
async def transition_call(
    call_id: uuid.UUID,
    payload: TransitionPayload,
    db: AsyncSession = Depends(get_db),
) -> "CallOut":
    """Advance a call to a new status via the state machine.

    In Phase 4 this drives the lifecycle deterministically (mock telephony).
    In Phase 10 Plivo webhooks will call the same transition service.
    """
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")

    set_fields = None
    if payload.provider_call_id or payload.provider_session_id or payload.failure_reason or payload.recording_url:
        from app.schemas.call import CallUpdate

        set_fields = CallUpdate(
            provider_call_id=payload.provider_call_id,
            provider_session_id=payload.provider_session_id,
            failure_reason=payload.failure_reason,
            recording_url=payload.recording_url,
        )

    try:
        call = await call_service.transition_call(db, call, payload.status, set_fields=set_fields)
    except call_service.IllegalTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return CallOut.model_validate(call)


@router.get("/{call_id}/events")
async def get_call_events(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return the lifecycle events for a call."""
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    events = await event_crud.list_events_for_call(db, call_id)
    return [
        {
            "id": str(e.id),
            "event_type": e.event_type,
            "occurred_at": e.occurred_at.isoformat(),
            "data": e.data,
        }
        for e in events
    ]


class SimulateRequest(BaseModel):
    """Optional request body for the Phase 12 call simulation."""

    script: list[str] | None = Field(default=None, max_length=30)


@router.post("/{call_id}/simulate", response_model=SimulateCallOut)
async def simulate_call(
    call_id: uuid.UUID,
    payload: SimulateRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> "SimulateCallOut":
    """Run a complete outbound call end-to-end.

    Always zero-cost: simulation forces the mock telephony/STT/TTS/LLM
    providers REGARDLESS of server configuration, so this endpoint can
    never place a real call or spend a paisa - even on a live-configured
    server. (An earlier revision used registry-selected providers, which
    really dialled out when the server ran with live providers.)
    """
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    if call.status != CallStatus.QUEUED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"call must be queued to simulate (current: {call.status})",
        )

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.providers.llm.mock import MockLLMProvider
    from app.providers.stt.mock import MockSTTProvider
    from app.providers.telephony.mock import MockTelephonyProvider
    from app.providers.tts.mock import MockTTSProvider

    factory = async_sessionmaker(
        bind=db.bind, class_=AsyncSession, expire_on_commit=False
    )
    orchestrator = CallOrchestrator(
        session_factory=factory,
        telephony=MockTelephonyProvider(),
        stt=MockSTTProvider(),
        tts=MockTTSProvider(),
        llm=MockLLMProvider(),
    )
    result = await orchestrator.run_simulated_call(
        db, call, script=payload.script if payload else None
    )
    return SimulateCallOut.from_result(result)


@router.get("/{call_id}/transcript.txt")
async def get_transcript_txt(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    """Download the call transcript as a plain-text file."""
    messages = await _transcript_messages(db, call_id)
    if not messages:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no transcript for call")
    lines = [f"{m.timestamp.isoformat()} | {m.speaker.upper()}: {m.text}" for m in messages]
    body = "\n".join(lines) + "\n"
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="call-{call_id}.txt"'},
    )


@router.get("/{call_id}/transcript.json")
async def get_transcript_json(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Download the call transcript as structured JSON."""
    messages = await _transcript_messages(db, call_id)
    if not messages:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no transcript for call")
    return JSONResponse(
        {
            "call_id": str(call_id),
            "messages": [
                {
                    "id": str(m.id),
                    "speaker": m.speaker,
                    "text": m.text,
                    "state": m.state,
                    "timestamp": m.timestamp.isoformat(),
                }
                for m in messages
            ],
        },
        headers={"Content-Disposition": f'attachment; filename="call-{call_id}.json"'},
    )


@router.get("/{call_id}/recording")
async def get_call_recording(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return recording availability/status for a call.

    Handles available / unavailable / processing / failed gracefully rather
    than assuming a recording always exists (Plivo recordings may be pending).
    """
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    url = call.recording_url
    if url:
        return {
            "call_id": str(call_id),
            "status": "available",
            "recording_url": url,
            "recording_enabled": call.recording_enabled,
        }
    if call.recording_enabled:
        return {
            "call_id": str(call_id),
            "status": "processing",
            "recording_url": None,
            "recording_enabled": True,
        }
    return {
        "call_id": str(call_id),
        "status": "unavailable",
        "recording_url": None,
        "recording_enabled": False,
        "detail": "recording not enabled/available for this call",
    }


async def _transcript_messages(db: AsyncSession, call_id: uuid.UUID):
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")
    messages = await conv_crud.list_messages_for_call(db, call_id)
    return messages


@router.get("/{call_id}/cost")
async def get_call_cost(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return the persisted per-component cost records for a call (Phase 15).

    Populated every time the call is simulated via the orchestrator, so costs
    stay auditable after the fact even though they are free in MOCK_MODE.
    """
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")

    from app.services import cost as cost_service

    records = await cost_service.get_records_for_call(db, call_id)
    summary = await cost_service.summary_for_call(db, call_id)
    return {
        "call_id": str(call_id),
        "currency": summary.currency,
        "components": summary.components,
        "total": summary.total,
        "records": records,
    }


@router.get("/{call_id}/requirements")
async def get_call_requirements(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return the property-requirement snapshot captured for a call.

    Mirrors the LeadRequirement row(s) upserted by the orchestrator / live
    outcome path so the dashboard call-detail view can render the extracted
    qualification (property type, BHK, location, budget, purpose, timeline).
    """
    call = await call_crud.get_call(db, call_id)
    if call is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="call not found")

    from sqlalchemy import select

    from app.models.lead_requirement import LeadRequirement

    stmt = (
        select(LeadRequirement)
        .where(LeadRequirement.call_id == str(call_id))
        .order_by(LeadRequirement.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "call_id": str(call_id),
        "lead_id": call.lead_id,
        "requirements": [
            {
                "id": str(r.id),
                "property_type": r.property_type,
                "bhk": r.bhk,
                "location": r.location,
                "city": r.city,
                "budget_min": r.budget_min,
                "budget_max": r.budget_max,
                "budget_currency": r.budget_currency,
                "budget_raw": r.budget_raw,
                "purpose": r.purpose,
                "timeline": r.timeline,
                "lead_score": r.lead_score,
                "qualification_reason": r.qualification_reason,
            }
            for r in rows
        ],
    }
