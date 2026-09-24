"""Unit tests for the audio transcoding utility (audio_codec)."""

import io
import struct
import wave

import pytest

from app.services import audio_codec


def _wav(rate: int, channels: int, width: int, samples: bytes) -> bytes:
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(samples), b"WAVE", b"fmt ",
        16, 1, channels, rate, rate * channels * width,
        channels * width, width * 8, b"data", len(samples),
    )
    return header + samples


def test_wav_to_mulaw_basic_roundtrip():
    # 24kHz mono 16-bit PCM with a non-trivial signal.
    n = 2400  # 0.1s @24k
    pcm = struct.pack(f"<{n}h", *[int(1000 * ((i % 50) / 50.0 - 0.5)) for i in range(n)])
    wav = _wav(24000, 1, 2, pcm)

    mulaw = audio_codec.wav_to_mulaw(wav)
    assert mulaw
    assert mulaw[:4] != b"RIFF"  # it is raw mu-law, not WAV

    # Decode back to WAV via the STT path.
    wav16 = audio_codec.mulaw_to_wav16k(mulaw)
    assert wav16[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav16), "rb") as w:
        assert w.getframerate() == audio_codec.STT_SAMPLE_RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2


def test_wav_to_mulaw_stereo_and_wider_width():
    # 8kHz stereo 16-bit -> mu-law mono.
    n = 1600
    pcm = struct.pack(f"<{2 * n}h", *([1000, -1000] * n))
    wav = _wav(8000, 2, 2, pcm)
    mulaw = audio_codec.wav_to_mulaw(wav)
    assert mulaw
    # Downsampled to 8k and downmixed to mono; decode must still be valid.
    assert audio_codec.mulaw_to_wav16k(mulaw)[:4] == b"RIFF"


def test_mulaw_to_wav16k_upsamples_to_16k():
    mulaw = bytes([128]) * 800  # 0.1s of silence @8k
    wav16 = audio_codec.mulaw_to_wav16k(mulaw)
    with wave.open(io.BytesIO(wav16), "rb") as w:
        assert w.getframerate() == 16000
        assert w.getnframes() >= 1600  # ~x2 samples


def test_wav_to_mulaw_passthrough_for_raw_mulaw():
    raw = bytes([200, 120, 30])
    assert audio_codec.wav_to_mulaw(raw) == raw


def test_wav_to_mulaw_invalid_input_raises():
    with pytest.raises(audio_codec.AudioCodecError):
        audio_codec.wav_to_mulaw(b"RIFF" + b"garbage NOT a wav")
