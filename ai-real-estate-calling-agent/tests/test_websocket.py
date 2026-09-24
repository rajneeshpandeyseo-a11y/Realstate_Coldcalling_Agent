"""WebSocket handshake tests (PHASE 11).

We invoke the real WS route handler directly (running in the pytest event loop,
same loop as the test DB engine) using an in-memory fake WebSocket transport.
This exercises the actual Plivo Audio Streaming protocol routing
(start -> media -> stop) and playAudio serialization deterministically and
cost-free, without the Starlette sync TestClient / asyncpg cross-loop issue.
"""

import base64
import json
import os
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.v1.ws import call_media_stream
from app.main import app


class FakeWebSocket:
    """Minimal in-memory WebSocket transport for the route handler."""

    def __init__(self, inbound):
        self.app = app
        self.inbound = inbound
        self.out = []
        self.closed_code = None
        self.accepted = False
        self.query_params = {"token": os.environ.get("WEBHOOK_TOKEN", "")}

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self.inbound:
            return self.inbound.pop(0)
        # Mimic Starlette: raising when the peer disconnects.
        raise WebSocketDisconnect()

    async def send_text(self, text):
        self.out.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_code = code


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "WebSocket Ankita", "phone": "+919600000030", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call["id"]


@pytest.mark.asyncio
async def test_ws_handshake_start_speaks_greeting(client, db_sessionmaker):
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        ws = FakeWebSocket(inbound=[json.dumps({"event": "start"})])
        await call_media_stream(ws, uuid.UUID(call_id))

        assert ws.accepted is True
        assert len(ws.out) == 1
        first = ws.out[0]
        assert first["event"] == "playAudio"
        assert first["media"]["payload"]  # base64 greeting audio
        assert first["media"]["sampleRate"] > 0
        assert first["agent"]["terminal"] is False
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_handshake_speech_leads_to_reply_audio(client, db_sessionmaker):
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        chunk = base64.b64encode(bytes([200]) * 320).decode()
        ws = FakeWebSocket(
            inbound=[
                json.dumps({"event": "start"}),
                json.dumps({"event": "media", "media": {"payload": chunk}}),
                json.dumps({"event": "stop"}),
            ]
        )
        await call_media_stream(ws, uuid.UUID(call_id))

        # Greeting only: the stop-flush persists the turn for the transcript
        # but never plays audio back into a dead call.
        assert len(ws.out) == 1
        assert ws.out[0]["event"] == "playAudio"  # greeting
        assert ws.closed_code == 1000
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_stop_flush_persists_turn_without_playback(client, db_sessionmaker, monkeypatch):
    """Stop-flush keeps transcript completeness while sending no audio."""
    from app.services.live_agent import LiveAgentSession as _Session

    texts = iter(["haan, batao", "2 bhk"])

    async def _fake_transcribe(self, audio):
        return next(texts)

    monkeypatch.setattr(_Session, "_transcribe", _fake_transcribe)
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        speech = base64.b64encode(bytes([200]) * 320).decode()
        silence = base64.b64encode(bytes([255]) * 320).decode()
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "stop-1"}})]
            + [json.dumps({"event": "media", "media": {"payload": speech}})] * 5
            + [json.dumps({"event": "media", "media": {"payload": silence}})] * 35
            + [json.dumps({"event": "media", "media": {"payload": speech}})] * 5
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        plays = [m for m in ws.out if m.get("event") == "playAudio"]
        # greeting + mid-call reply only; nothing after `stop`.
        assert len(plays) == 2
        assert ws.closed_code == 1000
        async with db_sessionmaker() as db:
            from app.crud import conversation as conv_crud

            msgs = await conv_crud.list_messages_for_call(db, uuid.UUID(call_id))
        speakers = [m.speaker for m in msgs]
        # The trailing utterance still landed in the transcript.
        assert speakers.count("customer") >= 2
        assert speakers.count("agent") >= 2
    finally:
        app.state.live_session_factory = None
