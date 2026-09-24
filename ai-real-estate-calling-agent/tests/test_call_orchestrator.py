"""Tests for the Phase 12 call orchestrator (end-to-end simulated call).

Every assertion here runs entirely against the mock providers in MOCK_MODE:
no real outbound call is placed, no key/balance/KYC is required, and the
outcome is deterministic. The same orchestrator code path will drive a real
Plivo/Sarvam/Gemini call once provider config is switched.
"""

import uuid

import pytest

from app.crud.call import get_call
from app.models.enums import CallStatus
from app.services.call_orchestrator import (
    CallOrchestrator,
    SCRIPT_DO_NOT_CALL,
)


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Phase12 Neel", "phone": "+919600000040", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call


async def _run(client, db_sessionmaker, call_id, script=None):
    async with db_sessionmaker() as db:
        call = await get_call(db, uuid.UUID(call_id))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        return await orch.run_simulated_call(db, call, script=script)


@pytest.mark.asyncio
async def test_simulated_call_runs_end_to_end(client, db_sessionmaker):
    call = await _create_call(client)
    result = await _run(client, db_sessionmaker, call["id"])

    assert result.simulated is True
    assert result.provider == "mock"
    assert result.session_id is not None
    assert result.final_call_status == CallStatus.COMPLETED.value
    assert len(result.turns) >= 3
    # Greeting first, then agent replies for each scripted customer line.
    assert result.turns[0].state == "GREETING"
    assert all(t.reply for t in result.turns)

    # The conversation was persisted to the transcript.
    async with db_sessionmaker() as db:
        from app.crud.conversation import list_messages

        msgs = await list_messages(db, uuid.UUID(result.session_id))
    assert len(msgs) >= 2
    assert msgs[0].speaker == "agent"


@pytest.mark.asyncio
async def test_simulated_call_advances_call_state_machine(client, db_sessionmaker):
    call = await _create_call(client)
    async with db_sessionmaker() as db:
        before = await get_call(db, uuid.UUID(call["id"]))
        assert before.status == CallStatus.QUEUED.value
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        result = await orch.run_simulated_call(db, before)
        await db.refresh(before)
        final_status = before.status
    assert final_status == CallStatus.COMPLETED.value
    assert result.final_call_status == CallStatus.COMPLETED.value
    # The outbound call got a provider id from the (mock) telephony provider.
    assert before.provider_call_id


@pytest.mark.asyncio
async def test_simulated_call_do_not_call_marks_lead(client, db_sessionmaker):
    call = await _create_call(client)
    async with db_sessionmaker() as db:
        call_obj = await get_call(db, uuid.UUID(call["id"]))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        result = await orch.run_simulated_call(db, call_obj, script=SCRIPT_DO_NOT_CALL)

    assert result.final_call_status == CallStatus.DO_NOT_CALL.value
    assert result.turns[-1].terminal is True
    assert result.final_lead_status == "do_not_call"


@pytest.mark.asyncio
async def test_simulated_call_produces_cost_ledger(client, db_sessionmaker):
    call = await _create_call(client)
    result = await _run(client, db_sessionmaker, call["id"])

    assert result.cost.currency == "INR"
    assert result.cost.stt >= 0
    assert result.cost.tts >= 0
    assert result.cost.telephony > 0  # duration * rate
    # total equals the sum of components.
    assert abs(
        result.cost.total
        - (result.cost.stt + result.cost.tts + result.cost.llm + result.cost.telephony)
    ) < 1e-6
    assert set(result.cost.components) == {"stt", "tts", "llm", "telephony"}


@pytest.mark.asyncio
async def test_simulate_endpoint_returns_report(client):
    call = await _create_call(client)
    resp = await client.post(f"/api/v1/calls/{call['id']}/simulate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["simulated"] is True
    assert data["provider"] == "mock"
    assert data["final_call_status"] == "completed"
    assert data["session_id"]
    assert data["cost"]["total"] >= 0
    assert data["turns"][0]["state"] == "GREETING"


@pytest.mark.asyncio
async def test_simulate_endpoint_rejects_non_queued_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Busy Radhika", "phone": "+919600000041", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    # Advance the call out of QUEUED first.
    await client.post(
        f"/api/v1/calls/{call['id']}/transition", json={"status": "initiated"}
    )
    resp = await client.post(f"/api/v1/calls/{call['id']}/simulate")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_simulate_endpoint_never_touches_live_registry(client, monkeypatch):
    """Regression: /simulate must not dial or bill even on a live server.

    The endpoint forces mock providers; the provider registry must not
    even be consulted (a bomb proves it). Previously the registry-selected
    (live) providers were used and simulation really dialled out.
    """
    import app.services.call_orchestrator as orch_mod

    def _boom():
        raise AssertionError("simulate must never touch the provider registry")

    monkeypatch.setattr(orch_mod, "get_telephony_provider", _boom)
    monkeypatch.setattr(orch_mod, "get_stt_provider", _boom)
    monkeypatch.setattr(orch_mod, "get_tts_provider", _boom)
    monkeypatch.setattr(orch_mod, "get_llm_provider", _boom)

    call = await _create_call(client)
    resp = await client.post(f"/api/v1/calls/{call['id']}/simulate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["simulated"] is True
    assert data["provider"] == "mock"
    assert data["final_call_status"] == "completed"
