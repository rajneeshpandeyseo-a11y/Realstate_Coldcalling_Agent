"""PHASE 1.3: barge-in (caller speaks over TTS playback) tests.

Plivo cancels queued TTS via `clearAudio {streamId}`. Our WS sends exactly
one clearAudio per playback when consecutive speech frames arrive while our
audio is playing. Sarvam STT/TTS are REST (no streaming), so interruption
lives only in the WS transport + LiveAgentSession VAD - no paid calls here.
"""

import base64
import json
import os
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.v1.ws import (
    BARGE_IN_POST_REPLY_GRACE_SEC,
    BARGE_IN_SPEECH_FRAMES,
    _clear_audio,
    _extract_stream_id,
    call_media_stream,
)
from app.main import app
from app.services.live_agent import LiveAgentSession

SPEECH = base64.b64encode(bytes([200]) * 320).decode()
SILENCE = base64.b64encode(bytes([255]) * 320).decode()


def _speech_burst(n: int | None = None) -> list[str]:
    """n sustained speech frames (default: exactly the barge-in threshold)."""
    count = BARGE_IN_SPEECH_FRAMES if n is None else n
    return [json.dumps({"event": "media", "media": {"payload": SPEECH}})] * count


class FakeWebSocket:
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
        raise WebSocketDisconnect()

    async def send_text(self, text):
        self.out.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_code = code


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads",
        json={"name": "Barge In Test", "phone": "+919600000031", "city": "Jaipur"},
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call["id"]


def test_extract_stream_id_variants():
    assert _extract_stream_id({"event": "start", "start": {"streamId": "abc"}}) == "abc"
    assert _extract_stream_id({"event": "start", "streamId": "xyz"}) == "xyz"
    assert _extract_stream_id({"event": "start"}) is None
    assert _extract_stream_id({}) is None


def test_clear_audio_builder():
    assert _clear_audio("s1") == {"event": "clearAudio", "streamId": "s1"}
    assert _clear_audio(None) is None
    assert _clear_audio("") is None


def test_agent_is_speech_and_interrupt(db_sessionmaker):
    agent = LiveAgentSession(uuid.uuid4(), session_factory=db_sessionmaker)
    assert agent.is_speech(bytes([200]) * 320) is True
    assert agent.is_speech(bytes([255]) * 320) is False
    assert agent.interrupted_count == 0
    agent.interrupt()
    assert agent.interrupted_count == 1


@pytest.mark.asyncio
async def test_ws_sends_clearAudio_on_barge_in(client, db_sessionmaker):
    """Sustained speech during greeting playback -> exactly one clearAudio."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "stream-1"}})]
            + _speech_burst()
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert kinds[0] == "playAudio"  # greeting
        clears = [m for m in ws.out if m.get("event") == "clearAudio"]
        assert len(clears) == 1
        assert clears[0]["streamId"] == "stream-1"
        assert BARGE_IN_SPEECH_FRAMES == 25
        assert ws.closed_code == 1000
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_short_blip_does_not_interrupt(client, db_sessionmaker):
    """A cough-length blip (~60ms) must not chop our reply."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "stream-1b"}})]
            + _speech_burst(3)
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert "playAudio" in kinds
        assert "clearAudio" not in kinds
        assert ws.closed_code == 1000
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_no_clearAudio_without_stream_id(client, db_sessionmaker):
    """Missing streamId must never crash and never send clearAudio."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start"})]
            + _speech_burst()
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert "playAudio" in kinds
        assert "clearAudio" not in kinds
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_silence_during_playback_sends_no_clearAudio(client, db_sessionmaker):
    """Silence while playing is not an interruption."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = [
            json.dumps({"event": "start", "start": {"streamId": "stream-2"}}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "media", "media": {"payload": SILENCE}}),
            json.dumps({"event": "stop"}),
        ]
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert "clearAudio" not in kinds
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_post_reply_grace_ignores_utterance_tail(client, db_sessionmaker, monkeypatch):
    """Tail of the just-answered utterance must not cancel our reply.

    Regression test for the live "caller hears silence" bug: after the
    engine answers, the tail of that same utterance is still streaming in.
    Those frames must not clearAudio-cancel the just-sent playAudio.
    """
    import app.api.v1.ws as ws_mod

    now = [1000.0]
    monkeypatch.setattr(ws_mod, "_monotonic", lambda: now[0])
    assert BARGE_IN_POST_REPLY_GRACE_SEC > 0
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "stream-g"}})]
            + _speech_burst()
            + [json.dumps({"event": "media", "media": {"payload": SILENCE}})] * 35
            # Utterance tail arrives immediately after our reply was sent.
            + _speech_burst()
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert kinds.count("playAudio") >= 2  # greeting + flush reply
        clears = [m for m in ws.out if m.get("event") == "clearAudio"]
        assert len(clears) == 1  # only the greeting barge-in
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_post_reply_grace_expires_then_barge_in_clears(client, db_sessionmaker, monkeypatch):
    """Genuine interruption after the grace window still cancels playback."""
    import app.api.v1.ws as ws_mod

    now = [1000.0]
    monkeypatch.setattr(ws_mod, "_monotonic", lambda: now[0])
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "stream-g2"}})]
            + _speech_burst()
            + [json.dumps({"event": "media", "media": {"payload": SILENCE}})] * 35
            + _speech_burst()
            + _speech_burst()
            + [json.dumps({"event": "stop"})]
        )

        class AdvancingSocket(FakeWebSocket):
            def __init__(self, inbound):
                super().__init__(inbound)
                self.n = 0

            async def receive_text(self):
                self.n += 1
                if self.n == 1 + BARGE_IN_SPEECH_FRAMES + 35 + BARGE_IN_SPEECH_FRAMES + 1:
                    # Step exactly past the grace window but still inside
                    # the reply's playback window (mock audio is short).
                    now[0] += BARGE_IN_POST_REPLY_GRACE_SEC
                return await super().receive_text()

        ws = AdvancingSocket(inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        clears = [m for m in ws.out if m.get("event") == "clearAudio"]
        assert len(clears) == 2  # greeting barge-in + post-grace interruption
    finally:
        app.state.live_session_factory = None


@pytest.mark.asyncio
async def test_ws_handles_played_and_cleared_confirmations(client, db_sessionmaker):
    """playedStream/clearedAudio confirmations are absorbed, socket survives."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = [
            json.dumps({"event": "start", "start": {"streamId": "stream-3"}}),
            json.dumps({"event": "playedStream", "streamId": "stream-3"}),
            json.dumps({"event": "clearedAudio", "streamId": "stream-3"}),
            json.dumps({"event": "stop"}),
        ]
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        assert ws.accepted is True
        assert ws.closed_code == 1000
    finally:
        app.state.live_session_factory = None
