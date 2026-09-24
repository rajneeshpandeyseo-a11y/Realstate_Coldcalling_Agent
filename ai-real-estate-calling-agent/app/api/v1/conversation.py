"""Conversation engine endpoints - /api/v1/conversation.

Exposes the deterministic Hinglish engine over HTTP so it can be exercised
independently of telephony (Phase 5). Real calls wire this into the call
WebSocket in later phases.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin_api_key
from app.conversation.states import ConvState
from app.crud import conversation as conv_crud
from app.db.session import get_db
from app.models.enums import ConversationSpeaker
from app.services.conversation import ConversationService

router = APIRouter(
    prefix="/conversation",
    tags=["conversation"],
    dependencies=[Depends(require_admin_api_key)],
)

_persona = {
    "company": "Creatik AI",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
        "Skyline Residency, Greater Noida": "2/3 BHK, Rs. 35 lakh se shuru",
        "Green Valley, Gurgaon": "3/4 BHK, Rs. 1 crore se shuru",
    },
}


def _service() -> ConversationService:
    from app.conversation.engine import ConversationEngine, ProviderLLMFallback

    engine = ConversationEngine(llm=ProviderLLMFallback())
    return ConversationService(engine)


class StartRequest(BaseModel):
    call_id: uuid.UUID
    language: str = "hi-Latn"


class TurnRequest(BaseModel):
    session_id: uuid.UUID
    call_id: uuid.UUID
    text: str = Field(min_length=1, max_length=2000)


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def start_session(
    payload: StartRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a conversation session and return the agent's greeting."""
    svc = _service()
    sid = await svc.new_session(
        db, call_id=payload.call_id, language=payload.language, persona=_persona
    )
    messages = await conv_crud.list_messages(db, sid)
    return {
        "session_id": str(sid),
        "state": "_GREETING",
        "messages": [_msg_json(m) for m in messages],
    }


@router.post("/turn")
async def process_turn(
    payload: TurnRequest,
    db: AsyncSession = Depends(get_db),
):
    """Process a user utterance and return the agent's next reply."""
    svc = _service()
    try:
        result = await svc.process_turn(
            db,
            session_id=payload.session_id,
            call_id=payload.call_id,
            user_text=payload.text,
            persona=_persona,
        )
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    session = await conv_crud.get_session(db, payload.session_id)
    return {
        "state": result.state.value,
        "reply": result.reply,
        "terminated": result.termination,
        "changed_state": result.changed_state,
        "slots": result.slots,
        "stored_slots": session.collected_data,
        "messages": [_msg_json(m) for m in await conv_crud.list_messages(db, payload.session_id)],
    }


@router.get("/sessions/{session_id}")
async def get_session_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return all messages for a session."""
    session = await conv_crud.get_session(db, session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="session not found")
    messages = await conv_crud.list_messages(db, session_id)
    return {
        "session_id": str(session.id),
        "state": session.state,
        "messages": [_msg_json(m) for m in messages],
    }


def _msg_json(m) -> dict:
    return {
        "id": str(m.id),
        "speaker": m.speaker,
        "text": m.text,
        "state": m.state,
        "timestamp": m.timestamp.isoformat(),
    }
