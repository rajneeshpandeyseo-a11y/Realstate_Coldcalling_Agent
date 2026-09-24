"""Tests for Phase 15 - cost optimization (ledger, persistence, policy).

All assertions run entirely against mock providers in MOCK_MODE, so no real
spend or outbound call is involved. We also verify the LLM cost path with a
stub fallback whose estimate is deterministic and non-zero.
"""

import uuid

import pytest

from app.conversation.engine import ConversationEngine, TurnResult
from app.conversation.intents import Intent, IntentResult
from app.conversation.states import ConvState
from app.crud.call import get_call
from app.models.enums import CallStatus
from app.services.cost import (
    llm_avoidance_ratio,
    projected_call_cost,
    tts_cache_hit_ratio,
)
from app.services.call_orchestrator import CallOrchestrator


# ---------------------------------------------------------------- policy


def test_llm_avoidance_ratio():
    assert llm_avoidance_ratio(10, 0) == 1.0
    assert llm_avoidance_ratio(10, 10) == 0.0
    assert llm_avoidance_ratio(4, 1) == 0.75
    assert llm_avoidance_ratio(0, 0) == 0.0


def test_tts_cache_hit_ratio():
    assert tts_cache_hit_ratio(10, 10) == 1.0
    assert tts_cache_hit_ratio(10, 0) == 0.0
    assert tts_cache_hit_ratio(4, 1) == 0.25
    assert tts_cache_hit_ratio(0, 0) == 0.0


def test_projected_call_cost_from_rates():
    comps = projected_call_cost(
        duration_minutes=5.0,
        telephony_per_minute=0.38,
        stt_per_minute=1.5,
        tts_per_char=0.003,
        tts_chars=1000,
        llm_tokens=2000,
    )
    assert comps["telephony"] == pytest.approx(1.9, abs=1e-6)
    assert comps["stt"] == pytest.approx(7.5, abs=1e-6)
    assert comps["tts"] == pytest.approx(3.0, abs=1e-6)
    assert comps["llm"] > 0
    assert comps["total"] == pytest.approx(
        comps["telephony"] + comps["stt"] + comps["tts"] + comps["llm"]
    )


def test_projected_call_cost_from_ledger_values():
    comps = projected_call_cost(
        duration_minutes=2.0,
        stt_cost=1.0,
        tts_cost=0.5,
        llm_cost=0.25,
        telephony_per_minute=0.38,
    )
    assert comps["stt"] == 1.0
    assert comps["tts"] == 0.5
    assert comps["llm"] == 0.25
    assert comps["telephony"] == pytest.approx(0.76, abs=1e-6)


# ------------------------------------------------------------ engine LLM cost


class StubLLMFallback:
    def __init__(self, intent=Intent.INTERESTED, confidence=0.99, cost=0.05):
        self._intent = intent
        self._confidence = confidence
        self._cost = cost

    async def classify(self, text: str, state: str) -> IntentResult:
        return IntentResult(self._intent, self._confidence, cost=self._cost)


@pytest.mark.asyncio
async def test_engine_propagates_llm_used_and_cost():
    fallback = StubLLMFallback(cost=0.123)
    engine = ConversationEngine(llm=fallback)
    # "zzz qqq" is not confidently classified by rules -> needs_llm true.
    turn = await engine.step(
        state=ConvState.INTRO, user_input="zzz qqq rrr gibberish"
    )
    assert isinstance(turn, TurnResult)
    assert turn.used_llm is True
    assert turn.llm_cost == pytest.approx(0.123)


@pytest.mark.asyncio
async def test_engine_no_llm_when_rules_are_confident():
    engine = ConversationEngine(llm=StubLLMFallback())
    turn = await engine.step(
        state=ConvState.INTRO, user_input="3 bhk chahiye"
    )
    # Rule path classifies this concretely; no LLM fallback fired.
    assert turn.used_llm is False
    assert turn.llm_cost == 0.0


# ------------------------------------------------ orchestrator cost persistence


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Cost Reena", "phone": "+919600000050", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call


async def _run(client, db_sessionmaker, call_id, engine=None, script=None):
    async with db_sessionmaker() as db:
        call = await get_call(db, uuid.UUID(call_id))
        orch = CallOrchestrator(session_factory=db_sessionmaker, engine=engine)
        return await orch.run_simulated_call(db, call, script=script)


@pytest.mark.asyncio
async def test_simulate_persists_cost_records(client, db_sessionmaker):
    call = await _create_call(client)
    result = await _run(client, db_sessionmaker, call["id"])

    resp = await client.get(f"/api/v1/calls/{call['id']}/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_id"] == call["id"]
    assert set(data["components"]) == {"stt", "tts", "llm", "telephony"}
    assert len(data["records"]) == 4
    assert data["total"] == pytest.approx(
        data["components"]["stt"]
        + data["components"]["tts"]
        + data["components"]["llm"]
        + data["components"]["telephony"]
    )
    # Persisted summary matches the in-memory ledger total.
    assert data["total"] == pytest.approx(result.cost.total, abs=1e-6)


@pytest.mark.asyncio
async def test_cost_endpoint_404_for_missing_call(client):
    resp = await client.get(f"/api/v1/calls/{uuid.uuid4()}/cost")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_orchestrator_aggregates_llm_cost_from_fallback(client, db_sessionmaker):
    # A stub fallback that fires on ambiguous utterances and reports a known cost.
    fallback = StubLLMFallback(cost=0.2)
    engine = ConversationEngine(llm=fallback)
    call = await _create_call(client)

    script = [
        "zzz qqq rrr gibberish one",
        "mmm nnn ppp gibberish two",
    ]
    result = await _run(client, db_sessionmaker, call["id"], engine=engine, script=script)

    # Ambiguous lines hit the LLM fallback, so total LLM cost is non-zero.
    assert result.cost.llm > 0
    assert result.final_call_status == CallStatus.COMPLETED.value
    assert any(t.used_llm for t in result.turns)
