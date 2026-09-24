"""Mock-mode simulation suite: live-call failure scenarios, zero API cost.

Problem-to-test map (user-observed live issues #1-8):

- T1 (#1 initial latency): greeting needs zero STT roundtrips (S1 covers
  barge-in mid-playback + coherent continuation)
- T2 (#2 per-turn latency): every audio-path turn records full stage
  timings (vad_wait/stt/engine/tts/total), all non-negative
- T3 (#3 overlap): double `start` speaks the greeting exactly once
  (answer-fetch idempotency)
- T4 (#4 barge vs capture): reconnect adopts the live session (no restart
  from zero); sustained interruption still answered (S1)
- T5 (#5 pre-emptive): later slots given early are kept (S2 covers the
  full multi-slot utterance; T5 covers purpose+timeline-first)
- T6 (#6 unclear): garbled input gets the short warm re-ask with no LLM
  call (S5 covers garbage redirect)
- T7 (#7 visit flow): day -> ONE combined day+date+time ask -> book
- T8 (#8 off-script tone): responder prompt mandates warm human tone and
  bans robotic openers
"""

import base64
import json
import uuid

import pytest

import app.services.live_agent as live_agent_mod
from app.api.v1.ws import BARGE_IN_SPEECH_FRAMES, call_media_stream
from app.main import app
from app.services.live_agent import LiveAgentSession

SPEECH = base64.b64encode(bytes([200]) * 320).decode()
SILENCE = base64.b64encode(bytes([255]) * 320).decode()


class FakeWebSocket:
    def __init__(self, inbound):
        self.app = app
        self.inbound = inbound
        self.out = []
        self.closed_code = None
        self.accepted = False
        self.query_params = {"token": ""}
        import os

        self.query_params = {"token": os.environ.get("WEBHOOK_TOKEN", "")}

    async def accept(self):
        self.accepted = True

    async def receive_text(self):
        if self.inbound:
            return self.inbound.pop(0)
        from starlette.websockets import WebSocketDisconnect

        raise WebSocketDisconnect()

    async def send_text(self, text):
        self.out.append(json.loads(text))

    async def close(self, code=1000):
        self.closed_code = code


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Sim", "phone": "+919700000001", "city": "Jaipur"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call["id"]


def _new_session(call_id, db_sessionmaker, **kwargs):
    return LiveAgentSession(uuid.UUID(call_id), session_factory=db_sessionmaker, **kwargs)


# ------------------------------------------------------------- S1: barge-in


@pytest.mark.asyncio
async def test_s1_barge_in_then_coherent_continuation(client, db_sessionmaker):
    """Sustained interruption cancels playback, conversation still continues."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "sim-1"}})]
            + [json.dumps({"event": "media", "media": {"payload": SPEECH}})] * BARGE_IN_SPEECH_FRAMES
            + [json.dumps({"event": "media", "media": {"payload": SILENCE}})] * 35
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))

        kinds = [m.get("event") for m in ws.out]
        assert kinds[0] == "playAudio"  # greeting went out
        assert kinds.count("clearAudio") == 1  # interruption honoured once
        assert kinds.count("playAudio") >= 2  # ...then the reply still played
        assert ws.closed_code == 1000
    finally:
        app.state.live_session_factory = None


# ------------------------------------------------- S2: multi-slot utterance


@pytest.mark.asyncio
async def test_s2_multi_slot_single_utterance_end_to_end(client, db_sessionmaker, monkeypatch):
    """One spoken turn carrying type+location+budget jumps past all 3 asks."""
    now = [1000.0]
    monkeypatch.setattr(live_agent_mod, "_monotonic", lambda: now[0])
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker, silence_frames=5)
    await agent.start()

    async def fake_transcribe(audio):
        return "3 BHK flat in Jaipur for 50 lakh"

    agent._transcribe = fake_transcribe  # type: ignore[method-assign]

    async def one_utterance():
        for _ in range(2):
            assert await agent.process_audio(bytes([200]) * 320) is None
        turn = None
        for _ in range(8):
            turn = await agent.process_audio(bytes([255]) * 320)
            if turn is not None and (turn.audio or turn.reply):
                break
        return turn

    # First utterance lands on PERMISSION (identity confirm) but captures all.
    turn1 = await one_utterance()
    assert turn1 is not None and turn1.audio
    # Second identical utterance (a real retry seconds later, not jitter)
    # advances straight to the recap - no repeat of type/location/budget.
    now[0] += 10.0
    turn2 = await one_utterance()
    assert turn2 is not None and turn2.audio
    assert turn2.state == "SUMMARY_CONFIRM"
    assert "Correct?" in (turn2.reply or "")
    async with db_sessionmaker() as db:
        from app.crud import conversation as conv_crud

        session = await conv_crud.get_session(db, turn2.session_id)
        slots = await conv_crud.load_slots(session)
    assert slots.get("bhk") == "3bhk"
    assert slots.get("location") == "Jaipur"
    assert slots.get("budget") == "50 lakh"


# --------------------------------------- S3/S4: duplicates and late arrivals


@pytest.mark.asyncio
async def test_s3_late_duplicate_transcript_dropped(client, db_sessionmaker, monkeypatch):
    """Same words arriving again seconds later (STT delay) make no new turn."""
    now = [1000.0]
    monkeypatch.setattr(live_agent_mod, "_monotonic", lambda: now[0])
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker)

    first = await agent.handle_user_text("2 bhk")
    assert first.audio  # answered normally
    now[0] += 2.0  # late re-delivery inside the window
    dup = await agent.handle_user_text("2 bhk")
    assert dup.audio is None and dup.reply is None  # dropped silently
    now[0] += 5.0  # genuinely repeated much later -> answered again
    third = await agent.handle_user_text("2 bhk")
    assert third.audio


@pytest.mark.asyncio
async def test_s4_jitter_repeats_collapse_to_one_turn(client, db_sessionmaker, monkeypatch):
    """Three identical back-to-back STT events -> exactly one agent turn."""
    now = [1000.0]
    monkeypatch.setattr(live_agent_mod, "_monotonic", lambda: now[0])
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker)

    results = []
    for dt in (0.0, 0.5, 1.0):
        now[0] += dt
        results.append(await agent.handle_user_text("haan"))
    assert results[0].audio
    assert results[1].audio is None and results[2].audio is None


# ------------------------------------------------------- S5: garbage input


@pytest.mark.asyncio
async def test_s5_garbage_input_redirects_without_crash(client, db_sessionmaker):
    """Unclear input with no matching intent -> polite redirect, same state."""
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker)
    await agent.start()

    turn = await agent.handle_user_text("xyz blorpt fnord wobble")
    assert turn.reply  # never silent, never an exception
    # Fresh session: garbage at identity confirm still moves to PERMISSION
    # (availability check), never crashes, never goes terminal.
    assert turn.state in ("QUALIFICATION", "GREETING", "PERMISSION")
    assert turn.audio  # still speaks the redirect
    assert turn.terminal is False


# --------------------------------- S6: single provider failure != dead call


@pytest.mark.asyncio
async def test_s6_single_turn_failure_keeps_socket_alive(client, db_sessionmaker, monkeypatch):
    """One STT 500 mid-call must not kill the whole media socket.

    Live regression class: an unguarded await in the media loop closed the
    socket on the first provider error, leaving a billed-but-dead call.
    """
    from app.services.live_agent import LiveAgentSession as _Session

    async def boom(self, audio):
        raise RuntimeError("STT 500")

    monkeypatch.setattr(_Session, "_transcribe", boom)
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        inbound = (
            [json.dumps({"event": "start", "start": {"streamId": "sim-6"}})]
            + [json.dumps({"event": "media", "media": {"payload": SPEECH}})] * 5
            + [json.dumps({"event": "media", "media": {"payload": SILENCE}})] * 35
            + [json.dumps({"event": "stop"})]
        )
        ws = FakeWebSocket(inbound=inbound)
        await call_media_stream(ws, uuid.UUID(call_id))  # must not raise

        assert ws.closed_code == 1000  # graceful close via stop, not a crash
    finally:
        app.state.live_session_factory = None


# --------------------------------- T1: greeting needs zero STT (#1 latency)


@pytest.mark.asyncio
async def test_t1_greeting_speaks_with_zero_stt(client, db_sessionmaker, monkeypatch):
    """Initial latency guard: greeting must not wait on any STT roundtrip."""
    from app.services.live_agent import LiveAgentSession as _Session

    async def _boom(self, audio):
        raise AssertionError("greeting path must never call STT")

    monkeypatch.setattr(_Session, "_transcribe", _boom)
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        ws = FakeWebSocket(
            inbound=[json.dumps({"event": "start", "start": {"streamId": "t1"}})]
        )
        await call_media_stream(ws, uuid.UUID(call_id))
        plays = [m for m in ws.out if m.get("event") == "playAudio"]
        assert len(plays) == 1  # greeting, instantly, no listen-first
    finally:
        app.state.live_session_factory = None


# --------------------------------- T2: per-turn stage timings (#2 latency)


@pytest.mark.asyncio
async def test_t2_audio_turn_records_full_stage_timings(client, db_sessionmaker):
    """Every audio-path turn measures vad_wait/stt/engine/tts/total ms."""
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker, silence_frames=5)
    await agent.start()

    async def fake_transcribe(audio):
        return "2 bhk"

    agent._transcribe = fake_transcribe  # type: ignore[method-assign]
    for _ in range(2):
        assert await agent.process_audio(bytes([200]) * 320) is None
    turn = None
    for _ in range(8):
        turn = await agent.process_audio(bytes([255]) * 320)
        if turn is not None and (turn.audio or turn.reply):
            break
    assert turn is not None and turn.audio
    timings = agent._last_timings
    assert set(timings) >= {"vad_wait_ms", "stt_ms", "engine_ms", "tts_ms", "total_ms"}
    assert all(v >= 0 for v in timings.values())
    assert timings["total_ms"] >= timings["stt_ms"]


# --------------------------------- T3: double start, one greeting (#3)


@pytest.mark.asyncio
async def test_t3_double_start_speaks_greeting_once(client, db_sessionmaker):
    """Retried answer-fetch must not overlap two greetings."""
    call_id = await _create_call(client)
    app.state.live_session_factory = db_sessionmaker
    try:
        ws = FakeWebSocket(
            inbound=[
                json.dumps({"event": "start", "start": {"streamId": "t3-a"}}),
                json.dumps({"event": "start", "start": {"streamId": "t3-b"}}),
                json.dumps({"event": "stop"}),
            ]
        )
        await call_media_stream(ws, uuid.UUID(call_id))
        plays = [m for m in ws.out if m.get("event") == "playAudio"]
        assert len(plays) == 1
    finally:
        app.state.live_session_factory = None


# --------------------------------- T4: session adopted on reconnect (#4)


@pytest.mark.asyncio
async def test_t4_reconnect_adopts_live_session(client, db_sessionmaker):
    """A new socket mid-call continues the same session (no restart)."""
    call_id = await _create_call(client)
    first = _new_session(call_id, db_sessionmaker)
    await first.start()
    await first.handle_user_text("2 bhk", dedupe=False)

    second = _new_session(call_id, db_sessionmaker)
    await second.create_session()
    assert second.session_id == first.session_id
    async with db_sessionmaker() as db:
        from app.crud import conversation as conv_crud

        slots = await conv_crud.load_slots(
            await conv_crud.get_session(db, second.session_id)
        )
    assert slots.get("bhk") == "2bhk"


# --------------------------------- T5: later slots first (#5 pre-emptive)


@pytest.mark.asyncio
async def test_t5_later_slots_first_are_kept(client, db_sessionmaker):
    """purpose+timeline before type/location -> kept, type asked (no repeat)."""
    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker)
    await agent.start()
    # Go through proper flow: GREETING -> PERMISSION -> QUALIFICATION
    turn = await agent.handle_user_text("haan, batao", dedupe=False)  # permission granted
    assert turn.state == "PERMISSION"
    turn = await agent.handle_user_text("haan", dedupe=False)  # permission granted -> QUALIFICATION
    assert turn.state == "QUALIFICATION"
    assert "kis type ki property" in turn.reply
    # Now provide purpose+timeline upfront
    turn = await agent.handle_user_text("self use ke liye, within 2 months", dedupe=False)
    assert turn.reply
    assert "kis type ki property" in turn.reply
    async with db_sessionmaker() as db:
        from app.crud import conversation as conv_crud

        slots = await conv_crud.load_slots(
            await conv_crud.get_session(db, turn.session_id)
        )
    assert slots.get("purpose") == "self_use"
    assert slots.get("timeline") == "2 months"


# --------------------------------- T6: unclear re-ask, no LLM (#6)


@pytest.mark.asyncio
async def test_t6_garbled_input_short_reask_no_llm(client, db_sessionmaker, monkeypatch):
    """Mumble -> one warm short re-ask, zero LLM roundtrips."""
    from app.conversation.engine import ConversationEngine
    from app.conversation.intents import Intent

    calls = []

    class CountingLLM:
        async def classify(self, text, state):
            calls.append(text)
            from app.conversation.intents import Confidence, IntentResult

            return IntentResult(Intent.OTHER, 0.8, {})

    from app.conversation.states import ConvState

    engine = ConversationEngine(llm=CountingLLM())
    turn = await engine.step(
        state=ConvState.QUALIFICATION, slots={},
        user_input="achha ji",  # bare smalltalk (in _BARE_SMALLTALK) -> no LLM
        persona={"company": "C", "agent_name": "Neha", "properties": {}},
    )
    assert calls == []
    assert "aawaz thodi kat gayi" in turn.reply
    assert "kis type ki property" in turn.reply


# --------------------------------- T7: consolidated visit ask (#7)


@pytest.mark.asyncio
async def test_t7_visit_day_covers_date_and_time_at_once(client, db_sessionmaker):
    """'Sunday' -> weekday + actual date + both times in ONE reply."""
    from app.conversation.engine import _upcoming_weekday_date
    from app.conversation.states import ConvState

    call_id = await _create_call(client)
    agent = _new_session(call_id, db_sessionmaker)
    await agent.start()
    turn = await agent.handle_user_text("haan", dedupe=False)
    assert turn.state == "PERMISSION"
    # Jump straight to closing like an interested customer would.
    from app.conversation.engine import ConversationEngine as _E

    eng = _E()
    t = await eng.step(
        state=ConvState.CLOSING, slots={}, user_input="Sunday",
        persona={"company": "C", "agent_name": "Neha", "properties": {}},
    )
    expected = _upcoming_weekday_date("sunday")
    assert "Ravivaar" in t.reply
    assert f"{expected.day} {expected.strftime('%B')}" in t.reply
    assert "11 AM" in t.reply and "4 PM" in t.reply


# --------------------------------- T8: warm responder tone (#8)


def test_t8_responder_prompt_bans_robotic_tone():
    """Off-script prompt mandates warmth, bans computer-speak."""
    from app.conversation.engine import _RESPONDER_SYSTEM

    lowered = _RESPONDER_SYSTEM.lower()
    assert "warm" in lowered
    assert "never say" in lowered and "ai" in lowered
    assert "follow-up question exactly" in lowered
    assert "as an ai" not in lowered
