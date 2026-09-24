"""Real-time audio WebSocket - /api/v1/ws/calls/{call_id}.

Bidirectional media socket consumed by Plivo's ``<Stream>`` element (see the
``answer`` webhook). Plivo opens this connection, sends a ``start`` event with
call metadata, then streams ~20ms ``media`` events of base64 audio from the
caller. We feed those chunks to a :class:`LiveAgentSession` (STT -> engine ->
TTS) and reply with ``playAudio`` events carrying synthesised audio.

The event protocol matches Plivo's Audio Streaming WebSocket API:

    Plivo -> us : {"event": "start", "start": {callId, streamId, ...}}
    Plivo -> us : {"event": "media", "media": {"payload": "<b64>"}}
    us    -> Plivo: {"event": "playAudio", "media": {"contentType": "...",
                 "sampleRate": <int>, "payload": "<b64>"}}

When the conversation engine terminates the call (TurnResult.terminal) we
hang up the Plivo call through the provider and finalise the CRM outcome
(requirements + site visit), so a live call is never left dangling.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.logging_config import get_logger
from app.services.live_agent import LiveAgentSession

log = get_logger("app.api.v1.ws")

router = APIRouter(prefix="/ws", tags=["websocket"])


def _session_factory(websocket: WebSocket):
    """Use an override bound to the app (for tests) or the default factory."""
    try:
        override = getattr(getattr(websocket, "app", None), "live_session_factory", None)
    except AttributeError:
        override = None
    return override or AsyncSessionLocal


def _play_audio(turn) -> dict:
    payload = base64.b64encode(turn.audio).decode("ascii") if turn.audio else ""
    return {
        "event": "playAudio",
        "media": {
            "contentType": turn.output_content_type,
            "sampleRate": turn.output_sample_rate,
            "payload": payload,
        },
        "agent": {
            "reply": turn.reply,
            "state": turn.state,
            "terminal": turn.terminal,
        },
    }


# PHASE 1.3 barge-in: Plivo clears queued TTS playback on `clearAudio`.
# Without this the caller hears the full old reply even after interrupting.
# Sustained speech while our audio is playing triggers one clearAudio per
# playback: 25 frames x ~20ms ~= 500ms. A short "haan/yes" backchannel
# ("sun raha hoon") must NOT chop our question mid-sentence - only a real,
# continued utterance yields the floor. Fast enough to feel responsive,
# calm enough not to stutter-repeat when the caller hums along.
BARGE_IN_SPEECH_FRAMES = 25
# PHASE 1.3b post-reply grace: right after we answer an utterance, the tail
# of that SAME utterance is still streaming in. Without a grace window those
# tail frames instantly clearAudio-cancel our just-sent reply before Plivo
# plays it (caller hears silence, repeats themselves, loop). Genuine
# interruptions arriving after the grace still cancel normally. The opening
# greeting has no grace (caller talking over it is a real barge-in).
BARGE_IN_POST_REPLY_GRACE_SEC = 2.0

# PHASE 1.5 no-response: caller silent this long (and our playback finished,
# so they are not just listening) -> polite nudge; after MAX_NUDGES nudges,
# wait GOODBYE_SEC for any last word, then graceful hangup + no_response.
NO_RESPONSE_TIMEOUT_SEC = 15.0
MAX_NUDGES = 2
GOODBYE_SEC = 8.0
# Playout slack over the raw audio duration (network + Plivo queueing).
PLAYBACK_SLACK_SEC = 2.0


def _extract_stream_id(event: dict) -> str | None:
    """Pull the Plivo streamId from a `start` event (format-tolerant)."""
    if not isinstance(event, dict):
        return None
    start = event.get("start") or {}
    if isinstance(start, dict):
        for key in ("streamId", "stream_id", "streamid"):
            val = start.get(key)
            if val:
                return str(val)
    for key in ("streamId", "stream_id", "streamid"):
        val = event.get(key)
        if val:
            return str(val)
    return None


def _clear_audio(stream_id: str | None) -> dict | None:
    """Build the Plivo `clearAudio` interruption event (None without id)."""
    if not stream_id:
        return None
    return {"event": "clearAudio", "streamId": stream_id}


def _checkpoint(stream_id: str | None, name: str) -> dict | None:
    """Mark a playback point so Plivo confirms `playedStream` when heard.

    Without checkpoints we never learn our audio finished, so silence would
    wrongly accrue while the caller is still listening (and barge-in would
    stay armed forever). None when the stream id is unknown (tests/local).
    """
    if not stream_id:
        return None
    return {"event": "checkpoint", "streamId": stream_id, "name": name}


def _playback_until(audio: bytes | None, now: float | None = None) -> float | None:
    """Wall-clock time our just-sent audio should be done playing (8kHz mu-law)."""
    if not audio:
        return None
    at = _monotonic() if now is None else now
    return at + len(audio) / 8000.0 + PLAYBACK_SLACK_SEC


async def _recent_greeting(call_id, session_factory, within_sec: float = 120.0) -> bool:
    """True when this call already heard the greeting moments ago.

    Plivo may fetch answer_url twice (retry) and open a second media
    stream; without this guard both sockets speak the greeting at once
    (the "two lines overlapping" symptom). Fail-open: any doubt means
    speak rather than leave the caller in silence.
    """
    try:
        from datetime import datetime, timezone

        from app.crud import conversation as conv_crud

        async with session_factory() as db:
            msgs = await conv_crud.list_messages_for_call(db, call_id)
        now = datetime.now(timezone.utc)
        for m in reversed(msgs):
            if getattr(m, "speaker", None) != "agent":
                continue
            if (getattr(m, "state", None) or "") != "GREETING":
                continue
            ts = getattr(m, "timestamp", None)
            if ts is None:
                return True
            stamp = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
            return (now - stamp).total_seconds() < within_sec
        return False
    except Exception:
        return False


def _monotonic() -> float:
    """Clock indirection so tests can time-travel (production: wall clock)."""
    return time.monotonic()


def _no_response_action(
    *,
    now: float,
    last_voice: float,
    playback_until: float | None,
    nudge_count: int,
    second_nudge_at: float | None,
    timeout: float = NO_RESPONSE_TIMEOUT_SEC,
    max_nudges: int = MAX_NUDGES,
    goodbye: float = GOODBYE_SEC,
) -> str:
    """Pure no-response decision: 'ok' | 'nudge' | 'hangup' (PHASE 1.5)."""
    if now - last_voice < timeout:
        return "ok"
    if playback_until is not None and now < playback_until:
        return "ok"  # our audio still playing; caller may just be listening
    if nudge_count >= max_nudges:
        if second_nudge_at is not None and now - second_nudge_at >= goodbye:
            return "hangup"
        return "ok"
    return "nudge"


async def _finalize_terminal_call(call_id: uuid.UUID, agent: LiveAgentSession) -> None:
    """Hang up the call and persist the CRM outcome for a terminal turn.

    Runs the telephony provider's hangup for the Plivo call and moves the call
    state machine to its terminal status, then persists requirements/site-visit
    when applicable. Executed in a detached way that never throws into the
    WebSocket loop.
    """
    from app.crud import call as call_crud
    from app.models.enums import CallStatus
    from app.services import call as call_service
    from app.services.live_outcome import finalize_live_call

    try:
        session_factory = getattr(agent, "session_factory", None) or AsyncSessionLocal
        async with session_factory() as db:
            call = await call_crud.get_call(db, call_id)
            if call is None:
                return
            # Ask the telephony provider to hang up the live Plivo call.
            provider_call_id = call.provider_call_id or ""
            if provider_call_id:
                from app.providers import get_telephony_provider

                try:
                    await get_telephony_provider().hangup(provider_call_id)
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("live hangup failed", extra={"ctx": {"error": str(exc)}})
            # Map terminal conversation outcome onto the call state machine,
            # mirroring CallOrchestrator: DO_NOT_CALL terminal -> DO_NOT_CALL;
            # a CALLBACK state reached earlier -> CALLBACK_REQUESTED; else
            # COMPLETED (CALLBACK/visit end states land on END).
            from app.crud import conversation as conv_crud

            callback_reached = False
            try:
                msgs = await conv_crud.list_messages_for_call(db, call_id)
                states = [m.state for m in msgs if m.speaker == "agent" and m.state]
                callback_reached = "CALLBACK" in states
            except Exception:
                pass
            terminal = getattr(agent, "terminal_state", None)
            target = CallStatus.COMPLETED
            if terminal == "DO_NOT_CALL":
                target = CallStatus.DO_NOT_CALL
            elif callback_reached:
                target = CallStatus.CALLBACK_REQUESTED
            if call.status not in (target.value, CallStatus.COMPLETED.value):
                try:
                    await call_service.transition_call(
                        db, call, target,
                        event_data={"source": "websocket", "terminal": terminal},
                    )
                except call_service.IllegalTransitionError:
                    pass
            await finalize_live_call(db, call, session_id=agent.session_id)
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "terminal call finalisation failed",
            extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
        )


async def _finalize_no_response(call_id: uuid.UUID, agent: LiveAgentSession) -> None:
    """Hang up a silent call and record CRM status no_response (PHASE 1.5).

    Runs the provider hangup, moves IN_PROGRESS/ANSWERED calls to the terminal
    NO_RESPONSE status (lead untouched except the AUTO_RETRY-guarded retry,
    mirroring NO_ANSWER), and persists whatever requirements exist (usually
    none). Never throws into the WebSocket loop.
    """
    from app.crud import call as call_crud
    from app.models.enums import CallStatus
    from app.services import call as call_service
    from app.services.live_outcome import finalize_live_call

    try:
        session_factory = getattr(agent, "session_factory", None) or AsyncSessionLocal
        async with session_factory() as db:
            call = await call_crud.get_call(db, call_id)
            if call is None:
                return
            provider_call_id = call.provider_call_id or ""
            if provider_call_id:
                from app.providers import get_telephony_provider

                try:
                    await get_telephony_provider().hangup(provider_call_id)
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("no-response hangup failed", extra={"ctx": {"error": str(exc)}})
            # Only live (answered) calls can go no_response; anything else
            # (e.g. already COMPLETED by a racing webhook) is left alone.
            if call.status in (CallStatus.IN_PROGRESS.value, CallStatus.ANSWERED.value):
                try:
                    await call_service.transition_call(
                        db, call, CallStatus.NO_RESPONSE,
                        event_data={"source": "websocket", "reason": "no_response"},
                    )
                except call_service.IllegalTransitionError:
                    pass
            await finalize_live_call(db, call, session_id=agent.session_id)
            await db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "no-response finalisation failed",
            extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
        )


@router.websocket("/calls/{call_id}")
async def call_media_stream(websocket: WebSocket, call_id: uuid.UUID):
    """Run a resilient bidirectional media loop.

    Important reliability fix: receiving Plivo media is deliberately separated
    from STT/LLM/TTS processing. The old loop awaited STT/engine/TTS directly
    inside ``receive_text()`` handling, which meant the application stopped
    reading the WebSocket for several seconds. During that time callers could
    keep speaking, but their media was not consumed promptly, causing missed
    speech, delayed turns, false barge-ins and apparent agent inconsistency.

    The reader below continuously drains the socket into a bounded FIFO queue;
    the worker processes those events in order. This keeps the transport alive
    while preserving the original STT -> conversation engine -> TTS concept.
    """
    # Reject unauthorised media sockets when a webhook token is configured.
    if settings.WEBHOOK_TOKEN:
        qp = getattr(websocket, "query_params", None) or {}
        provided = qp.get("token") or ""
        if not provided or not hmac.compare_digest(provided, settings.WEBHOOK_TOKEN):
            await websocket.close(code=4401)
            return

    await websocket.accept()

    caller_name: str | None = None
    try:
        from app.crud import call as call_crud
        from app.crud import lead as lead_crud

        async with AsyncSessionLocal() as _db:
            _call = await call_crud.get_call(_db, call_id)
            if _call is not None and getattr(_call, "lead_id", None):
                _lead = await lead_crud.get_lead(_db, _call.lead_id)
                if _lead is not None:
                    caller_name = getattr(_lead, "name", None) or None
    except Exception as exc:
        log.warning(
            "caller lookup failed",
            extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
        )

    agent = LiveAgentSession(
        call_id,
        session_factory=_session_factory(websocket),
        caller_name=caller_name,
    )
    log.info("ws connect", extra={"ctx": {"call_id": str(call_id)}})

    # A 20ms Plivo media stream produces ~50 events/sec. A large bounded queue
    # prevents a slow provider call from stopping socket reads while still
    # placing an upper bound on memory use if a provider becomes unavailable.
    EVENT_QUEUE_MAX = 4000
    queue: asyncio.Queue[tuple[str, dict | None, float] | None] = asyncio.Queue(
        maxsize=EVENT_QUEUE_MAX
    )
    reader_error: BaseException | None = None

    async def _reader() -> None:
        """Continuously drain the WebSocket so caller audio is never blocked."""
        nonlocal reader_error
        try:
            while True:
                raw = await websocket.receive_text()
                received_at = _monotonic()
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning(
                        "invalid websocket json ignored",
                        extra={"ctx": {"call_id": str(call_id)}},
                    )
                    continue

                # Queue every valid event in arrival order. Do not parse media
                # audio in the reader; keeping this task cheap is what makes
                # it effective at draining the transport continuously.
                item = (str(event.get("event") or ""), event, received_at)
                await queue.put(item)
                if item[0] == "stop":
                    return
        except WebSocketDisconnect:
            # Normal remote hang-up. A sentinel lets the worker flush and
            # cleanly finalize without trying to receive from a dead socket.
            try:
                await queue.put(None)
            except Exception:
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - transport dependent
            reader_error = exc
            log.warning(
                "websocket reader failed",
                extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
            )
            try:
                await queue.put(None)
            except Exception:
                pass

    reader_task = asyncio.create_task(_reader(), name=f"plivo-reader-{call_id}")

    stream_id: str | None = None
    playback_until: float | None = None
    playback_started_at: float | None = None
    speech_frames = 0
    play_seq = 0
    last_reply_at: float | None = None
    last_voice = _monotonic()
    second_nudge_at: float | None = None

    def _playback_active(now: float | None = None) -> bool:
        at = _monotonic() if now is None else now
        return playback_until is not None and at < playback_until

    async def _send_playback(turn, arrived_at: float | None = None) -> None:
        """Send audio and track when its triggering event arrived."""
        nonlocal playback_until, playback_started_at, play_seq
        now = _monotonic()
        await websocket.send_text(json.dumps(_play_audio(turn)))
        if stream_id:
            play_seq += 1
            checkpoint = _checkpoint(stream_id, f"play-{play_seq}")
            if checkpoint is not None:
                await websocket.send_text(json.dumps(checkpoint))
        if turn.audio:
            playback_started_at = now if arrived_at is None else arrived_at
            playback_until = _playback_until(turn.audio, now=now)

    async def _silence_tick() -> bool:
        nonlocal second_nudge_at
        action = _no_response_action(
            now=_monotonic(),
            last_voice=last_voice,
            playback_until=playback_until,
            nudge_count=agent.nudge_count,
            second_nudge_at=second_nudge_at,
        )
        if action == "nudge":
            try:
                nturn = await agent.nudge()
            except Exception as exc:
                log.warning(
                    "no-response nudge failed",
                    extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
                )
                return False
            if nturn and nturn.audio:
                await _send_playback(nturn)
            log.info(
                "no-response nudge",
                extra={"ctx": {"call_id": str(call_id), "nudge": agent.nudge_count}},
            )
            if agent.nudge_count >= MAX_NUDGES:
                second_nudge_at = _monotonic()
        elif action == "hangup":
            await _finalize_no_response(call_id, agent)
            await websocket.close(code=1000)
            return True
        return False

    async def _flush_and_close() -> None:
        """Persist any final utterance, but never synthesize onto a dead call."""
        if (
            agent.nudge_count >= MAX_NUDGES
        ):
            await _finalize_no_response(call_id, agent)
            try:
                await websocket.close(code=1000)
            except Exception:
                pass
            return
        try:
            turn = await agent.flush(speak=False)
        except Exception as exc:
            log.warning(
                "live stop-flush failed",
                extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
            )
            turn = None
        if turn and turn.terminal:
            await _finalize_terminal_call(call_id, agent)
        elif turn is not None and getattr(agent, "terminal", False):
            await _finalize_terminal_call(call_id, agent)
        if not getattr(websocket, "client_state", None) or getattr(
            getattr(websocket, "client_state", None), "name", "CONNECTED"
        ) != "DISCONNECTED":
            try:
                await websocket.close(code=1000)
            except Exception:
                pass

    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=NO_RESPONSE_TIMEOUT_SEC
                )
            except asyncio.TimeoutError:
                if await _silence_tick():
                    return
                continue

            if item is None:
                await _flush_and_close()
                return

            kind, event, received_at = item
            event = event or {}

            if kind == "start":
                stream_id = _extract_stream_id(event) or stream_id
                last_voice = received_at
                if await _recent_greeting(call_id, _session_factory(websocket)):
                    log.info(
                        "ws start already greeted, joining silently",
                        extra={"ctx": {"call_id": str(call_id)}},
                    )
                    speech_frames = 0
                    continue
                try:
                    turn = await agent.start()
                except Exception as exc:
                    log.warning(
                        "live greeting failed",
                        extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
                    )
                    turn = None
                if turn and turn.audio:
                    await _send_playback(turn, arrived_at=received_at)
                    speech_frames = 0
                continue

            if kind == "media":
                media = event.get("media") or {}
                encoded = media.get("payload") or ""
                if not encoded:
                    continue
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except (ValueError, TypeError, base64.binascii.Error):
                    log.warning(
                        "invalid media payload ignored",
                        extra={"ctx": {"call_id": str(call_id)}},
                    )
                    continue
                if not chunk:
                    continue

                try:
                    speech = agent.is_speech(chunk)
                except Exception:
                    speech = False

                if speech:
                    # Queue processing can lag behind transport arrival. Never
                    # move the voice clock backwards when an older queued frame
                    # is finally processed.
                    last_voice = max(last_voice, received_at)
                    if agent.nudge_count >= MAX_NUDGES:
                        agent.nudge_count = 0
                        second_nudge_at = None

                # Only media that ARRIVED after playback began may interrupt
                # it. This is crucial with the reader queue: media captured
                # while STT/LLM/TTS was running is older than the reply and is
                # not a new barge-in merely because the worker processes it
                # after the reply is sent.
                media_during_playback = (
                    speech
                    and playback_started_at is not None
                    and received_at >= playback_started_at
                    and _playback_active(received_at)
                )
                if media_during_playback:
                    speech_frames += 1
                elif not speech:
                    speech_frames = 0

                if speech_frames >= BARGE_IN_SPEECH_FRAMES and media_during_playback:
                    # Use the frame's arrival time, not the worker's current
                    # time. The worker may be seconds behind while STT/TTS is
                    # running; otherwise a tail frame that was harmless when
                    # it arrived could become a false interruption later.
                    in_grace = (
                        last_reply_at is not None
                        and received_at - last_reply_at < BARGE_IN_POST_REPLY_GRACE_SEC
                    )
                    if in_grace:
                        # Do not carry the answered utterance's tail into a
                        # later, genuine interruption.
                        speech_frames = 0
                    else:
                        clear = _clear_audio(stream_id)
                        if clear is not None:
                            try:
                                await websocket.send_text(json.dumps(clear))
                            except Exception as exc:
                                log.debug(
                                    "clearAudio send failed",
                                    extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
                                )
                        try:
                            agent.interrupt()
                        except Exception:
                            pass
                        playback_until = None
                        playback_started_at = None
                        speech_frames = 0

                # The expensive pipeline is isolated to the worker task. The
                # reader continues draining incoming audio in parallel.
                try:
                    turn = await agent.process_audio(chunk)
                except Exception as exc:
                    log.warning(
                        "live turn failed, continuing",
                        extra={"ctx": {"call_id": str(call_id), "error": str(exc)}},
                    )
                    turn = None

                if turn and turn.audio:
                    await _send_playback(turn, arrived_at=received_at)
                    last_reply_at = received_at
                    speech_frames = 0

                if turn is not None and turn.reply:
                    agent.nudge_count = 0
                    second_nudge_at = None

                if turn and turn.terminal:
                    await _finalize_terminal_call(call_id, agent)
                    await websocket.close(code=1000)
                    return

                if await _silence_tick():
                    return
                continue

            if kind in ("playedStream", "played"):
                # Do not trust an unrelated/old confirmation to cancel a newer
                # playback. The checkpoint name is not present on every Plivo
                # variant, so only clear the local estimate if there is no
                # newer send in flight.
                playback_until = None
                playback_started_at = None
                speech_frames = 0
                continue

            if kind in ("clearedAudio", "cleared"):
                playback_until = None
                playback_started_at = None
                speech_frames = 0
                continue

            if kind == "stop":
                await _flush_and_close()
                return

            # DTMF and future provider events are intentionally ignored.

    except WebSocketDisconnect:
        pass
    finally:
        if not reader_task.done():
            reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, WebSocketDisconnect):
            pass
        except Exception:
            pass
        log.info("ws close", extra={"ctx": {"call_id": str(call_id), "reader_error": str(reader_error) if reader_error else None}})
