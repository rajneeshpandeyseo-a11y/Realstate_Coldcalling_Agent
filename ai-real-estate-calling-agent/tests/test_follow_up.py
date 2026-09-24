"""Tests for Phase 14 - Follow-ups / retries.

Covers the scheduling service (callback + automatic retry), the REST API
(/api/v1/follow-ups), the call-state wiring (CALLBACK_REQUESTED / NO_ANSWER
create a FollowUp) and the orchestrator's callback script. All deterministic
and cost-free in MOCK_MODE.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.follow_up import FollowUp
from app.services.follow_up import (
    DuplicateFollowUpError,
    complete,
    next_due,
    schedule,
    schedule_callback,
    schedule_retry,
)
from app.schemas.follow_up import FollowUpCreate


async def _new_lead(db, phone, name="Phase14 Lead"):
    from app.crud.lead import create_lead as crud_create_lead
    from app.schemas.lead import LeadCreate

    lead = await crud_create_lead(
        db, LeadCreate(name=name, phone=phone, city="Noida")
    )
    await db.commit()
    return lead


# ------------------------------------------------------------------ service


@pytest.mark.asyncio
async def test_service_schedule_callback_and_duplicate_guard(db_sessionmaker):
    async with db_sessionmaker() as db:
        lead = await _new_lead(db, "+912200000001")
        fu = await schedule_callback(
            db, lead_id=lead.id, notes="kal shaam"
        )
        await db.commit()
        assert getattr(fu.reason, "value", fu.reason) == "callback"
        assert getattr(fu.status, "value", fu.status) == "pending"

        with pytest.raises(DuplicateFollowUpError):
            await schedule_callback(db, lead_id=lead.id)

        # A different-reason follow-up is allowed.
        fu2 = await schedule(db, lead_id=lead.id, reason="follow_up")
        await db.commit()
        assert getattr(fu2.status, "value", fu2.status) == "pending"


@pytest.mark.asyncio
async def test_service_schedule_retry_and_list_due(db_sessionmaker):
    async with db_sessionmaker() as db:
        lead = await _new_lead(db, "+912200000002")
        retry = await schedule_retry(db, lead_id=lead.id, reason="no_answer", delay_hours=0)
        await db.commit()
        assert getattr(retry.status, "value", retry.status) == "pending"
        assert retry.scheduled_for is not None
        assert retry.reason == "no_answer"

        # Due now (delay 0) and pending -> surfaced by next_due.
        due = await next_due(db)
        assert any(str(f.id) == str(retry.id) for f in due)

        await complete(db, retry.id)
        await db.commit()
        fresh = (await db.execute(
            select(FollowUp).where(FollowUp.id == retry.id)
        )).scalar_one()
        assert getattr(fresh.status, "value", fresh.status) == "done"
        assert fresh.completed_at is not None


@pytest.mark.asyncio
async def test_service_retry_not_due_before_scheduled_time(db_sessionmaker):
    async with db_sessionmaker() as db:
        lead = await _new_lead(db, "+912200000003")
        retry = await schedule_retry(db, lead_id=lead.id, reason="busy", delay_hours=10)
        await db.commit()
        due = await next_due(db)
        assert all(str(f.id) != str(retry.id) for f in due)


# ------------------------------------------------------------------ API


async def _api_lead(client, phone):
    resp = await client.post(
        "/api/v1/leads", json={"name": "FU Lead", "phone": phone, "city": "Noida"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_api_create_list_get_complete(client):
    lead = await _api_lead(client, "+912200000004")
    created = (await client.post(
        "/api/v1/follow-ups",
        json={"lead_id": lead["id"], "reason": "callback", "notes": "kal 4 baje"},
    )).json()
    assert created["status"] == "pending"
    assert created["reason"] == "callback"

    listed = (await client.get("/api/v1/follow-ups", params={"lead_id": lead["id"]})).json()
    assert listed["total"] >= 1

    fetched = (await client.get(f"/api/v1/follow-ups/{created['id']}")).json()
    assert fetched["id"] == created["id"]

    done = (await client.post(f"/api/v1/follow-ups/{created['id']}/complete")).json()
    assert done["status"] == "done"
    assert done["completed_at"] is not None


@pytest.mark.asyncio
async def test_api_duplicate_callback_conflict(client):
    lead = await _api_lead(client, "+912200000005")
    payload = {"lead_id": lead["id"], "reason": "callback"}
    ok = await client.post("/api/v1/follow-ups", json=payload)
    assert ok.status_code == 201
    dup = await client.post("/api/v1/follow-ups", json=payload)
    assert dup.status_code == 409


# ------------------------------------------- call-state wiring / orchestrator


async def _make_call(client, phone):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "FU Call", "phone": phone, "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return lead, call


@pytest.mark.asyncio
async def test_orchestrator_callback_creates_follow_up(client, db_sessionmaker):
    from app.crud.call import get_call
    from app.services.call_orchestrator import CallOrchestrator, SCRIPT_CALLBACK

    lead, call = await _make_call(client, "+912200000006")
    async with db_sessionmaker() as db:
        call_obj = await get_call(db, uuid.UUID(call["id"]))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        result = await orch.run_simulated_call(db, call_obj, script=SCRIPT_CALLBACK)
        await db.commit()

    assert result.final_call_status == "callback_requested"
    assert result.final_lead_status == "callback_requested"

    async with db_sessionmaker() as db:
        from app.crud import follow_up as fu_crud

        fups = await fu_crud.get_follow_ups_for_lead(db, uuid.UUID(lead["id"]))
    assert len(fups) >= 1
    assert fups[0].reason == "callback"
    assert "kal shaam" in (fups[0].notes or "")


@pytest.mark.asyncio
async def test_orchestrator_completed_creates_no_follow_up(client, db_sessionmaker):
    from app.crud.call import get_call
    from app.services.call_orchestrator import CallOrchestrator

    lead, call = await _make_call(client, "+912200000007")
    async with db_sessionmaker() as db:
        call_obj = await get_call(db, uuid.UUID(call["id"]))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        result = await orch.run_simulated_call(db, call_obj)
        await db.commit()

    assert result.final_call_status == "completed"
    async with db_sessionmaker() as db:
        from app.crud import follow_up as fu_crud

        fups = await fu_crud.get_follow_ups_for_lead(db, uuid.UUID(lead["id"]))
    assert fups == []


@pytest.mark.asyncio
async def test_call_transition_no_answer_schedules_retry(client, db_sessionmaker):
    from app.config import settings

    prev = settings.AUTO_RETRY_ENABLED
    settings.AUTO_RETRY_ENABLED = True
    try:
        lead, call = await _make_call(client, "+912200000008")
        # QUEUED -> INITIATED (places mock outbound) -> NO_ANSWER.
        r1 = await client.post(
            f"/api/v1/calls/{call['id']}/transition", json={"status": "initiated"}
        )
        assert r1.status_code == 200, r1.text
        r2 = await client.post(
            f"/api/v1/calls/{call['id']}/transition", json={"status": "no_answer"}
        )
        assert r2.status_code == 200, r2.text

        async with db_sessionmaker() as db:
            from app.crud import follow_up as fu_crud

            fups = await fu_crud.get_follow_ups_for_lead(db, uuid.UUID(lead["id"]))
        assert len(fups) == 1
        assert fups[0].reason == "retry"
        assert getattr(fups[0].status, "value", fups[0].status) == "pending"
        assert fups[0].scheduled_for is not None
    finally:
        settings.AUTO_RETRY_ENABLED = prev
