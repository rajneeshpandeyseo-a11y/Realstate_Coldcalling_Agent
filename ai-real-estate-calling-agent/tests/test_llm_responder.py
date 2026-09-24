"""Tests for the LLM intent-fallback + grounded responder hooks.

Uses fakes (no network, no paid calls): the hooks fire ONLY on ambiguous /
out-of-scope input, so every deterministic turn stays free and unchanged.
"""

import uuid

import pytest

from app.conversation.engine import (
    AnswerResult,
    ConversationEngine,
    IntentResult,
    LLMFallback,
    LLMResponder,
)
from app.conversation.intents import Intent
from app.conversation.states import ConvState
from app.services.live_agent import LiveAgentSession

PERSONA = {
    "company": "Creatik AI",
    "agent_name": "Neha",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
    },
}


class FakeLLM(LLMFallback):
    """Deterministic stand-in for the Gemini intent fallback."""

    def __init__(self, intent: Intent, confidence: float = 0.8, slots: dict | None = None):
        self._intent = intent
        self._confidence = confidence
        self._slots = slots or {}

    async def classify(self, text: str, state: str) -> IntentResult:
        return IntentResult(self._intent, self._confidence, dict(self._slots))


class FakeResponder(LLMResponder):
    """Deterministic stand-in for the grounded answer generator."""

    def __init__(self, text: str | None = None, exc: Exception | None = None):
        self._text = text
        self._exc = exc
        self.calls = 0

    async def answer(self, text, state, persona, pending_question):
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        if self._text is None:
            return None
        return AnswerResult(self._text, 0.01)


@pytest.mark.asyncio
async def test_llm_fallback_flips_ambiguous_permission_answer():
    engine = ConversationEngine(llm=FakeLLM(Intent.CONFIRMATION))
    r = await engine.step(
        state=ConvState.PERMISSION, user_input="xyz jabberwocky",
        persona=PERSONA,
    )
    # Gibberish is OTHER by rules; the LLM reads intent -> flow continues.
    assert r.state == ConvState.QUALIFICATION
    assert r.used_llm is True


@pytest.mark.asyncio
async def test_responder_answers_unexpected_question_in_words():
    engine = ConversationEngine(
        responder=FakeResponder("Ji, main Noida se bol rahi hoon. " + "Aapko kaunsa layout chahiye?")
    )
    r = await engine.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk", "location": "Noida"},
        user_input="aap kahan se ho", persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "Noida se bol rahi" in r.reply
    assert "Kshama" not in r.reply
    assert r.used_llm is True
    assert r.llm_cost == 0.01


@pytest.mark.asyncio
async def test_responder_failure_falls_back_to_canned_sorry():
    engine = ConversationEngine(responder=FakeResponder(exc=RuntimeError("down")))
    r = await engine.step(
        state=ConvState.QUALIFICATION, user_input="aap kahan se ho",
        persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "Kshama" in r.reply
    assert r.used_llm is False


@pytest.mark.asyncio
async def test_responder_none_result_falls_back_to_canned():
    engine = ConversationEngine(responder=FakeResponder(text=None))
    r = await engine.step(
        state=ConvState.CLOSING, slots={"preferred_date": "sunday"},
        user_input="tumhari salary kitni hai", persona=PERSONA,
    )
    assert r.state in (ConvState.CLOSING, ConvState.VISIT_BOOKING)
    # Either a grounded fallback or the visit flow continues - never a crash.
    assert isinstance(r.reply, str) and r.reply


@pytest.mark.asyncio
async def test_permission_other_uses_responder_then_continues():
    engine = ConversationEngine(responder=FakeResponder("Ji bilkul. Is this a good time?"))
    r = await engine.step(
        state=ConvState.PERMISSION, user_input="tum kaun ho",
        persona=PERSONA, caller_name="Sourabh",
    )
    assert r.state == ConvState.PERMISSION
    assert "Ji bilkul" in r.reply


@pytest.mark.asyncio
async def test_blank_text_never_calls_responder():
    responder = FakeResponder("should not be used")
    engine = ConversationEngine(responder=responder)
    r = await engine.step(state=ConvState.QUALIFICATION, user_input="   ", persona=PERSONA)
    assert responder.calls == 0
    # Blank/mumble gets the short warm re-ask (no robotic full repeat).
    assert "aawaz thodi kat gayi" in r.reply
    assert "kis type ki property" in r.reply


def test_live_session_in_mock_mode_has_no_hooks(db_sessionmaker):
    """Mock/test loop stays fully deterministic: no LLM wiring by default."""
    agent = LiveAgentSession(uuid.uuid4(), session_factory=db_sessionmaker)
    assert agent.engine.llm is None
    assert agent.engine.responder is None


@pytest.mark.asyncio
async def test_llm_slots_fill_gaps_rules_missed():
    """LLM-extracted slots merge in for keys the rules left empty."""
    engine = ConversationEngine(
        llm=FakeLLM(Intent.TIMELINE, 0.8, {"timeline": "2 months"})
    )
    result = await engine._detect("xyz jabberwocky", ConvState.QUALIFICATION.value)
    assert result.intent == Intent.TIMELINE
    assert result.slots.get("timeline") == "2 months"
    assert result.used_llm is True


@pytest.mark.asyncio
async def test_llm_location_slot_is_never_trusted():
    """A fabricated LLM location must not override/corrupt rule slots."""
    engine = ConversationEngine(
        llm=FakeLLM(Intent.LOCATION, 0.8, {"location": "Sunrise Heights"})
    )
    from app.conversation.engine import _persona_property_names

    result = await engine._detect(
        "sunrise heights",
        ConvState.QUALIFICATION.value,
        _persona_property_names(PERSONA),
    )
    assert "location" not in result.slots


@pytest.mark.asyncio
async def test_llm_location_accepted_when_rules_missed_and_not_project():
    """Slot-skipping fix: rules-missed locality (e.g. bare 'Gopalpura') is
    accepted from the LLM so its question is never re-asked."""
    engine = ConversationEngine(
        llm=FakeLLM(Intent.LOCATION, 0.8, {"location": "Gopalpura"})
    )
    from app.conversation.engine import _persona_property_names

    result = await engine._detect(
        "gopalpura",
        ConvState.QUALIFICATION.value,
        _persona_property_names(PERSONA),
    )
    assert result.slots.get("location") == "Gopalpura"


@pytest.mark.asyncio
async def test_multi_slot_answer_never_reasks_filled_slot():
    """Rules-missed locality on repeat ('Gopalpura') is LLM-filled so the
    location question is never asked twice for the same answer."""
    engine = ConversationEngine(
        llm=FakeLLM(Intent.LOCATION, 0.8, {"location": "Gopalpura"})
    )
    r = await engine.step(
        state=ConvState.QUALIFICATION,
        slots={"bhk": "2bhk", "property_type": "2bhk", "budget": "50 lakh"},
        user_input="Gopalpura",
        persona=PERSONA,
    )
    assert r.slots.get("location") == "Gopalpura"
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "Correct?" in r.reply


# ------------------------------------------------- PHASE 1.2: side-questions


class StubLLMProvider:
    """Minimal stub for ProviderLLMResponder (no network)."""

    def __init__(self, text: str = "", name: str = "gemini"):
        self._text = text
        self._name = name
        self.last_prompt = ""
        self.last_system: str | None = None

    def name(self) -> str:
        return self._name

    async def complete(self, prompt: str, *, system=None, max_tokens=512, temperature=0.2):
        from app.providers.base import CostEstimate, LLMResult

        self.last_prompt = prompt
        self.last_system = system
        return LLMResult(
            text=self._text,
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            cost=CostEstimate(currency="USD", component="llm", amount=0.01),
            provider=self._name,
        )


def _provider_responder(stub: StubLLMProvider):
    from app.conversation.engine import ProviderLLMResponder

    return ProviderLLMResponder(provider=stub)


@pytest.mark.asyncio
async def test_provider_responder_company_side_question_grounded_plus_pending():
    """'aap kaunsi company se ho' -> company + pending question, short."""
    pending = "Aapko kaunsa layout chahiye - 2, 3 ya 4 BHK?"
    stub = StubLLMProvider(
        "Ji, main Creatik AI se Neha bol rahi hoon. " + pending
    )
    r = await _provider_responder(stub).answer(
        "aap kaunsi company se ho", ConvState.QUALIFICATION.value, PERSONA, pending
    )
    assert r is not None
    assert "Creatik AI" in r.text
    assert pending in r.text
    assert len(r.text.split()) <= 60
    # Prompt carries grounding + feminine instruction.
    assert "Creatik AI" in stub.last_prompt and "Neha" in stub.last_prompt
    assert "bol rahi hoon" in (stub.last_system or "")


@pytest.mark.asyncio
async def test_provider_responder_price_side_question_no_invention_plus_pending():
    """'price kitna hai' -> no invented price, WhatsApp fallback + pending."""
    pending = "Aur approximately aapka budget range kya rahega?"
    stub = StubLLMProvider(
        "Ji, exact price list main WhatsApp par share kar dungi. " + pending
    )
    r = await _provider_responder(stub).answer(
        "price kitna hai", ConvState.QUALIFICATION.value, PERSONA, pending
    )
    assert r is not None
    assert pending in r.text
    assert "WhatsApp" in r.text
    # Must not invent a price that is not in persona.
    assert "99 crore" not in r.text and "5 lakh" not in r.text


@pytest.mark.asyncio
async def test_provider_responder_appends_pending_when_model_forgets():
    """Model forgot pending Q -> code appends it verbatim (no stall)."""
    pending = "Aapko kis location ya area mein chahiye?"
    stub = StubLLMProvider("Ji, main Creatik AI se bol rahi hoon.")
    r = await _provider_responder(stub).answer(
        "aap kaunsi company se ho", ConvState.QUALIFICATION.value, PERSONA, pending
    )
    assert r is not None
    assert "Creatik AI" in r.text
    assert pending in r.text


@pytest.mark.asyncio
async def test_provider_responder_fixed_never_calls_llm():
    stub = StubLLMProvider("should not be used", name="fixed")
    r = await _provider_responder(stub).answer(
        "aap kaun ho", ConvState.PERMISSION.value, PERSONA, "Is this a good time?"
    )
    assert r is None
    assert stub.last_prompt == ""


@pytest.mark.asyncio
async def test_provider_responder_empty_returns_none():
    stub = StubLLMProvider("   ")
    r = await _provider_responder(stub).answer(
        "hello", ConvState.QUALIFICATION.value, PERSONA, "pending?"
    )
    assert r is None


@pytest.mark.asyncio
async def test_engine_company_side_question_returns_to_pending():
    """Engine company side-Q -> deterministic grounded answer + pending (no LLM cost)."""
    pending = "Aapko kaunsa layout chahiye?"
    engine = ConversationEngine(
        responder=FakeResponder(
            "Ji, main Creatik AI se Neha bol rahi hoon. " + pending
        )
    )
    r = await engine.step(
        state=ConvState.QUALIFICATION, slots={},
        user_input="aap kaunsi company se ho", persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "Creatik AI" in r.reply
    assert "layout" in r.reply.lower() or "BHK" in r.reply
    # Deterministic grounding wins over LLM responder: zero cost.
    assert r.used_llm is False
    assert r.llm_cost == 0.0


@pytest.mark.asyncio
async def test_engine_price_side_question_returns_to_pending():
    """Engine price side-Q -> deterministic inventory price + pending (no LLM cost)."""
    engine = ConversationEngine(
        responder=FakeResponder(
            "Ji, Sunrise Heights me 2/3/4 BHK Rs. 40 lakh se shuru hai. "
            "Aur approximately aapka budget range kya rahega?"
        )
    )
    r = await engine.step(
        state=ConvState.QUALIFICATION,
        slots={"bhk": "2bhk", "location": "Noida"},
        user_input="price kitna hai", persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "40 lakh" in r.reply
    assert "budget" in r.reply.lower()
    # Deterministic grounding wins over LLM responder: zero cost.
    assert r.used_llm is False
    assert r.llm_cost == 0.0
