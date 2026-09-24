"""Tests for Plivo webhooks: answer XML + status mapping (PHASE 11)."""

import uuid

import pytest


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Ravi", "phone": "+919600000020", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call


@pytest.mark.asyncio
async def test_answer_returns_stream_xml_pointing_at_ws(client):
    call_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/webhooks/plivo/answer?call_id={call_id}")

    assert resp.status_code == 200
    assert "application/xml" in resp.headers["content-type"]
    body = resp.text
    assert "<Stream" in body
    assert 'contentType="audio/x-mulaw;rate=8000"' in body
    assert f"/api/v1/ws/calls/{call_id}" in body
    assert body.strip().endswith("</Response>")


@pytest.mark.asyncio
async def test_answer_xml_includes_record_when_enabled(monkeypatch, client):
    from app.config import settings

    monkeypatch.setattr(settings, "RECORDING_ENABLED", True)
    call_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/webhooks/plivo/answer?call_id={call_id}")
    body = resp.text
    assert "<Record recordSession=\"true\"" in body
    assert "callbackMethod=\"POST\"" in body
    assert f"recording?call_id={call_id}" in body


@pytest.mark.asyncio
async def test_answer_xml_has_no_record_when_disabled(client):
    call_id = str(uuid.uuid4())
    resp = await client.get(f"/api/v1/webhooks/plivo/answer?call_id={call_id}")
    assert "<Record" not in resp.text


@pytest.mark.asyncio
async def test_recording_callback_stores_url(client):
    call = await _create_call(client)
    resp = await client.post(
        "/api/v1/webhooks/plivo/recording",
        params={"call_id": call["id"]},
        data={"record_url": "https://s3.plivo.com/rec/xyz.mp3"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["recording_url"] == "https://s3.plivo.com/rec/xyz.mp3"
    assert updated["recording_enabled"] is True


@pytest.mark.asyncio
async def test_recording_callback_unknown_call_is_ignored(client):
    resp = await client.post(
        "/api/v1/webhooks/plivo/recording",
        params={"call_id": "00000000-0000-0000-0000-000000000000"},
        data={"record_url": "https://s3.plivo.com/rec/xyz.mp3"},
    )
    assert resp.status_code == 200
    assert resp.json()["skipped"] == "unknown_call"


@pytest.mark.asyncio
async def test_answer_accepts_post(client):
    resp = await client.post("/api/v1/webhooks/plivo/answer")
    assert resp.status_code == 200
    assert "<Response>" in resp.text


@pytest.mark.asyncio
async def test_status_maps_plivo_ringing_to_call(client):
    call = await _create_call(client)

    # Move the call into INITIATED and force a known provider call id (the
    # transition endpoint applies provider_call_id last, overriding the mock).
    tr = await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-test-123"},
    )
    assert tr.status_code == 200

    # Plivo rings the number -> status webhook.
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "ringing", "CallUUID": "PF-test-123"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "ringing"


@pytest.mark.asyncio
async def test_status_maps_no_answer_and_records_event(client):
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-test-456"},
    )

    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "no-answer", "CallUUID": "PF-test-456"},
    )
    assert resp.status_code == 200

    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "no_answer"

    events = (await client.get(f"/api/v1/calls/{call['id']}/events")).json()
    assert any(e["event_type"] == "no_answer" for e in events)


@pytest.mark.asyncio
async def test_status_unknown_call_is_ignored(client):
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "ringing", "CallUUID": "does-not-exist"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["skipped"] == "unknown_call"


@pytest.mark.asyncio
async def test_status_unmapped_value_is_ignored(client):
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-test-789"},
    )
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "weird-value", "CallUUID": "PF-test-789"},
    )
    assert resp.status_code == 200
    assert resp.json()["skipped"] == "unmapped"


@pytest.mark.asyncio
async def test_status_real_plivo_flow_in_progress_to_completed(client):
    """Plivo's real status sequence (ringing -> in-progress -> completed)."""
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-real-1"},
    )

    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "ringing", "CallUUID": "PF-real-1"},
    )
    assert resp.json()["ok"] is True

    # Plivo sends `in-progress` directly (no separate `answered` event).
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "in-progress", "CallUUID": "PF-real-1"},
    )
    assert resp.json()["ok"] is True

    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "in_progress"
    assert updated["answered_at"] is not None

    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "completed", "CallUUID": "PF-real-1"},
    )
    assert resp.json()["ok"] is True

    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "completed"
    assert updated["duration_seconds"] is not None


@pytest.mark.asyncio
async def test_status_accepts_query_params_via_get(client):
    """ring_url defaults to GET; accept status via query-string params too."""
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-real-2"},
    )
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        params={"CallStatus": "ringing", "CallUUID": "PF-real-2"},
    )
    assert resp.json()["ok"] is True
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "ringing"


@pytest.mark.asyncio
async def test_recording_callback_stores_plivo_recordurl_field(client):
    """Plivo posts the recording file as `RecordUrl` (not `record_url`)."""
    call = await _create_call(client)
    resp = await client.post(
        "/api/v1/webhooks/plivo/recording",
        params={"call_id": call["id"]},
        data={"RecordUrl": "https://aps1.media.plivo.com/v1/Account/X/Recording/abc.mp3"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["recording_url"] == "https://aps1.media.plivo.com/v1/Account/X/Recording/abc.mp3"


async def _drive_to_in_progress(client, provider_id):
    """Create a call and walk it queued -> initiated -> in_progress (mock dial)."""
    call = await _create_call(client)
    tr = await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": provider_id},
    )
    assert tr.status_code == 200
    tr = await client.post(
        f"/api/v1/calls/{call['id']}/transition", json={"status": "in_progress"}
    )
    assert tr.status_code == 200
    return call


@pytest.mark.asyncio
async def test_status_out_of_order_ringing_ignored(client):
    """Late `ringing` after `in_progress` must not regress the call."""
    call = await _drive_to_in_progress(client, "PF-ooo-1")
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "ringing", "CallUUID": "PF-ooo-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["skipped"] == "stale"
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "in_progress"


@pytest.mark.asyncio
async def test_status_duplicate_completed_single_event(client):
    """Retried `completed` webhook: applied once, never finalised twice."""
    call = await _drive_to_in_progress(client, "PF-dup-1")
    bodies = []
    for _ in range(2):
        resp = await client.post(
            "/api/v1/webhooks/plivo/status",
            data={"CallStatus": "completed", "CallUUID": "PF-dup-1"},
        )
        assert resp.status_code == 200
        bodies.append(resp.json())
    assert bodies[0]["ok"] is True
    assert bodies[1].get("skipped") == "duplicate"
    events = (await client.get(f"/api/v1/calls/{call['id']}/events")).json()
    completed = [e for e in events if e["event_type"] == "completed"]
    assert len(completed) == 1
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "completed"
    assert updated["ended_at"] is not None


@pytest.mark.asyncio
async def test_status_completed_straight_from_ringing(client):
    """Live regression: callee hangs up during ring -> completed recorded."""
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-ring-1"},
    )
    await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "ringing", "CallUUID": "PF-ring-1"},
    )
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "completed", "CallUUID": "PF-ring-1"},
    )
    assert resp.json()["ok"] is True
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "completed"
    assert updated["ended_at"] is not None


@pytest.mark.asyncio
async def test_status_completed_straight_from_initiated(client):
    """Short leg reported completed with no answer events at all."""
    call = await _create_call(client)
    await client.post(
        f"/api/v1/calls/{call['id']}/transition",
        json={"status": "initiated", "provider_call_id": "PF-init-1"},
    )
    resp = await client.post(
        "/api/v1/webhooks/plivo/status",
        data={"CallStatus": "completed", "CallUUID": "PF-init-1"},
    )
    assert resp.json()["ok"] is True
    updated = (await client.get(f"/api/v1/calls/{call['id']}")).json()
    assert updated["status"] == "completed"


def test_webhook_outcome_matrix():
    from app.api.v1.webhooks import _webhook_outcome
    from app.models.enums import CallStatus

    assert _webhook_outcome("in_progress", CallStatus.RINGING) == "stale"
    assert _webhook_outcome("ringing", CallStatus.RINGING) == "duplicate"
    assert _webhook_outcome("completed", CallStatus.COMPLETED) == "duplicate"
    assert _webhook_outcome("initiated", CallStatus.IN_PROGRESS) == "apply"
    assert _webhook_outcome("ringing", CallStatus.COMPLETED) == "apply"
    assert _webhook_outcome("queued", CallStatus.RINGING) == "apply"
