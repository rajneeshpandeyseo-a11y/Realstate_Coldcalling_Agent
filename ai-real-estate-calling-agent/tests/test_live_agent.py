"""Tests for the live agent audio turn loop (PHASE 11)."""

import uuid

import pytest

from app.services import audio_codec
from app.services.live_agent import LiveAgentSession


def _is_mulaw_wav(turn_audio: bytes) -> bool:
    """Audio sent to Plivo must be 8kHz mu-law that decodes to a valid WAV."""
    if not turn_audio:
        return False
    if turn_audio[:4] == b"RIFF":
        return False  # raw TTS wav not transcoded
    return audio_codec.mulaw_to_wav16k(turn_audio)[:4] == b"RIFF"


async def _create_call(client):
    lead = (await client.post(
        "/api/v1/leads", json={"name": "Neha", "phone": "+919600000010", "city": "Noida"}
    )).json()
    call = (await client.post("/api/v1/calls", json={"lead_id": lead["id"]})).json()
    return call


@pytest.mark.asyncio
async def test_start_speaks_greeting_and_creates_session(client, db_sessionmaker):
    call = await _create_call(client)
    agent = LiveAgentSession(uuid.UUID(call["id"]), session_factory=db_sessionmaker)

    turn = await agent.start()

    assert turn.session_id is not None
    assert turn.audio  # synthesised greeting to play back
    assert _is_mulaw_wav(turn.audio)  # transcoded to Plivo 8kHz mu-law
    assert turn.terminal is False

    # Greeting is persisted as the first agent message in the transcript.
    async with db_sessionmaker() as db:
        from app.crud import conversation as conv_crud

        msgs = await conv_crud.list_messages(db, turn.session_id)
    assert msgs
    assert msgs[0].speaker == "agent"
    assert msgs[0].text.startswith("Namaste")


@pytest.mark.asyncio
async def test_flush_empty_utterance_returns_no_turn(client, db_sessionmaker):
    call = await _create_call(client)
    agent = LiveAgentSession(uuid.UUID(call["id"]), session_factory=db_sessionmaker)
    await agent.start()

    turn = await agent.flush()
    assert turn.audio is None
    assert turn.transcript is None


@pytest.mark.asyncio
async def test_flush_zero_cost_pipeline_produces_audio(client, db_sessionmaker):
    call = await _create_call(client)
    agent = LiveAgentSession(uuid.UUID(call["id"]), session_factory=db_sessionmaker)
    await agent.start()

    # Feed one speech chunk then force the turn.
    speech = bytes([200]) * 320  # high energy -> mock STT returns a canned transcript
    assert await agent.process_audio(speech) is None

    turn = await agent.flush()
    assert turn.transcript  # mock transcript
    assert turn.reply  # engine reply
    assert turn.audio  # TTS audio to play back
    assert _is_mulaw_wav(turn.audio)
    assert turn.terminal is False


def test_is_system_announcement_filters_plivo_notices():
    from app.services.live_agent import is_system_announcement

    assert is_system_announcement("This call is now being recorded.") is True
    assert is_system_announcement("call recording has now ended") is True
    assert is_system_announcement("  THIS CALL MAY BE RECORDED  ") is True
    assert is_system_announcement("haan, 2 BHK chahiye") is False
    assert is_system_announcement("Hello") is False
    assert is_system_announcement("") is False


@pytest.mark.asyncio
async def test_flush_drops_system_announcement_silently(client, db_sessionmaker):
    """A recording notice in the audio stream never reaches the engine."""
    from app.services.live_agent import LiveAgentSession

    call = await _create_call(client)
    agent = LiveAgentSession(uuid.UUID(call["id"]), session_factory=db_sessionmaker)
    await agent.start()

    async def fake_transcribe(audio):
        return "This call is now being recorded."

    agent._transcribe = fake_transcribe  # type: ignore[method-assign]
    agent._buffer.extend(bytes([200]) * 320)
    agent._had_speech = True

    turn = await agent.flush()
    assert turn.transcript == "This call is now being recorded."
    assert turn.audio is None
    assert turn.reply is None

    async with db_sessionmaker() as db:
        from app.crud import conversation as conv_crud

        msgs = await conv_crud.list_messages(db, turn.session_id)
    assert all("being recorded" not in m.text for m in msgs)


def test_vad_defaults_are_human_like(db_sessionmaker):
    """End-of-speech gap ~400ms (not 600ms+) for human-like turn-taking."""
    agent = LiveAgentSession(uuid.uuid4(), session_factory=db_sessionmaker)
    assert agent.silence_frames == 20


@pytest.mark.asyncio
async def test_consecutive_drops_speak_line_check(client, db_sessionmaker):
    """Two dead-air turns in a row -> line-check nudge, not more silence."""
    call = await _create_call(client)
    agent = LiveAgentSession(uuid.UUID(call["id"]), session_factory=db_sessionmaker)
    await agent.start()

    async def fake_empty(audio):
        return ""

    agent._transcribe = fake_empty  # type: ignore[method-assign]

    async def one_drop():
        agent._buffer.extend(bytes([200]) * 320)
        agent._had_speech = True
        return await agent.flush()

    first = await one_drop()
    assert first.audio is None and first.reply is None  # silent once
    second = await one_drop()
    assert second.audio  # line-check spoken
    assert "sun pa rahe" in (second.reply or "")
    third = await one_drop()
    assert third.audio is None and third.reply is None  # streak restarted


@pytest.mark.asyncio
async def test_silence_detection_flushes_after_end_of_speech(client, db_sessionmaker):
    call = await _create_call(client)
    agent = LiveAgentSession(
        uuid.UUID(call["id"]), session_factory=db_sessionmaker, silence_frames=3
    )
    await agent.start()

    speech = bytes([200]) * 160  # above RMS threshold (μ-law 0xC8 decodes hot)
    silence = bytes([255]) * 160  # μ-law silence (0xFF -> linear 0)

    for _ in range(2):
        assert await agent.process_audio(speech) is None

    turn = None
    for _ in range(5):
        turn = await agent.process_audio(silence)
        if turn is not None:
            break
    assert turn is not None
    assert turn.audio
    assert turn.transcript  # whole utterance transcribed at once
