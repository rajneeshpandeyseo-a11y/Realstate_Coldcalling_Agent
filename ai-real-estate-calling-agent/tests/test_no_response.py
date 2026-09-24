"""PHASE 1.5: no-response nudge -> graceful hangup + status no_response."""

import base64
import json
import os
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

import app.api.v1.ws as ws_mod
from app.api.v1.ws import (
    _checkpoint,
    _no_response_action,
    _playback_until,
    call_media_stream,
)
from app.main import app
from app.models.enums import CallStatus
from app.services import call as call_service

SILENCE = base64.b64encode(bytes([255]) * 320).decode()
SPEECH = base64.b64encode(bytes([200]) * 320).decode()


class FakeWebSocket:
    def __init__(self, inbound, clock=None):
        self.app = app
        self.inbound = inbound
        self.out = []
        self.closed_code = None
        self.accepted = False
        self.query_params = {"token": os.environ.get("WEBHOOK_TOKEN", "")}
        self.clock = clock

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        # Each inbound Plivo message arrives ~20s after the previous one on
        # the fake call clock (time-travel for deterministic no-response tests).
        if self.clock is not None:
            self.clock.advance()
        if self.inbound:
            return self.inbound.pop(0)
        raise WebSocketDisconnect()

    async def send_text(self, text):
        self.out.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_code = code


class FakeClock:
    """Frozen wall clock; advances one STEP per received WS message."""

    STEP = 20.0

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds: float = STEP):
        self.t += seconds


def _replies(ws):
    return [
        m.get("agent", {}).get("reply", "")
        for m in ws.out
        if m.get("event") == "playAudio"
    ]


async def _create_call(client, phone):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "NoResp", "phone": phone, "city": "Jaipur"}
    )).json()
    assert "id" in lead, lead
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    # Drive to a live state like the Plivo answer webhook would.
    await client.post(f"/api/v1/calls/{call['id']}/transition", json={"status": "initiated"})
    await client.post(f"/api/v1/calls/{call['id']}/transition", json={"status": "in_progress"})
    return call["id"]


# ------------------------------------------------------------- pure decision


def test_action_ok_before_timeout():
    assert _no_response_action(
        now=100.0, last_voice=95.0, playback_until=None,
        nudge_count=0, second_nudge_at=None, timeout=15.0,
    ) == "ok"


def test_action_ok_while_our_audio_playing():
    assert _no_response_action(
        now=100.0, last_voice=0.0, playback_until=200.0,
        nudge_count=0, second_nudge_at=None, timeout=15.0,
    ) == "ok"


def test_action_nudge_after_timeout():
    assert _no_response_action(
        now=100.0, last_voice=0.0, playback_until=None,
        nudge_count=0, second_nudge_at=None, timeout=15.0,
    ) == "nudge"
    assert _no_response_action(
        now=100.0, last_voice=0.0, playback_until=50.0,
        nudge_count=1, second_nudge_at=None, timeout=15.0,
    ) == "nudge"


def test_action_ok_during_goodbye_window_then_hangup():
    assert _no_response_action(
        now=100.0, last_voice=0.0, playback_until=None,
        nudge_count=2, second_nudge_at=95.0, timeout=15.0, goodbye=8.0,
    ) == "ok"
    assert _no_response_action(
        now=110.0, last_voice=0.0, playback_until=None,
        nudge_count=2, second_nudge_at=95.0, timeout=15.0, goodbye=8.0,
    ) == "hangup"


def test_checkpoint_needs_stream_id():
    assert _checkpoint("s1", "play-1") == {
        "event": "checkpoint", "streamId": "s1", "name": "play-1",
    }
    assert _checkpoint(None, "play-1") is None
    assert _playback_until(None) is None
    assert _playback_until(b"x" * 8000) is not None


# ------------------------------------------------------------- state machine


@pytest.mark.asyncio
async def test_no_response_terminal_transition(client, db_sessionmaker):
    call_id = await _create_call(client, f"+91961{uuid.uuid4().int % 100000:05d}")
    async with db_sessionmaker() as db:
        from app.crud import call as call_crud

        call = await call_crud.get_call(db, uuid.UUID(call_id))
        assert call.status == CallStatus.IN_PROGRESS.value
        call = await call_service.transition_call(db, call, CallStatus.NO_RESPONSE)
        await db.commit()
        assert call.status == CallStatus.NO_RESPONSE.value
        assert call.status in call_service.TERMINAL_STATES
        assert call.ended_at is not None


@pytest.mark.asyncio
async def test_no_response_rejected_from_queued(client, db_sessionmaker):
    lead = (await client.post(
        "/api/v1/leads",
        json={"name": "NoResp", "phone": f"+91962{uuid.uuid4().int % 100000:05d}", "city": "Jaipur"},
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    async with db_sessionmaker() as db:
        from app.crud import call as call_crud

        row = await call_crud.get_call(db, uuid.UUID(call["id"]))
        with pytest.raises(call_service.IllegalTransitionError):
            await call_service.transition_call(db, row, CallStatus.NO_RESPONSE)


# ------------------------------------------------------------- live socket


@pytest.mark.asyncio
async def test_ws_nudge_then_hangup_no_response(client, db_sessionmaker, monkeypatch):
    """Silence past both nudges -> goodbye + hangup, status no_response."""
    clock = FakeClock()
    monkeypatch.setattr(ws_mod, "_monotonic", clock)
    call_id = await _create_call(client, f"+91963{uuid.uuid4().int % 100000:05d}")
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = [
            json.dumps({"event": "start", "start": {"streamId": "nr-1"}}),
            json.dumps({"event": "playedStream", "streamId": "nr-1"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "playedStream", "streamId": "nr-1"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "playedStream", "streamId": "nr-1"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
        ]
        ws = FakeWebSocket(inbound=inbound, clock=clock)
        await call_media_stream(ws, uuid.UUID(call_id))

        replies = _replies(ws)
        assert any("sun pa rahe" in r for r in replies)
        assert any("busy hain" in r for r in replies)
        assert ws.closed_code == 1000
        async with db_sessionmaker() as db:
            from app.crud import call as call_crud

            call = await call_crud.get_call(db, uuid.UUID(call_id))
            assert call.status == CallStatus.NO_RESPONSE.value
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_speech_aborts_no_response_hangup(client, db_sessionmaker, monkeypatch):
    """Caller speaks after nudges -> hangup cancelled, call stays live."""
    clock = FakeClock()
    monkeypatch.setattr(ws_mod, "_monotonic", clock)
    call_id = await _create_call(client, f"+91964{uuid.uuid4().int % 100000:05d}")
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = [
            json.dumps({"event": "start", "start": {"streamId": "nr-2"}}),
            json.dumps({"event": "playedStream", "streamId": "nr-2"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "playedStream", "streamId": "nr-2"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            # Caller finally speaks: countdown resets.
            json.dumps({"event": "media", "media": {"payload": SPEECH}}),
            json.dumps({"event": "media", "media": {"payload": SPEECH}}),
            json.dumps({"event": "media", "media": {"payload": SPEECH}}),
            json.dumps({"event": "playedStream", "streamId": "nr-2"}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "stop"}),
        ]
        ws = FakeWebSocket(inbound=inbound, clock=clock)
        await call_media_stream(ws, uuid.UUID(call_id))

        replies = _replies(ws)
        assert sum("sun pa rahe" in r for r in replies) == 2
        assert sum("busy hain" in r for r in replies) == 1
        assert ws.closed_code == 1000  # via stop, not via hangup
        async with db_sessionmaker() as db:
            from app.crud import call as call_crud

            call = await call_crud.get_call(db, uuid.UUID(call_id))
            assert call.status != CallStatus.NO_RESPONSE.value
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_no_nudge_while_greeting_plays(client, db_sessionmaker):
    """Silence during our own playback is listening, not no-response."""
    call_id = await _create_call(client, f"+91965{uuid.uuid4().int % 100000:05d}")
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = [
            json.dumps({"event": "start", "start": {"streamId": "nr-3"}}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "stop"}),
        ]
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        assert "sun pa rahe" not in " ".join(_replies(ws))
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_stalled_socket_stays_graceful(client, db_sessionmaker, monkeypatch):
    """No WS traffic at all (network stall) never crashes the handler."""
    import asyncio as _asyncio

    monkeypatch.setattr(ws_mod, "NO_RESPONSE_TIMEOUT_SEC", 0.05)
    call_id = await _create_call(client, f"+91966{uuid.uuid4().int % 100000:05d}")
    app.state.live_session_factory = db_sessionmaker

    class StallingSocket(FakeWebSocket):
        def __init__(self):
            super().__init__([json.dumps({"event": "start"})])
            self._calls = 0

        async def receive_text(self):
            self._calls += 1
            if self._calls == 1:
                return self.inbound.pop(0)
            await _asyncio.sleep(10)  # stall: wait_for must time out, not hang
            raise WebSocketDisconnect()

    try:
        ws = StallingSocket()
        await call_media_stream(ws, uuid.UUID(call_id))
        assert ws.accepted is True
        # Greeting still went out before the stall.
        assert any(m.get("event") == "playAudio" for m in ws.out)
    finally:
        app.state.live_session_factory = None
