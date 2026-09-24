"""Audio transcoding between Plivo's 8kHz mu-law stream and Sarvam WAV.

Plivo's ``<Stream>`` element carries G.711 mu-law audio at 8kHz
(``audio/x-mulaw;rate=8000``). Sarvam STT expects a WAV upload and Sarvam TTS
returns WAV audio. This module bridges the two so a real two-way call actually
produces audible, transcribable audio.

Everything here is pure stdlib (``wave`` + ``audioop``) so no third-party audio
dependency is required. ``audioop`` is deprecated in 3.11+ and slated for
removal in 3.13; if it is missing we degrade the transcoding calls to a clear
error rather than silently sending the wrong format.
"""

from __future__ import annotations

import io
import struct
import wave

from app.logging_config import get_logger

log = get_logger("app.services.audio_codec")

# Plivo <Stream> media format (fixed by the Plivo answer XML).
PLIVO_SAMPLE_RATE = 8000
# Sarvam STT upload format.
STT_SAMPLE_RATE = 16000


class AudioCodecError(Exception):
    """Raised when audio cannot be transcoded to the expected format."""


_ULAW2LIN = None
_LIN2ULAW = None


def _audioop():
    global _ULAW2LIN, _LIN2ULAW
    if _ULAW2LIN is None:
        try:
            import audioop

            _ULAW2LIN = audioop.ulaw2lin
            _LIN2ULAW = audioop.lin2ulaw
            _TOMONO = audioop.tomono
            _LIN2LIN = audioop.lin2lin
            _BIAS = audioop.bias
        except ImportError as exc:  # pragma: no cover - audioop removed in 3.13
            raise AudioCodecError(
                "audio transcoding unavailable: Python 'audioop' module is missing "
                "(removed in Python 3.13). Pin Python <3.13 or add an audio dep."
            ) from exc
        globals()["_TOMONO"] = _TOMONO
        globals()["_LIN2LIN"] = _LIN2LIN
        globals()["_BIAS"] = _BIAS
    return globals()


def _read_wav_pcm(wav_bytes: bytes) -> tuple[bytes, int, int]:
    """Parse a WAV and return (16-bit signed mono PCM, sample_rate, channels)."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate()
            nch = w.getnchannels()
            sw = w.getsampwidth()
            pcm = w.readframes(w.getnframes())
    except (wave.Error, EOFError, struct.error) as exc:
        raise AudioCodecError(f"input is not a valid WAV: {exc}") from exc

    a = _audioop()
    # Normalise every WAV variant to 16-bit signed.
    if sw == 1:
        pcm = a["_LIN2LIN"](a["_BIAS"](pcm, 1, -128), 1, 2)
    elif sw == 2:
        pass
    elif sw in (3, 4):
        pcm = a["_LIN2LIN"](pcm, sw, 2)
    else:
        raise AudioCodecError(f"unsupported sample width {sw}")

    if nch > 1:
        pcm = a["_TOMONO"](pcm, 2, 0.5, 0.5)
    return pcm, rate, nch


def _resample(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linearly resample 16-bit signed PCM between rates."""
    if src_rate <= 0 or dst_rate <= 0 or src_rate == dst_rate:
        return pcm
    width = 2
    n = len(pcm) // width
    if n <= 1:
        return pcm
    src = struct.unpack(f"<{n}h", pcm)
    ratio = src_rate / dst_rate
    if dst_rate < src_rate:
        count = int(n / ratio)
        out = []
        for i in range(count):
            start = int(i * ratio)
            end = min(int((i + 1) * ratio), n)
            window = src[start:end]
            out.append(int(sum(window) / len(window)) if window else src[start])
    else:
        count = int(n * dst_rate / src_rate)
        out = []
        for i in range(count):
            pos = i * ratio
            i0 = int(pos)
            i1 = min(i0 + 1, n - 1)
            frac = pos - i0
            out.append(int(src[i0] * (1.0 - frac) + src[i1] * frac))
    return struct.pack(f"<{len(out)}h", *out)


def _build_wav(pcm: bytes, rate: int, channels: int, width: int) -> bytes:
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE", b"fmt ",
        16, 1, channels, rate, rate * channels * width,
        channels * width, width * 8, b"data", data_size,
    )
    return header + pcm


def mulaw_to_wav16k(mulaw: bytes) -> bytes:
    """Convert Plivo 8kHz mu-law audio to a 16kHz mono 16-bit WAV (STT)."""
    if not mulaw:
        return _build_wav(b"", STT_SAMPLE_RATE, 1, 2)
    a = _audioop()
    pcm = a["_ULAW2LIN"](mulaw, 2)          # 8kHz 16-bit
    pcm = _resample(pcm, PLIVO_SAMPLE_RATE, STT_SAMPLE_RATE)
    return _build_wav(pcm, STT_SAMPLE_RATE, 1, 2)


def wav_to_mulaw(wav: bytes) -> bytes:
    """Convert WAV audio (from TTS) to 8kHz mu-law (for Plivo playAudio)."""
    if not wav:
        return b""
    if wav[:4] != b"RIFF":
        # Already raw mu-law (e.g. a provider returning mu-law directly).
        return wav
    pcm, rate, _ = _read_wav_pcm(wav)
    pcm = _resample(pcm, rate, PLIVO_SAMPLE_RATE)
    a = _audioop()
    return a["_LIN2ULAW"](pcm, 2)
