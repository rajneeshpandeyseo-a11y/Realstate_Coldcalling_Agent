"""Integration tests for the conversation API (PHASE 5)."""

import pytest

from app.models.enums import ConversationSpeaker


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Neha", "phone": "+919600000001", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return lead, call


@pytest.mark.asyncio
async def test_start_session_returns_greeting(client):
    _, call = await _create_call(client)
    resp = await client.post(
        "/api/v1/conversation/sessions", json={"call_id": call["id"]}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["session_id"]
    texts = [m["text"] for m in data["messages"]]
    assert any(t.startswith("Namaste") for t in texts)
    assert any(m["speaker"] == ConversationSpeaker.AGENT.value for m in data["messages"])


@pytest.mark.asyncio
async def test_full_turn_flow_persists_messages(client):
    _, call = await _create_call(client)
    start = (await client.post(
        "/api/v1/conversation/sessions", json={"call_id": call["id"]}
    )).json()
    sid = start["session_id"]

    # Turn 1: customer says interested
    t1 = (await client.post(
        "/api/v1/conversation/turn",
        json={"session_id": sid, "call_id": call["id"], "text": "haan, main interested hoon"},
    )).json()
    assert t1["state"]
    assert t1["reply"]

    # Progress through intro -> property selection -> qualification (each state
    # consumes one user turn), then fill all requirement slots.
    for user_text in [
        "haan, batao",
        "sunrise heights",
        "3 bhk",
        "40 lakh",
        "noida",
        "jaldi",
        "rehne ke liye",
        "haan, visit karna hai",
    ]:
        t = (await client.post(
            "/api/v1/conversation/turn",
            json={"session_id": sid, "call_id": call["id"], "text": user_text},
        )).json()
        assert t["reply"]

    # Session should have collected requirements and reached a later state
    final = (await client.get(f"/api/v1/conversation/sessions/{sid}")).json()
    states = [m["state"] for m in final["messages"] if m["speaker"] == "agent"]
    assert "QUALIFICATION" in states
    assert "CLOSING" in states

    # Transcript alternates agent/customer
    speakers = [m["speaker"] for m in final["messages"]]
    assert speakers[0] == ConversationSpeaker.AGENT.value
    assert len(speakers) >= 5


@pytest.mark.asyncio
async def test_dnc_turn_terminates(client):
    _, call = await _create_call(client)
    start = (await client.post(
        "/api/v1/conversation/sessions", json={"call_id": call["id"]}
    )).json()
    sid = start["session_id"]
    t = (await client.post(
        "/api/v1/conversation/turn",
        json={"session_id": sid, "call_id": call["id"], "text": "mujhe call mat karo"},
    )).json()
    assert t["terminated"] is True
    assert t["state"] == "DO_NOT_CALL"


@pytest.mark.asyncio
async def test_turn_missing_session_404(client):
    _, call = await _create_call(client)
    resp = await client.post(
        "/api/v1/conversation/turn",
        json={
            "session_id": "00000000-0000-0000-0000-000000000000",
            "call_id": call["id"],
            "text": "hello",
        },
    )
    assert resp.status_code == 404
