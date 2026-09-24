"""Integration tests for the Call lifecycle API (PHASE 4)."""

import pytest


async def _make_lead(client):
    resp = await client.post(
        "/api/v1/leads", json={"name": "Aman", "phone": "+919700000011", "city": "Gurgaon"}
    )
    assert resp.status_code == 201
    return resp.json()


@pytest.mark.asyncio
async def test_create_call_queued(client):
    lead = await _make_lead(client)
    resp = await client.post("/api/v1/calls", json={"lead_id": lead["id"]})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "queued"
    assert body["lead_id"] == lead["id"]
    assert body["direction"] == "outbound"
    assert body["attempt"] == 1


@pytest.mark.asyncio
async def test_create_call_missing_lead_404(client):
    resp = await client.post(
        "/api/v1/calls", json={"lead_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_call_dnc_lead_forbidden(client):
    lead = await _make_lead(client)
    await client.post(f"/api/v1/leads/{lead['id']}/do-not-call")
    resp = await client.post("/api/v1/calls", json={"lead_id": lead["id"]})
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_full_lifecycle_transitions(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]

    async def trans(status, **extra):
        return (await client.post(
            f"/api/v1/calls/{cid}/transition", json={"status": status, **extra}
        ))

    # queued -> initiated -> ringing -> answered -> in_progress -> completed
    assert (await trans("initiated")).json()["status"] == "initiated"
    assert (await trans("ringing")).json()["status"] == "ringing"
    answered = (await trans("answered")).json()
    assert answered["status"] == "answered"
    assert answered["answered_at"] is not None
    assert (await trans("in_progress")).json()["status"] == "in_progress"

    completed = (await trans("completed")).json()
    assert completed["status"] == "completed"
    assert completed["ended_at"] is not None
    assert completed["duration_seconds"] is not None

    # lead should now be marked contacted (not new)
    lead_after = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert lead_after["status"] == "contacted"


@pytest.mark.asyncio
async def test_illegal_transition_rejected(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]

    # queued -> completed is not allowed (must dial first)
    resp = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "completed"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_dial_failure_records_failed_not_stuck_queued(client, monkeypatch):
    """Plivo dial blowing up -> FAILED + reason, never a silent QUEUED limbo."""
    import app.services.call as call_service

    def _boom():
        raise RuntimeError("Plivo down for maintenance")

    monkeypatch.setattr(call_service, "get_telephony_provider", _boom)
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()

    # The operator still sees the error (500), ...
    with pytest.raises(RuntimeError, match="Plivo down"):
        await client.post(
            f"/api/v1/calls/{call['id']}/transition", json={"status": "initiated"}
        )
    # ... but the DB tells the truth instead of looking QUEUED forever.
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "failed"
    assert updated["ended_at"] is not None
    events = (await client.get(f"/api/v1/calls/{call['id']}/events")).json()
    assert any(e["event_type"] == "failed" for e in events)


@pytest.mark.asyncio
async def test_transition_to_dnc_marks_lead_optout(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]
    await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "initiated"})
    await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "ringing"})
    resp = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "answered"})
    assert resp.status_code == 200
    resp = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "in_progress"})
    assert resp.status_code == 200
    dnc = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "do_not_call"})
    assert dnc.status_code == 200

    lead_after = (await client.get(f"/api/v1/leads/{lead['id']}")).json()
    assert lead_after["do_not_call"] is True
    assert lead_after["status"] == "do_not_call"


@pytest.mark.asyncio
async def test_call_events_recorded(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]
    await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "initiated"})
    await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "ringing"})

    events = (await client.get(f"/api/v1/calls/{cid}/events")).json()
    types = [e["event_type"] for e in events]
    assert "queued" in types
    assert "initiated" in types
    assert "ringing" in types
    # ordered chronologically (queued first, then initiated, then ringing)
    assert types == ["queued", "initiated", "ringing"]


@pytest.mark.asyncio
async def test_no_answer_terminal(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]
    await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "initiated"})
    resp = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "no_answer"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "no_answer"
    # terminal state: further transitions rejected
    bad = await client.post(f"/api/v1/calls/{cid}/transition", json={"status": "initiated"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_initiate_invokes_telephony_provider(client):
    """Phase 6: initiating a call must use the telephony provider, storing the
    provider call id on the call record."""
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]
    assert call.get("provider_call_id") in (None, "")

    initiated = (await client.post(
        f"/api/v1/calls/{cid}/transition", json={"status": "initiated"}
    )).json()
    assert initiated["status"] == "initiated"
    # mock provider returns provider_call_id like "mock-..."
    assert initiated["provider_call_id"]
    assert initiated["provider_call_id"].startswith("mock-")
    assert initiated["provider"] == "mock"


@pytest.mark.asyncio
async def test_transcript_and_recording_endpoints(client):
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    cid = call["id"]

    # Simulate the call so a transcript is generated (mock providers, cost-free).
    sim = await client.post(f"/api/v1/calls/{cid}/simulate", json={})
    assert sim.status_code == 200

    # Plain-text transcript download.
    txt = await client.get(f"/api/v1/calls/{cid}/transcript.txt")
    assert txt.status_code == 200
    assert "text/plain" in txt.headers["content-type"]
    assert "AGENT" in txt.text
    assert "content-disposition" in {k.lower() for k in txt.headers}

    # Structured JSON transcript download.
    js = await client.get(f"/api/v1/calls/{cid}/transcript.json")
    assert js.status_code == 200
    assert js.json()["call_id"] == cid
    assert len(js.json()["messages"]) > 0
    assert js.json()["messages"][0]["speaker"]

    # Recording: not enabled by default in mock mode -> handled gracefully.
    rec = await client.get(f"/api/v1/calls/{cid}/recording")
    assert rec.status_code == 200
    assert rec.json()["call_id"] == cid
    assert rec.json()["status"] in {
        "available",
        "processing",
        "unavailable",
    }


@pytest.mark.asyncio
async def test_transcript_missing_call_404(client):
    resp = await client.get(
        "/api/v1/calls/00000000-0000-0000-0000-000000000000/transcript.txt"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dial_records_dialled_number_not_caller_id(client):
    """place_call stores the lead's number on the call (not our caller ID)."""
    lead = await _make_lead(client)
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    tr = await client.post(
        f"/api/v1/calls/{call['id']}/transition", json={"status": "initiated"}
    )
    assert tr.status_code == 200
    assert tr.json()["phone_number"] == lead["phone"]
    assert tr.json()["phone_number"] != "+918031807191"

