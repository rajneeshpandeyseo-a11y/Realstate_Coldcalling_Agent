"""Call orchestrator - runs an outbound call end-to-end (PHASE 12).

The orchestrator is the single entry point that drives a complete call through
all four provider abstractions (telephony -> STT -> conversation engine (+LLM
fallback) -> TTS) and the call state machine, persisting everything.

Cost-first principle: every provider call carries a `CostEstimate`, which the
orchestrator aggregates into a per-call cost ledger so each simulated call
prints the same shape of bill a real call will produce.

Zero-cost & deterministic by default
------------------------------------
All providers are selected through `app/providers/registry`. In `MOCK_MODE`
the registry returns `MockTelephonyProvider`, `MockSTTProvider`,
`MockTTSProvider` and `MockLLMProvider`, all of which are local-only (no
network, no keys, no balance, no KYC, no real outbound call). The simulation
is therefore fully reproducible and free.

Switching to a real call later
------------------------------
No code changes are required to go live: flip the provider settings
(`TELEPHONY_PROVIDER=plivo`, `STT_PROVIDER=sarvam`, `TTS_PROVIDER=sarvam`,
`LLM_PROVIDER=gemini`, `MOCK_MODE=false`) and the same orchestrator code path
places a real Plivo call and runs the real conversation. The interfaces and the
orchestration are identical; only the configuration changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.conversation.engine import ConversationEngine
from app.conversation.states import ConvState
from app.db.session import AsyncSessionLocal
from app.logging_config import get_logger
from app.models.call import Call
from app.models.enums import CallStatus
from app.providers import (
    LLMProvider,
    STTProvider,
    TelephonyProvider,
    TTSProvider,
    get_llm_provider,
    get_stt_provider,
    get_telephony_provider,
    get_tts_provider,
)
from app.services import call as call_service
from app.services.live_agent import LiveAgentSession

log = get_logger("app.services.call_orchestrator")

# Persona/facts used to behave alongside the conversation engine.
_DEFAULT_PERSONA = {
    "company": "Creatik AI",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
        "Skyline Residency, Greater Noida": "2/3 BHK, Rs. 35 lakh se shuru",
        "Green Valley, Gurgaon": "3/4 BHK, Rs. 1 crore se shuru",
    },
    "project_location": "Noida, Greater Noida aur Gurgaon",
}

# A realistic, deterministic Hinglish call following the approved example
# transcript: identity -> permission -> type -> location -> budget ->
# summary confirm -> purpose -> timeline -> present -> visit date/time ->
# booked site visit.
SCRIPT_INTERESTED: tuple[str, ...] = (
    "haan, main interested hoon",
    "haan, batao",
    "3 bhk",
    "noida",
    "40 lakh",
    "haan",
    "rehne ke liye",
    "jaldi",
    "haan",
    "shanivaar",
    "subah",
)

# A deterministic call that ends with the caller asking to not be called again.
SCRIPT_DO_NOT_CALL: tuple[str, ...] = (
    "mujhe call mat karo",
)

# A deterministic call where the customer asks to be called back later (Phase 14).
SCRIPT_CALLBACK: tuple[str, ...] = (
    "haan, main interested hoon",
    "haan, batao",
    "3 bhk",
    "noida",
    "40 lakh",
    "haan",
    "rehne ke liye",
    "jaldi",
    "haan",
    "phir call karna",
    "kal shaam",
)


@dataclass
class SimulatedTurn:
    """One turn of a simulated call (customer utterance -> agent reply)."""

    index: int
    user_text: str
    reply: str
    state: str | None = None
    terminal: bool = False
    used_llm: bool = False
    llm_cost: float = 0.0


@dataclass
class CostLedger:
    """Summary of estimated costs for one call."""

    currency: str = "INR"
    stt: float = 0.0
    tts: float = 0.0
    llm: float = 0.0
    telephony: float = 0.0
    total: float = 0.0
    components: dict[str, float] = field(default_factory=dict)


@dataclass
class SimulatedCall:
    """Full outcome of running an end-to-end simulated call."""

    call_id: str
    session_id: str | None
    provider: str
    turns: list[SimulatedTurn] = field(default_factory=list)
    final_call_status: str | None = None
    final_lead_status: str | None = None
    site_visit_id: str | None = None
    duration_seconds: int = 0
    cost: CostLedger = field(default_factory=CostLedger)
    simulated: bool = True


class CallOrchestrator:
    """Drives a complete outbound call through the provider stack + engine.

    Providers are injectable but default to the registry-selected instances
    (mocks in `MOCK_MODE`), so it always runs cost-free during dev/test and can
    be pointed at real providers purely via configuration.
    """

    def __init__(
        self,
        *,
        telephony: TelephonyProvider | None = None,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        llm: LLMProvider | None = None,
        engine: ConversationEngine | None = None,
        session_factory: async_sessionmaker | None = None,
        persona: dict | None = None,
    ):
        self.telephony = telephony or get_telephony_provider()
        self.stt = stt or get_stt_provider()
        self.tts = tts or get_tts_provider()
        self.llm = llm or get_llm_provider()
        self.engine = engine or ConversationEngine()
        self.session_factory = session_factory or AsyncSessionLocal
        self.persona = persona or dict(_DEFAULT_PERSONA)

    async def place_outbound(self, db: AsyncSession, call: Call) -> None:
        """Place (or in mock, simulate placing) the outbound call.

        Delegates to the state machine's INITIATED transition which calls
        ``place_outbound_call`` using the configured telephony provider (the
        mock in MOCK_MODE; Plivo once configured).
        """
        await call_service.transition_call(
            db,
            call,
            CallStatus.INITIATED,
            event_data={
                "source": "orchestrator",
                "provider": self.telephony.name(),
            },
        )

    async def run_simulated_call(
        self,
        db: AsyncSession,
        call: Call,
        *,
        script: Sequence[str] | None = None,
    ) -> SimulatedCall:
        """Run a complete outbound call end-to-end deterministically.

        Steps (all local, no real network in MOCK_MODE):
          1. Place the outbound call (-> INITIATED, sets provider id).
          2. Caller answers (-> IN_PROGRESS).
          3. Replay the scripted conversation through the live-agent turn loop
             (same `LiveAgentSession` the Phase 11 WebSocket uses) - the
             deterministic, cost-free stand-in for real STT -> engine -> TTS.
          4. Terminate the call (COMPLETED or DO_NOT_CALL) via the state
             machine so lead/call side-effects are applied.
          5. Compute the cost ledger + transcript and return a full report.
        """
        # 1. Place outbound.
        await self.place_outbound(db, call)
        await db.commit()

        # 2. Answer: the state machine walks INITIATED -> RINGING -> ANSWERED
        #    -> IN_PROGRESS. We drive the same transitions the Plivo webhook
        #    would produce for a real call.
        for status in (CallStatus.RINGING, CallStatus.ANSWERED, CallStatus.IN_PROGRESS):
            await call_service.transition_call(
                db, call, status, event_data={"source": "orchestrator", "simulated": True}
            )
        await db.commit()

        # 3. Replay the conversation through the same live-agent pipeline.
        turns: list[SimulatedTurn] = []
        agent = LiveAgentSession(
            call.id,
            session_factory=self.session_factory,
            engine=self.engine,
            persona=self.persona,
            stt=self.stt,
            tts=self.tts,
        )
        greeting = await agent.start()
        if greeting.audio:
            turns.append(
                SimulatedTurn(
                    index=0,
                    user_text="",
                    reply=greeting.reply or "",
                    state="GREETING",
                )
            )

        lines = list(script) if script is not None else list(SCRIPT_INTERESTED)
        terminal_hit = False
        visit_agreed = False
        booking_text = ""
        callback_reached = False
        callback_text = ""
        for i, line in enumerate(lines, start=1):
            # Scripted repeats are intentional - never dedupe here (live WS
            # path keeps dedupe=True for jitter/tail duplicates).
            turn = await agent.handle_user_text(line, dedupe=False)
            turns.append(
                SimulatedTurn(
                    index=i,
                    user_text=line,
                    reply=turn.reply or "",
                    state=turn.state,
                    terminal=turn.terminal,
                    used_llm=turn.used_llm,
                    llm_cost=turn.llm_cost,
                )
            )
            # Reaching VISIT_BOOKING means the customer agreed to a site visit.
            if turn.state == ConvState.VISIT_BOOKING.value:
                visit_agreed = True
            # Reaching CALLBACK means the customer asked to be called back.
            if turn.state == ConvState.CALLBACK.value:
                callback_reached = True
            # The last utterance the callback stage is the preferred calling time.
            if callback_reached and turn.state in (
                ConvState.CALLBACK.value,
                ConvState.END.value,
            ) and line.strip():
                callback_text = line
            # The last utterance at/after the booking stage carries the date/time.
            if visit_agreed and turn.state in (
                ConvState.VISIT_BOOKING.value,
                ConvState.END.value,
            ) and line.strip():
                booking_text = line
            if turn.terminal:
                terminal_hit = True
                break

        await db.commit()

        # 4. Terminate through the state machine.
        final_call_status: CallStatus
        event_data: dict = {"source": "orchestrator", "simulated": True}
        if terminal_hit and turns[-1].state == ConvState.DO_NOT_CALL.value:
            final_call_status = CallStatus.DO_NOT_CALL
        elif callback_reached:
            final_call_status = CallStatus.CALLBACK_REQUESTED
            if callback_text:
                event_data["notes"] = f"callback time: {callback_text}"
        else:
            final_call_status = CallStatus.COMPLETED
        await call_service.transition_call(
            db,
            call,
            final_call_status,
            event_data=event_data,
        )
        await db.commit()
        await db.refresh(call)

        # 5. If the customer agreed to a site visit, persist it (Phase 13).
        site_visit_id = None
        if (
            visit_agreed
            and final_call_status == CallStatus.COMPLETED
            and call.lead_id is not None
        ):
            from app.services.site_visit import create_booking_for_call

            # Pull the chosen property (and any other slots) from the live
            # conversation session so the visit record captures which property
            # the customer was interested in (monitoring/CRM).
            session_slots: dict = {}
            if agent.session_id is not None:
                from app.crud.conversation import get_session as get_conv_session
                from app.crud.conversation import load_slots as load_conv_slots

                conv_session = await get_conv_session(db, agent.session_id)
                if conv_session is not None:
                    session_slots = await load_conv_slots(conv_session)

            property_name = session_slots.get("property")
            visit_location = self.persona.get("project_location")
            visit = await create_booking_for_call(
                db,
                lead_id=call.lead_id,
                call_id=call.id,
                booking_text=booking_text,
                slots=session_slots,
                location=property_name or visit_location,
            )
            site_visit_id = str(visit.id)
            await db.commit()
            await db.refresh(call)

        # 6. Cost ledger (Phase 15): aggregate every component deterministically.
        duration_seconds = max(15, len(lines) * 4)
        minutes = duration_seconds / 60.0
        telephony_amount = self._telephony_rate() * minutes
        stt_cost = agent.cost.get("stt", 0.0)
        tts_cost = agent.cost.get("tts", 0.0)
        # LLM cost is tracked per conversation turn (TurnResult.llm_cost), which
        # the engine set if and only if the LLM fallback actually fired.
        llm_cost = round(sum(float(t.llm_cost) for t in turns), 8)
        ledger = CostLedger(
            currency="INR",
            stt=round(stt_cost, 8),
            tts=round(tts_cost, 8),
            telephony=round(telephony_amount, 8),
            llm=llm_cost,
        )
        ledger.components = {
            "stt": ledger.stt,
            "tts": ledger.tts,
            "llm": ledger.llm,
            "telephony": ledger.telephony,
        }
        ledger.total = round(
            ledger.stt + ledger.tts + ledger.llm + ledger.telephony, 8
        )

        # Persist one CostRecord per component for auditability (Phase 15).
        from app.services import cost as cost_service

        cost_records = await cost_service.persist_ledger(db, call.id, ledger)
        await db.commit()
        await db.refresh(call)

        lead_status = None
        if call.lead_id is not None:
            from app.crud import lead as lead_crud

            lead = await lead_crud.get_lead(db, call.lead_id)
            lead_status = lead.status.value if lead is not None else None

        log.info(
            "simulated call complete",
            extra={
                "ctx": {
                    "call_id": str(call.id),
                    "final_status": call.status,
                    "turns": len(turns),
                    "cost": ledger.total,
                }
            },
        )

        return SimulatedCall(
            call_id=str(call.id),
            session_id=str(agent.session_id) if agent.session_id else None,
            provider=self.telephony.name(),
            turns=turns,
            final_call_status=call.status,
            final_lead_status=lead_status,
            site_visit_id=site_visit_id,
            duration_seconds=duration_seconds,
            cost=ledger,
        )

    def _telephony_rate(self) -> float:
        """Per-minute telephony rate (estimated; provider may refine)."""
        rate = getattr(self.telephony, "per_minute_rate", None)
        if rate is not None:
            return float(rate)
        try:
            return float(settings.PLIVO_COST_PER_MINUTE or 0.0)
        except Exception:
            return 0.0
