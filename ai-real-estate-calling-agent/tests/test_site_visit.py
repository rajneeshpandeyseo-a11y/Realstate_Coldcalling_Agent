"""Tests for Phase 13 - Site visits.

Covers the booking-slot parsers, the SiteVisit CRUD/service layer, the REST API
(/api/v1/site-visits) and the orchestration hook that creates a SiteVisit after
a qualified call. All runs are deterministic and cost-free in MOCK_MODE.
"""

import uuid

import pytest
from sqlalchemy import select

from app.models.lead import Lead
from app.models.site_visit import SiteVisit
from app.services.site_visit import (
    capture_booking_from_text,
    create_site_visit,
    MultipleActiveVisitsError,
    resolve_preferred_date,
)
from app.schemas.site_visit import SiteVisitCreate


# ------------------------------------------------------------------ parsers


def test_capture_booking_parses_time_and_weekday():
    captured = capture_booking_from_text("shanivaar subah")
    assert captured["preferred_time"] == "morning"
    assert captured["preferred_date"] == "saturday"

    captured = capture_booking_from_text("kal shaam 5 baje")
    assert captured["preferred_time"] == "evening"
    assert captured["preferred_date"] == "tomorrow"


def test_capture_booking_parses_explicit_clock_time():
    captured = capture_booking_from_text("Saturday 4:30 pm")
    assert captured["preferred_time"] == "16:30"
    assert captured["preferred_date"] == "saturday"


def test_resolve_preferred_date_maps_weekday_to_upcoming():
    resolved = resolve_preferred_date("saturday")
    assert resolved is not None
    assert resolved.weekday() == 5  # Saturday


# ------------------------------------------------------------------ service


@pytest.mark.asyncio
async def test_service_create_and_duplicate_guard(db_sessionmaker):
    from app.crud.lead import create_lead as crud_create_lead
    from app.schemas.lead import LeadCreate

    async with db_sessionmaker() as db:
        lead = await crud_create_lead(
            db, LeadCreate(name="Site Ravi", phone="+911100000001", city="Noida")
        )
        await db.commit()

        visit1 = await create_site_visit(
            db,
            SiteVisitCreate(
                lead_id=lead.id,
                preferred_time="morning",
                preferred_date=None,
                location="Noida Extension",
            ),
        )
        await db.commit()
        assert getattr(visit1.status, "value", visit1.status) == "requested"

        with pytest.raises(MultipleActiveVisitsError):
            await create_site_visit(
                db, SiteVisitCreate(lead_id=lead.id, preferred_time="evening")
            )


# ------------------------------------------------------------------ API


async def _new_lead(client, phone):
    resp = await client.post(
        "/api/v1/leads", json={"name": "Phase13 Lead", "phone": phone, "city": "Noida"}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_api_create_and_get_site_visit(client):
    lead = await _new_lead(client, "+911100000002")
    resp = await client.post(
        "/api/v1/site-visits",
        json={
            "lead_id": lead["id"],
            "preferred_time": "evening",
            "location": "Noida Extension",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == "requested"
    assert data["preferred_time"] == "evening"
    assert data["confirmed_by_customer"] is False

    fetched = await client.get(f"/api/v1/site-visits/{data['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == data["id"]


@pytest.mark.asyncio
async def test_api_duplicate_active_visit_conflict(client):
    lead = await _new_lead(client, "+911100000003")
    payload = {"lead_id": lead["id"], "preferred_time": "morning"}
    ok = await client.post("/api/v1/site-visits", json=payload)
    assert ok.status_code == 201
    dup = await client.post("/api/v1/site-visits", json=payload)
    assert dup.status_code == 409


@pytest.mark.asyncio
async def test_api_confirm_promotes_lead_to_qualified(client):
    lead = await _new_lead(client, "+911100000004")
    created = (await client.post(
        "/api/v1/site-visits",
        json={"lead_id": lead["id"], "preferred_time": "morning"},
    )).json()

    resp = await client.patch(
        f"/api/v1/site-visits/{created['id']}",
        json={"status": "confirmed", "confirmed_by_customer": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["confirmed_by_customer"] is True

    lead_now = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert lead_now["status"] == "qualified"


@pytest.mark.asyncio
async def test_api_list_site_visits(client):
    lead = await _new_lead(client, "+911100000005")
    await client.post("/api/v1/site-visits", json={"lead_id": lead["id"]})
    resp = await client.get("/api/v1/site-visits", params={"lead_id": lead["id"]})
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1
    assert resp.json()["items"][0]["lead_id"] == lead["id"]


# ------------------------------------------------------ orchestration hook


async def _make_call(client, phone):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Visit Neeti", "phone": phone, "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return lead, call


@pytest.mark.asyncio
async def test_orchestrator_creates_site_visit_on_qualified_call(client, db_sessionmaker):
    from app.crud.call import get_call
    from app.services.call_orchestrator import CallOrchestrator, SCRIPT_DO_NOT_CALL

    lead, call = await _make_call(client, "+911100000006")
    async with db_sessionmaker() as db:
        call_obj = await get_call(db, uuid.UUID(call["id"]))
        orch = CallOrchestrator(session_factory=db_sessionmaker)
        result = await orch.run_simulated_call(db, call_obj)
        await db.commit()

    assert result.final_call_status == "completed"
    assert result.site_visit_id is not None
    assert result.final_lead_status == "qualified"

    # The site visit was persisted with captured booking data.
    async with db_sessionmaker() as db:
        visit = (await db.execute(
            select(SiteVisit).where(SiteVisit.id == uuid.UUID(result.site_visit_id))
        )).scalar_one()
        lead_obj = (await db.execute(
            select(Lead).where(Lead.id == uuid.UUID(lead["id"]))
        )).scalar_one()
    status = getattr(visit.status, "value", visit.status)
    assert status == "requested"
    assert visit.preferred_time == "morning"
    # The visit records the specific property the customer chose (monitoring).
    assert visit.location == "Sunrise Heights, Noida"
    # Two-step booking (weekday, then time): the last booking utterance is kept.
    assert visit.notes == "subah"
    assert getattr(lead_obj.status, "value", lead_obj.status) == "qualified"

    # Do-not-call script must NOT create a site visit (fresh call + lead).
    from app.crud.call import get_call as get_call_crud

    lead2, call2 = await _make_call(client, "+911100000007")
    async with db_sessionmaker() as db:
        call2_obj = await get_call_crud(db, uuid.UUID(call2["id"]))
        orch2 = CallOrchestrator(session_factory=db_sessionmaker)
        result2 = await orch2.run_simulated_call(db, call2_obj, script=SCRIPT_DO_NOT_CALL)
        await db.commit()
    assert result2.site_visit_id is None
    assert result2.final_call_status == "do_not_call"
