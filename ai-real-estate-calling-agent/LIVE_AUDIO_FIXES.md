# Live Voice Reliability Fix

The project keeps the original architecture: **Plivo media -> VAD -> STT -> conversation engine -> TTS -> Plivo playback**.

## What was fixed

- Added `app/api/v1/ws_stable.py` as the production WebSocket implementation.
- The WebSocket reader now continuously drains incoming Plivo media into a bounded FIFO queue while STT/LLM/TTS work runs in a separate worker path. This prevents the application from temporarily stopping its audio intake during slow provider calls.
- Preserved media arrival timestamps so queued audio captured before a reply cannot be incorrectly treated as a new barge-in after the reply is sent.
- Barge-in grace is evaluated using the media frame's arrival time, reducing false `clearAudio` events caused by worker backlog.
- Voice timestamps never move backwards when delayed queued frames are processed.
- Malformed JSON/base64 media is ignored safely instead of terminating the live loop.
- Existing `app/api/v1/ws.py` remains as a compatibility entrypoint, so existing imports continue to work.
- `app/api/v1/router.py` points directly at the stable implementation.
- Added a Python 3.13-compatible `audioop-lts` dependency because the stdlib `audioop` module was removed in Python 3.13.

## Concept intentionally unchanged

No changes were made to the conversation state machine, qualification flow, persona, STT language, TTS provider selection, database model, or telephony provider abstraction.

## Run

Use the same startup command/environment as the original project. The WebSocket endpoint remains:

`/api/v1/ws/calls/{call_id}`
