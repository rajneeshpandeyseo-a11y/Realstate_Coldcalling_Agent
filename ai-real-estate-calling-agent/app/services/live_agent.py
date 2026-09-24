"""Live agent session: real-time audio turn loop over the conversation engine.

Bridges the Plivo audio-stream WebSocket (or any live audio source) to the
deterministic conversation engine via the provider abstraction:

    caller audio (chunks) -> STT -> engine turn (deterministic rules + LLM
    fallback) -> TTS -> synthesised audio to play back to the caller.

The session owns a ConversationService so every turn is persisted to the
transcript exactly like the HTTP conversation API. It uses silent-interval
detection (simple RMS energy) to know when the caller has finished speaking,
then flushes the accumulated audio through the pipeline. All provider calls go
through the configurable registry, so in MOCK_MODE the whole live loop is
zero-cost and deterministic.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.conversation.engine import (
    ConversationEngine,
    ProviderLLMFallback,
    ProviderLLMResponder,
)
from app.config import settings
from app.crud import conversation as conv_crud
from app.logging_config import get_logger
from app.providers import (
    STTProvider,
    TTSProvider,
    get_stt_provider,
    get_tts_provider,
)
from app.services.conversation import ConversationService
from app.services.pii import redact_pii

log = get_logger("app.services.live_agent")


def _log_snip(text: str | None, n: int = 60) -> str:
    """Short PII-redacted snippet for turn logs (never raw contact data)."""
    try:
        return redact_pii((text or "")[:n])
    except Exception:
        return (text or "")[:n]


def _monotonic() -> float:
    """Clock indirection so tests can time-travel (production: wall clock)."""
    return time.monotonic()


# Exact-duplicate STT suppression window (network jitter / re-transcribed
# tail): the same words twice within seconds is one utterance, not two
# turns. Without this a duplicated transcript burns a full engine+TTS turn
# and sounds like the agent "didn't listen" the first time.
DEDUP_WINDOW_SEC = 4.0

# Audio buffering bounds (8kHz mu-law ~= 8000 bytes/sec of caller audio).
# Leading silence is never buffered, and the buffer is capped: without this
# a quiet minute piles up unheard audio that STT then bills in full and
# answers minutes late (stale replies / "purani baat ka jawab").
MAX_BUFFER_BYTES = 8000 * 20


def _is_unintelligible(transcript: str) -> bool:
    """True when the STT text carries no real words (noise/hallucination).

    Catches outputs like "???", "...", "???" so the agent stays silent and
    keeps listening instead of reprompt-spamming the caller.
    """
    import re as _re

    stripped = (transcript or "").strip()
    if not stripped:
        return True
    words = _re.findall(r"[a-zA-Z\u0900-\u097F']+", stripped)
    return len(words) == 0


# Telephony system announcements (Plivo recording notices etc.) that leak
# into the caller audio stream. They are NOT the customer speaking: answering
# them confuses the flow (a "recording has ended" notice once triggered a
# company-intro side answer), so they are dropped silently like noise.
_SYSTEM_ANNOUNCEMENTS = (
    "this call is now being recorded",
    "call is now being recorded",
    "this call is being recorded",
    "call is being recorded",
    "this call may be recorded",
    "call may be recorded",
    "call recording has now ended",
    "recording has now ended",
    "call recording has started",
)


def is_system_announcement(transcript: str) -> bool:
    """True when the STT text is a telephony system notice, not the caller."""
    lowered = (transcript or "").strip().lower()
    return any(phrase in lowered for phrase in _SYSTEM_ANNOUNCEMENTS)


async def prewarm_greeting_for_lead(
    caller_name: str | None, persona: dict | None = None
) -> None:
    """Synthesise the personalised greeting once, ahead of answer.

    Called right after the dial is placed so the ~4s Sarvam synthesis happens
    during the ringing window. The TTS cache is process-wide, so when the
    customer answers, the greeting plays instantly instead of after seconds
    of silence (during which callers hang up). Best-effort: never raises.
    """
    try:
        if settings.is_mock:
            return
        from app.conversation.states import ConvState

        engine = ConversationEngine()
        turn = await engine.step(
            state=ConvState.GREETING,
            first_turn=True,
            persona=persona or DEFAULT_PERSONA,
            caller_name=caller_name,
        )
        if turn.reply:
            await get_tts_provider().synthesize(turn.reply)
            log.info("personalised greeting pre-warmed")
    except Exception as exc:
        log.warning("greeting pre-warm skipped", extra={"ctx": {"error": str(exc)}})


# Default caller persona (company + inventory) used when the WebSocket
# handler creates a session without an explicit persona. Kept at module
# level so server startup can pre-warm the greeting audio with the exact
# same persona (cache hit => greeting plays instantly on answer).
DEFAULT_PERSONA: dict = {
    "company": "Creatik AI",
    "agent_name": "Neha",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
        "Skyline Residency, Greater Noida": "2/3 BHK, Rs. 35 lakh se shuru",
        "Green Valley, Gurgaon": "3/4 BHK, Rs. 1 crore se shuru",
    },
    "project_location": "Noida, Greater Noida aur Gurgaon",
}


@dataclass
class LiveTurn:
    """One processed user utterance -> the audio to play back."""

    audio: bytes | None = None
    output_content_type: str = "audio/x-mulaw"
    output_sample_rate: int = 8000
    transcript: str | None = None
    reply: str | None = None
    state: str | None = None
    terminal: bool = False
    used_llm: bool = False
    llm_cost: float = 0.0
    cost: dict[str, float] = field(default_factory=dict)
    session_id: uuid.UUID | None = None


def _rms(chunk: bytes) -> float:
    """Mean energy of a byte chunk (integer samples). Cheap VAD proxy.

    Plivo streams G.711 μ-law bytes (8-bit). A raw byte RMS does not reflect
    loudness — μ-law silence is 0xFF, not 0 — so we decode each byte to its
    linear P16 sample before measuring energy.
    """
    if not chunk:
        return 0.0
    total = 0.0
    for b in chunk:
        s = _ulaw_to_sample(b)
        total += s * s
    return (total / len(chunk)) ** 0.5


def _ulaw_to_sample(b: int) -> int:
    """Convert one 8-bit G.711 μ-law byte into a signed 16-bit linear sample."""
    t = b ^ 0xFF
    sign = t & 0x80
    exponent = (t >> 4) & 0x07
    mantissa = t & 0x0F
    sample = ((mantissa << 3) + 0x84) << exponent
    sample -= 0x84
    return -sample if sign else sample


class LiveAgentSession:
    """Runs a single live conversation for one call.

    Not tied to any transport: callers feed audio chunks via ``process_audio``
    and consume the returned ``LiveTurn`` (or call ``flush`` after sending an
    explicit end-of-speech signal).
    """

    def __init__(
        self,
        call_id: uuid.UUID,
        *,
        session_factory: async_sessionmaker,
        engine: ConversationEngine | None = None,
        persona: dict | None = None,
        content_type: str = "audio/x-mulaw",
        sample_rate: int = 8000,
        speech_threshold: float = 200.0,
        silence_frames: int = 20,
        stt: STTProvider | None = None,
        tts: TTSProvider | None = None,
        caller_name: str | None = None,
    ):
        self.call_id = call_id
        self.caller_name = caller_name
        self.session_factory = session_factory
        if engine is not None:
            self.engine = engine
        elif settings.is_mock:
            # Mock/test loop stays fully deterministic and cost-free.
            self.engine = ConversationEngine()
        else:
            # Live loop: deterministic rules first, Gemini intent fallback +
            # grounded answers for anything the rules cannot handle.
            self.engine = ConversationEngine(
                llm=ProviderLLMFallback(), responder=ProviderLLMResponder()
            )
        self.svc = ConversationService(self.engine)
        self.persona = persona or dict(DEFAULT_PERSONA)
        self.content_type = content_type
        self.sample_rate = sample_rate
        self.speech_threshold = speech_threshold
        self.silence_frames = silence_frames

        # Injectable so the Phase 12 orchestrator can drive a call with a
        # specific provider (defaults to the config-selected registry mock).
        self._stt = stt or get_stt_provider()
        self._tts = tts or get_tts_provider()

        # Streaming state.
        self._buffer = bytearray()
        self._had_speech = False
        self._silent_frames = 0
        self._utterance_start: float | None = None
        self._session_id: uuid.UUID | None = None

        # Terminal-turn bookkeeping (set when the engine ends a conversation so
        # the WebSocket handler can hang up + map the final call status).
        self.terminal: bool = False
        self.terminal_state: str | None = None

        # PHASE 1.3 barge-in bookkeeping: how many times the caller spoke
        # over our playback (WS sends Plivo `clearAudio`, then calls
        # interrupt() so the VAD flush timer restarts for the new utterance).
        self.interrupted_count: int = 0
        # PHASE 1.5 no-response nudges sent on this connection (max 2, then
        # the WS hangs up gracefully with status no_response).
        self.nudge_count: int = 0

        # Running ledger of estimated provider costs (currency from providers).
        self.cost: dict[str, float] = {"stt": 0.0, "tts": 0.0}

        # Per-stage latency of the last turn (vad_wait/stt/engine/tts/total
        # milliseconds) - the measurement behind "where exactly is it slow".
        self._last_timings: dict[str, float] = {}

        # Duplicate-turn suppression (exact same transcript twice in a row).
        self._last_cust_text: str | None = None
        self._last_cust_at: float = 0.0

        # Consecutive STT drop streak (empty/unintelligible audio). After a
        # couple of drops in a row the line is likely breaking up, so speak
        # a line-check instead of more dead air (escalates via the shared
        # nudge counter towards a graceful hangup).
        self._drop_streak: int = 0

    # ------------------------------------------------------------- lifecycle

    async def create_session(self) -> None:
        """Create (once) the persisted conversation session for this call.

        Resume, don't restart: if the transport reconnects mid-call (or a
        second stream arrives), adopt the latest live session so the caller
        is not greeted from zero again. Only non-terminal sessions qualify.
        """
        if self._session_id is not None:
            return
        from app.conversation.states import TERMINAL_STATES, ConvState
        from app.models.enums import ConversationSpeaker

        async with self.session_factory() as db:
            adopted = None
            try:
                for session in await conv_crud.list_sessions_for_call(db, self.call_id):
                    try:
                        state = ConvState(session.state) if session.state else None
                    except ValueError:
                        state = None
                    if state is not None and state not in TERMINAL_STATES:
                        adopted = session
                        break
            except Exception:
                adopted = None
            if adopted is not None:
                self._session_id = adopted.id
                log.info(
                    "live session resumed",
                    extra={"ctx": {"call_id": str(self.call_id), "state": adopted.state}},
                )
            else:
                self._session_id = await self.svc.new_session(
                    db,
                    call_id=self.call_id,
                    persona=self.persona,
                    caller_name=self.caller_name,
                )
            # Fetch the greeting so we can speak it.
            msgs = await conv_crud.list_messages(db, self._session_id)
            await db.commit()
        greeting = ""
        if msgs and msgs[-1].speaker == ConversationSpeaker.AGENT.value:
            greeting = msgs[-1].text
        self._greeting = greeting

    async def start(self) -> LiveTurn:
        """Speak the opening greeting (call was answered)."""
        await self.create_session()
        if not getattr(self, "_greeting", ""):
            return LiveTurn(state="GREETING", session_id=self._session_id)
        return await self._speak(self._greeting, state="GREETING")

    async def nudge(self) -> LiveTurn:
        """Speak a no-response nudge (PHASE 1.5: caller silent too long).

        First nudge is a gentle check, second is the goodbye played just
        before the WebSocket hangs up gracefully. The line is persisted to
        the transcript like any other agent turn. Returns audio to play.
        """
        from app.conversation import responses as R
        from app.crud import conversation as conv_crud
        from app.models.enums import ConversationSpeaker

        await self.create_session()
        self.nudge_count += 1
        if self.nudge_count >= 2:
            text = R.NUDGE_SILENCE_2
        else:
            agent_name = (self.persona or {}).get("agent_name", "Neha")
            company = (self.persona or {}).get("company", "our team")
            text = R.render(R.NUDGE_SILENCE_1, agent=agent_name, company=company)
        turn = await self._speak(text, state="NUDGE")
        try:
            async with self.session_factory() as db:
                await conv_crud.add_message(
                    db,
                    call_id=self.call_id,
                    session_id=self._session_id,
                    speaker=ConversationSpeaker.AGENT,
                    text=text,
                    state="NUDGE",
                )
                await db.commit()
        except Exception:
            log.debug("nudge transcript persist skipped")
        return turn

    # ---------------------------------------------------------------- inputs

    def is_speech(self, chunk: bytes) -> bool:
        """Fast VAD check for one audio chunk (PHASE 1.3 barge-in).

        Same RMS threshold as process_audio, without touching the buffer,
        so the WebSocket layer can detect "caller spoke over our playback"
        before the utterance is complete (no STT cost).
        """
        return _rms(chunk) >= self.speech_threshold

    def interrupt(self) -> None:
        """Mark a barge-in: restart the VAD flush timer for the new utterance.

        The buffer is KEPT (it already holds the caller's new speech that
        triggered the interruption); only the silence counter resets so the
        in-progress utterance is not flushed prematurely. The already-sent
        TTS playback itself is cancelled transport-side via Plivo
        `clearAudio` by the WebSocket handler.
        """
        self.interrupted_count += 1
        self._had_speech = True
        self._silent_frames = 0

    async def process_audio(self, chunk: bytes) -> LiveTurn | None:
        """Feed one audio chunk; returns a reply once speech ends, else None.

        Leading silence is never buffered (it would pile up unheard minutes
        that STT then bills in full and answers late), and the buffer is
        capped so one long monologue cannot become an unbounded request.
        """
        energy = _rms(chunk)

        if energy >= self.speech_threshold:
            if not self._had_speech:
                self._utterance_start = _monotonic()
            self._had_speech = True
            self._silent_frames = 0
            self._buffer.extend(chunk)
            overflow = len(self._buffer) - MAX_BUFFER_BYTES
            if overflow > 0:
                del self._buffer[:overflow]
            return None

        # Silence: only care once we've heard speech.
        if not self._had_speech:
            return None

        self._buffer.extend(chunk)
        overflow = len(self._buffer) - MAX_BUFFER_BYTES
        if overflow > 0:
            del self._buffer[:overflow]
        self._silent_frames += 1
        if self._silent_frames >= self.silence_frames:
            return await self.flush()
        return None

    async def flush(self, *, speak: bool = True) -> LiveTurn:
        """Force a turn from the current buffered audio (ends the utterance).

        ``speak=False`` persists the turn but skips TTS synthesis - used on
        the `stop` event where the call is already over and playback would
        go nowhere (saves a wasted paid synthesis per call).
        """
        audio = bytes(self._buffer)
        self._buffer.clear()
        self._had_speech = False
        self._silent_frames = 0
        flush_at = _monotonic()
        utterance_start, self._utterance_start = self._utterance_start, None

        if not audio:
            return LiveTurn(session_id=self._session_id)

        stt_at = _monotonic()
        transcript = await self._transcribe(audio)
        stt_ms = (_monotonic() - stt_at) * 1000.0
        self._last_timings = {
            "vad_wait_ms": round(max(0.0, (flush_at - utterance_start) * 1000.0), 1)
            if utterance_start is not None
            else 0.0,
            "stt_ms": round(stt_ms, 1),
        }
        if not transcript or _is_unintelligible(transcript):
            # Nothing usable arrived (silence/noise/STT miss): stay silent,
            # but say so LOUDLY in the logs (INFO, not debug) so dead-air
            # turns are diagnosable instead of "random". After consecutive
            # drops the line is likely breaking - speak the standard
            # line-check nudge rather than more silence.
            self._drop_streak += 1
            log.info(
                "live stt drop",
                extra={"ctx": {
                    "empty": not transcript,
                    "streak": self._drop_streak,
                    "text": (transcript or "")[:60],
                }},
            )
            if self._drop_streak >= 2:
                self._drop_streak = 0
                return await self.nudge()
            return LiveTurn(transcript=transcript, session_id=self._session_id)
        if is_system_announcement(transcript):
            # Plivo recording notices etc. are not the caller: drop silently
            # (INFO so dropped turns stay visible in live-call diagnostics).
            log.info(
                "live stt system announcement ignored",
                extra={"ctx": {"text": transcript[:80]}},
            )
            return LiveTurn(transcript=transcript, session_id=self._session_id)

        turn = await self.handle_user_text(transcript, speak=speak)
        await self._log_turn_timings(flush_at)
        return turn

    async def _log_turn_timings(self, started_at: float) -> None:
        """Record + log the full per-stage latency of a finished turn."""
        self._last_timings["total_ms"] = round(
            max(0.0, (_monotonic() - started_at) * 1000.0), 1
        )
        log.info(
            "live turn timings",
            extra={"ctx": {"timings_ms": dict(self._last_timings)}},
        )

    # ---------------------------------------------------------------- helpers

    async def _transcribe(self, audio: bytes) -> str | None:
        from app.services import audio_codec

        # Plivo streams mu-law 8kHz; Sarvam STT expects a WAV upload.
        wav = audio_codec.mulaw_to_wav16k(audio)
        result = await self._stt.transcribe(
            wav,
            language="hi-Latn",
            content_type="audio/wav",
        )
        if result.cost is not None:
            self.cost["stt"] += result.cost.amount
        text = (result.text or "").strip()
        log.debug("live stt", extra={"ctx": {"text": text[:80]}})
        return text or None

    async def handle_user_text(
        self, transcript: str, *, dedupe: bool = True, speak: bool = True
    ) -> LiveTurn:
        """Process one recognised user utterance (produces the agent reply).

        Public entry used by the Phase 12 call orchestrator to replay a
        scripted conversation deterministically (in production the transcript
        comes from STT on real media, then this same path runs the engine and
        synthesises the reply).

        ``dedupe`` drops an exact-duplicate transcript arriving within
        DEDUP_WINDOW_SEC of the identical previous one (jitter / tail
        re-transcription). The orchestrator passes ``dedupe=False`` because
        scripted repeats are intentional.

        ``speak=False`` persists the turn but skips TTS synthesis (used on
        the `stop` event: the caller is gone, playback would go nowhere).
        """
        await self.create_session()
        assert self._session_id is not None
        norm = (transcript or "").strip().lower()
        if dedupe and norm:
            now = _monotonic()
            if (
                self._last_cust_text is not None
                and norm == self._last_cust_text
                and now - self._last_cust_at < DEDUP_WINDOW_SEC
            ):
                log.info(
                    "live duplicate turn dropped",
                    extra={"ctx": {"text": transcript[:60]}},
                )
                return LiveTurn(transcript=transcript, session_id=self._session_id)
            self._last_cust_text = norm
            self._last_cust_at = now
        self._drop_streak = 0  # a real turn arrived - line is alive
        log.info(
            "live turn start",
            extra={"ctx": {"heard": _log_snip(transcript)}},
        )
        eng_at = _monotonic()
        async with self.session_factory() as db:
            turn = await self.svc.process_turn(
                db,
                session_id=self._session_id,
                call_id=self.call_id,
                user_text=transcript,
                persona=self.persona,
                caller_name=self.caller_name,
            )
            await db.commit()
        self._last_timings["engine_ms"] = round((_monotonic() - eng_at) * 1000.0, 1)

        if not turn.reply:
            self.terminal = bool(turn.termination)
            if self.terminal:
                self.terminal_state = turn.state.value if turn.state else None
            self._last_timings["tts_ms"] = 0.0
            log.info(
                "live turn done",
                extra={"ctx": {
                    "state": turn.state.value if turn.state else None,
                    "reply": "",
                    "used_llm": turn.used_llm,
                    "terminal": turn.termination,
                    "timings_ms": dict(self._last_timings),
                }},
            )
            return LiveTurn(
                transcript=transcript,
                session_id=self._session_id,
                state=turn.state.value if turn.state else None,
                terminal=turn.termination,
                used_llm=turn.used_llm,
                llm_cost=turn.llm_cost,
            )
        self.terminal = bool(turn.termination)
        if self.terminal:
            self.terminal_state = turn.state.value if turn.state else getattr(
                turn.state, "value", None
            )
        if not speak:
            # Transcript persisted above; report the turn outcome (with the
            # reply TEXT for logs/tests) without burning a TTS synthesis.
            return LiveTurn(
                transcript=transcript,
                session_id=self._session_id,
                state=turn.state.value if turn.state else None,
                reply=turn.reply,
                terminal=turn.termination,
                used_llm=turn.used_llm,
                llm_cost=turn.llm_cost,
            )
        log.info(
            "live turn done",
            extra={"ctx": {
                "state": turn.state.value if turn.state else None,
                "reply": _log_snip(turn.reply),
                "used_llm": turn.used_llm,
                "terminal": turn.termination,
                "timings_ms": dict(self._last_timings),
            }},
        )
        tts_at = _monotonic()
        spoken = await self._speak(
            turn.reply,
            transcript=transcript,
            state=turn.state.value if turn.state else None,
            terminal=turn.termination,
            used_llm=turn.used_llm,
            llm_cost=turn.llm_cost,
        )
        self._last_timings["tts_ms"] = round((_monotonic() - tts_at) * 1000.0, 1)
        return spoken

    async def _speak(
        self,
        text: str,
        *,
        transcript: str | None = None,
        state: str | None = None,
        terminal: bool = False,
        used_llm: bool = False,
        llm_cost: float = 0.0,
    ) -> LiveTurn:
        from app.services import audio_codec

        synth = await self._tts.synthesize(text)
        # Sarvam TTS returns WAV; Plivo's <Stream> needs 8kHz mu-law. Without
        # this conversion the caller would hear malformed/silent audio.
        payload = audio_codec.wav_to_mulaw(synth.audio)
        cost = dict(self.cost)
        if synth.cost is not None:
            self.cost["tts"] += synth.cost.amount
            cost["tts"] = self.cost["tts"]
        return LiveTurn(
            audio=payload,
            output_content_type=self.content_type.split(";")[0].strip(),
            output_sample_rate=self.sample_rate,
            transcript=transcript,
            reply=text,
            state=state,
            terminal=terminal,
            used_llm=used_llm,
            llm_cost=llm_cost,
            cost=cost,
            session_id=self._session_id,
        )

    @property
    def session_id(self) -> uuid.UUID | None:
        return self._session_id
