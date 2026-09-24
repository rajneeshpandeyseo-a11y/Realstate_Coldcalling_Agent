"""Conversation orchestration service: engine + persistence + slots."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.conversation.engine import ConversationEngine, TurnResult
from app.conversation.states import ConvState
from app.crud import conversation as conv_crud
from app.models.enums import ConversationSpeaker


def _state_from_str(value: str) -> ConvState:
    try:
        return ConvState(value)
    except ValueError:
        return ConvState.GREETING


async def _count_reprompts(db: AsyncSession, session_id: uuid.UUID, state: str) -> int:
    """Consecutive agent turns already spent in `state` (PHASE 1.4).

    Walks the transcript backwards counting agent messages tagged with this
    state; stops at the first agent message from a different state. Fresh
    states yield 0 (primary phrasing), repeats yield 1, 2, ... and the
    engine rotates alternates via modulo. Best-effort: any failure -> 0.
    """
    try:
        messages = await conv_crud.list_messages(db, session_id)
    except Exception as exc:
        from app.logging_config import get_logger

        get_logger("app.services.conversation").debug(
            "reprompt count failed, assuming 0", extra={"ctx": {"error": str(exc)}}
        )
        return 0
    count = 0
    for msg in reversed(messages):
        if getattr(msg, "speaker", None) != ConversationSpeaker.AGENT.value:
            continue
        if (getattr(msg, "state", None) or "") != state:
            break
        count += 1
    return count


class ConversationService:
    """Handles turning a user utterance into a persisted reply turn."""

    def __init__(self, engine: ConversationEngine):
        self.engine = engine

    async def new_session(
        self,
        db: AsyncSession,
        *,
        call_id: uuid.UUID,
        language: str = "hi-Latn",
        persona: dict | None = None,
        caller_name: str | None = None,
    ) -> uuid.UUID:
        """Create a conversation session for a call and post the greeting."""
        session = await conv_crud.create_session(db, call_id=call_id, language=language)
        result = await self.engine.step(
            state=ConvState.GREETING,
            first_turn=True,
            persona=persona,
            caller_name=caller_name,
        )
        await conv_crud.add_message(
            db,
            call_id=call_id,
            session_id=session.id,
            speaker=ConversationSpeaker.AGENT,
            text=result.reply,
            state=result.state.value,
        )
        return session.id

    async def process_turn(
        self,
        db: AsyncSession,
        *,
        session_id: uuid.UUID,
        call_id: uuid.UUID,
        user_text: str,
        persona: dict | None = None,
        caller_name: str | None = None,
    ) -> TurnResult:
        """Handle one user utterance and return the agent's next reply."""
        session = await conv_crud.get_session(db, session_id)
        if session is None:
            raise KeyError(f"session {session_id} not found")

        current_state = _state_from_str(session.state)
        slots = await conv_crud.load_slots(session)

        # persist the user's message first
        await conv_crud.add_message(
            db,
            call_id=call_id,
            session_id=session.id,
            speaker=ConversationSpeaker.CUSTOMER,
            text=user_text,
            state=current_state.value,
        )

        # PHASE 1.4: count how many times the current question was already
        # asked (trailing agent turns in this same state) so the engine can
        # rotate alternate phrasings instead of repeating the exact line.
        reprompt = await _count_reprompts(db, session.id, current_state.value)

        result = await self.engine.step(
            state=current_state,
            slots=slots,
            user_input=user_text,
            persona=persona,
            caller_name=caller_name,
            reprompt=reprompt,
        )

        # persist agent reply + updated state/slots
        await conv_crud.add_message(
            db,
            call_id=call_id,
            session_id=session.id,
            speaker=ConversationSpeaker.AGENT,
            text=result.reply,
            state=result.state.value,
        )
        await conv_crud.save_state(db, session, result.state.value, result.slots or slots)
        return result
