"""Compatibility entrypoint for the production live-call WebSocket.

The implementation now lives in :mod:`app.api.v1.ws_stable`. Keeping this
module preserves existing imports used by tests and other application code
without changing the public endpoint or the call-agent architecture.
"""

from app.api.v1 import ws_stable as _stable
from app.api.v1.ws_stable import (
    BARGE_IN_POST_REPLY_GRACE_SEC,
    BARGE_IN_SPEECH_FRAMES,
    GOODBYE_SEC,
    MAX_NUDGES,
    NO_RESPONSE_TIMEOUT_SEC,
    PLAYBACK_SLACK_SEC,
    _checkpoint,
    _clear_audio,
    _extract_stream_id,
    _finalize_no_response,
    _finalize_terminal_call,
    _monotonic,
    _no_response_action,
    _play_audio,
    _playback_until,
    _recent_greeting,
    router,
)


async def call_media_stream(websocket, call_id):
    """Preserve the legacy module's monkeypatchable clock for tests."""
    original_clock = _stable._monotonic
    _stable._monotonic = _monotonic
    try:
        await _stable.call_media_stream(websocket, call_id)
    finally:
        _stable._monotonic = original_clock

__all__ = [
    "router",
    "call_media_stream",
    "BARGE_IN_SPEECH_FRAMES",
    "BARGE_IN_POST_REPLY_GRACE_SEC",
    "NO_RESPONSE_TIMEOUT_SEC",
    "MAX_NUDGES",
    "GOODBYE_SEC",
    "PLAYBACK_SLACK_SEC",
    "_extract_stream_id",
    "_clear_audio",
    "_checkpoint",
    "_playback_until",
    "_recent_greeting",
    "_no_response_action",
    "_finalize_terminal_call",
    "_finalize_no_response",
    "_play_audio",
    "_monotonic",
]
