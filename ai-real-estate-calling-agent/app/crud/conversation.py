"""CRUD for conversation sessions and messages."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation_message import ConversationMessage
from app.models.conversation_session import ConversationSession
from app.models.enums import ConversationSpeaker


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> ConversationSession | None:
    stmt = select(ConversationSession).where(ConversationSession.id == session_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_sessions_for_call(
    db: AsyncSession, call_id: uuid.UUID
) -> list[ConversationSession]:
    """All conversation sessions for a call, newest first."""
    stmt = (
        select(ConversationSession)
        .where(ConversationSession.call_id == str(call_id))
        .order_by(ConversationSession.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def create_session(
    db: AsyncSession,
    *,
    call_id: uuid.UUID | None,
    language: str = "hi-Latn",
) -> ConversationSession:
    session = ConversationSession(
        call_id=str(call_id) if call_id else None,
        state="GREETING",
        language=language,
        collected_data="{}",
    )
    db.add(session)
    await db.flush()
    return session


async def save_state(db: AsyncSession, session: ConversationSession, state: str, slots: dict) -> None:
    session.state = state
    session.collected_data = json.dumps(slots)
    await db.flush()


async def load_slots(session: ConversationSession) -> dict:
    try:
        return json.loads(session.collected_data or "{}")
    except (TypeError, ValueError):
        return {}


async def add_message(
    db: AsyncSession,
    *,
    call_id: uuid.UUID,
    session_id: uuid.UUID | None,
    speaker: ConversationSpeaker,
    text: str,
    state: str | None = None,
    latency_ms: int | None = None,
) -> ConversationMessage:
    msg = ConversationMessage(
        call_id=str(call_id),
        session_id=str(session_id) if session_id else None,
        speaker=speaker.value,
        text=text,
        timestamp=datetime.now(timezone.utc),
        state=state,
        latency_ms=latency_ms,
    )
    db.add(msg)
    await db.flush()
    return msg


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> list[ConversationMessage]:
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.session_id == str(session_id))
        .order_by(ConversationMessage.timestamp.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_messages_for_call(db: AsyncSession, call_id: uuid.UUID) -> list[ConversationMessage]:
    """All transcript messages for a call (across sessions), chronological."""
    stmt = (
        select(ConversationMessage)
        .where(ConversationMessage.call_id == str(call_id))
        .order_by(ConversationMessage.timestamp.asc())
    )
    return list((await db.execute(stmt)).scalars().all())
