"""PHASE 1.6: SCRIPT_CALLBACK vs negative responses.

Drives the real orchestrator (mock providers, zero cost) with callback-bound
and negative scripts and asserts the CRM outcome for each:
  "nahi chahiye" -> NOT_INTERESTED terminal (never a callback),
  "busy hoon" / "baad mein" -> CALLBACK -> callback follow-up,
  DNC markers always win over a callback.
"""

import uuid

import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.states import ConvState
from app.crud.call import get_call
from app.crud.follow_up import get_follow_ups_for_lead
from app.models.enums import CallStatus
from app.services.call_orchestrator import (
    SCRIPT_CALLBACK,
    CallOrchestrator,
)

PERSONA = {
    "company": "Creatik AI",
    "agent_name": "Neha",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
    },
}

ENGINE = ConversationEngine()


def _phone(tag: str) -> str:
    return f"+91967{(uuid.uuid4().int % 100000):05d}"


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "CB Neg", "phone": _phone("x"), "city": "Jaipur"}
    )).json()
    assert "id" in lead, lead
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return lead["id"], call["id"]


async def _run(db_sessionmaker, call_id, script):
    async with db_sessionmaker() as db:
        call = await get_call(db, uuid.UUID(call_id))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        return await orch.run_simulated_call(db, call, script=script)


async def _follow_ups(db_sessionmaker, lead_id):
    async with db_sessionmaker() as db:
        return await get_follow_ups_for_lead(db, uuid.UUID(lead_id))


# ------------------------------------------------------------- engine routing


@pytest.mark.asyncio
async def test_baad_mein_mid_qualification_routes_to_callback():
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk"},
        user_input="baad mein call karna", persona=PERSONA,
    )
    assert r.state == ConvState.CALLBACK
    assert r.changed_state is True


@pytest.mark.asyncio
async def test_busy_mid_qualification_routes_to_callback():
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk"},
        user_input="busy hoon", persona=PERSONA,
    )
    assert r.state == ConvState.CALLBACK


@pytest.mark.asyncio
async def test_nahi_chahiye_mid_qualification_terminates_not_interested():
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk"},
        user_input="nahi chahiye", persona=PERSONA,
    )
    assert r.state == ConvState.NOT_INTERESTED
    assert r.termination is True


# ------------------------------------------------------- orchestrator outcomes


@pytest.mark.asyncio
async def test_callback_script_books_callback_followup(client, db_sessionmaker):
    lead_id, call_id = await _create_call(client)
    result = await _run(db_sessionmaker, call_id, SCRIPT_CALLBACK)

    assert result.final_call_status == CallStatus.CALLBACK_REQUESTED.value
    assert result.final_lead_status == "callback_requested"
    rows = await _follow_ups(db_sessionmaker, lead_id)
    assert any(
        (getattr(f, "reason", None) or "") == "callback"
        and (getattr(f, "status", None) or getattr(getattr(f, "status", ""), "value", "")) != "cancelled"
        for f in rows
    ), "expected a callback follow-up for the lead"


@pytest.mark.asyncio
async def test_busy_at_permission_books_callback(client, db_sessionmaker):
    lead_id, call_id = await _create_call(client)
    result = await _run(
        db_sessionmaker, call_id,
        ("hello", "busy hoon", "kal shaam ko call karna"),
    )

    assert result.turns[-1].terminal is True
    assert result.final_call_status == CallStatus.CALLBACK_REQUESTED.value
    rows = await _follow_ups(db_sessionmaker, lead_id)
    assert any((getattr(f, "reason", None) or "") == "callback" for f in rows)


@pytest.mark.asyncio
async def test_baad_mein_mid_qualification_books_callback(client, db_sessionmaker):
    lead_id, call_id = await _create_call(client)
    result = await _run(
        db_sessionmaker, call_id,
        ("hello", "haan, batao", "2 bhk", "baad mein call karna", "kal shaam ko"),
    )

    assert result.final_call_status == CallStatus.CALLBACK_REQUESTED.value
    rows = await _follow_ups(db_sessionmaker, lead_id)
    assert any((getattr(f, "reason", None) or "") == "callback" for f in rows)


@pytest.mark.asyncio
async def test_nahi_chahiye_never_books_callback(client, db_sessionmaker):
    lead_id, call_id = await _create_call(client)
    result = await _run(
        db_sessionmaker, call_id,
        ("haan, main interested hoon", "haan, batao", "nahi chahiye"),
    )

    assert result.turns[-1].state == ConvState.NOT_INTERESTED.value
    assert result.turns[-1].terminal is True
    rows = await _follow_ups(db_sessionmaker, lead_id)
    assert all((getattr(f, "reason", None) or "") != "callback" for f in rows)


@pytest.mark.asyncio
async def test_dnc_wins_over_callback(client, db_sessionmaker):
    lead_id, call_id = await _create_call(client)
    result = await _run(
        db_sessionmaker, call_id,
        ("hello", "haan, batao", "mujhe call mat karo"),
    )

    assert result.final_call_status == CallStatus.DO_NOT_CALL.value
    assert result.final_lead_status == "do_not_call"
    rows = await _follow_ups(db_sessionmaker, lead_id)
    assert all((getattr(f, "reason", None) or "") != "callback" for f in rows)
