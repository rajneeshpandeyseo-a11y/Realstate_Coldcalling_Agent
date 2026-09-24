"""Plivo webhooks router - /api/v1/webhooks/plivo.

Two callbacks Plivo invokes on our host:

* ``answer``  - Plivo requests this URL when the outbound call is answered.
  We reply with Plivo XML containing a bidirectional ``<Stream>`` element that
  points at our real-time audio WebSocket, so Plivo streams the caller's audio
  to us and plays our synthesised audio back (Phase 11).

* ``status``  - Plivo posts call-status callbacks (ringing, in-progress,
  completed, ...) which we map onto the deterministic call state machine
  (Phase 4 service) and persist.

These endpoints are intentionally thin: business logic (state machine,
conversation) lives in the service layer, and both are fully testable with
mocked providers so no paid telephony is required in development.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limit_request_body, require_webhook_token
from app.config import settings
from app.crud import call as call_crud
from app.logging_config import get_logger
from app.db.session import get_db
from app.models.enums import CallStatus
from app.services import call as call_service

log = get_logger("app.api.v1.webhooks")

router = APIRouter(prefix="/webhooks/plivo", tags=["webhooks"])

# Plivo CallStatus/Event -> our canonical CallStatus.
_PLIVO_STATUS_MAP = {
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    "answered": CallStatus.ANSWERED,
    "completed": CallStatus.COMPLETED,
    "missed": CallStatus.NO_ANSWER,
    "no-answer": CallStatus.NO_ANSWER,
    "no_answer": CallStatus.NO_ANSWER,
    "busy": CallStatus.BUSY,
    "canceled": CallStatus.CANCELLED,
    "cancelled": CallStatus.CANCELLED,
    "transport-error": CallStatus.FAILED,
    "transport_error": CallStatus.FAILED,
    "failed": CallStatus.FAILED,
}

# Content type we negotiate on the Plivo stream (native telephony codec).
_DEFAULT_STREAM_MEDIA = "audio/x-mulaw;rate=8000"


# Lifecycle rank for stale-webhook suppression. Plivo callbacks can arrive
# out of order (observed live: `ringing` 13ms AFTER `in-progress`) and are
# retried on slow responses. Without a guard a late `ringing` REGRESSES an
# answered call, and a retried `completed` re-runs CRM finalisation.
_STATUS_RANK = {
    CallStatus.QUEUED: 0,
    CallStatus.INITIATED: 1,
    CallStatus.RINGING: 2,
    CallStatus.ANSWERED: 3,
    CallStatus.IN_PROGRESS: 4,
    CallStatus.COMPLETED: 5,
    CallStatus.NO_ANSWER: 5,
    CallStatus.NO_RESPONSE: 5,
    CallStatus.BUSY: 5,
    CallStatus.FAILED: 5,
    CallStatus.CANCELLED: 5,
    CallStatus.CALLBACK_REQUESTED: 5,
    CallStatus.DO_NOT_CALL: 5,
}


def _webhook_outcome(current: str, incoming: CallStatus) -> str:
    """Classify an incoming webhook status: 'apply' | 'stale' | 'duplicate'.

    - 'duplicate': same status we already hold (Plivo retry) - skip so CRM
      finalisation never runs twice for one call.
    - 'stale': an older lifecycle step arriving late - skip so the call can
      never move backwards (e.g. ringing after in-progress).
    - 'apply': anything else; the state machine still validates the edge.
    """
    if current == incoming.value:
        return "duplicate"
    try:
        cur_rank = _STATUS_RANK.get(CallStatus(current), -1)
    except ValueError:
        cur_rank = -1
    if _STATUS_RANK.get(incoming, 5) < cur_rank:
        return "stale"
    return "apply"


def _ws_url(call_id: uuid.UUID | None) -> str:
    """Derive a WebSocket URL from PUBLIC_BASE_URL for the live audio stream."""
    base = (settings.PUBLIC_BASE_URL or "http://localhost:8000").rstrip("/")
    if base.startswith("https://"):
        ws = "wss://" + base[len("https://") :]
    else:
        ws = "ws://" + base[len("http://") :]
    return f"{ws}/api/v1/ws/calls/{call_id or 'unknown'}"


def _answer_xml(call_id: uuid.UUID | None) -> str:
    """Build Plivo XML: record the whole call (if recording enabled) and open a
    bidirectional audio stream to our WebSocket.

    Recording of a complete two-way call uses Plivo's session recording
    (``<Record recordSession="true">``), which runs in the background for the
    whole call and notifies ``callbackUrl`` with the recording file once done.
    """
    stream_media = getattr(settings, "PLIVO_STREAM_CONTENT_TYPE", None) or _DEFAULT_STREAM_MEDIA
    url = _ws_url(call_id)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        + _record_element(call_id)
        + f'    <Stream bidirectional="true" keepCallAlive="true" '
        f'contentType="{stream_media}">\n'
        f"        {url}\n"
        "    </Stream>\n"
        "</Response>"
    )


def _record_element(call_id: uuid.UUID | None) -> str:
    """Return the ``<Record>`` element when recording is enabled (else empty).

    ``callbackUrl`` is our Plivo recording webhook; the call id rides along as a
    query param so the webhook can attach the recording URL to the right call.
    """
    if not settings.RECORDING_ENABLED:
        return ""
    callback = (
        f"{settings.PUBLIC_BASE_URL}/api/v1/webhooks/plivo/recording"
        f"?call_id={call_id}" if call_id else f"{settings.PUBLIC_BASE_URL}/api/v1/webhooks/plivo/recording"
    )
    return (
        f'    <Record recordSession="true" callbackUrl="{callback}" '
        f'callbackMethod="POST" maxLength="600" />\n'
    )


@router.api_route(
    "/answer",
    methods=["GET", "POST"],
    response_class=Response,
    include_in_schema=False,
)
async def plivo_answer(
    request: Request,
    call_id: uuid.UUID | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return Plivo XML that opens a bidirectional audio stream to our WS."""
    log.info(
        "plivo answer webhook",
        extra={"ctx": {"call_id": str(call_id) if call_id else None}},
    )
    # Plivo fires answer_url when the call is answered; the status rides along
    # (CallStatus=in-progress). Map it onto our state machine so the call is
    # not stuck as RINGING while the audio stream runs. Stale hints (older
    # than what we hold, e.g. a delayed answer fetch) are ignored.
    if call_id is not None:
        status_hint = (request.query_params.get("CallStatus") or "").lower()
        if status_hint and status_hint != "ringing":
            to_status = _PLIVO_STATUS_MAP.get(status_hint)
            if to_status is not None:
                call = await call_crud.get_call(db, call_id)
                if call is not None:
                    if _webhook_outcome(call.status, to_status) != "apply":
                        log.info(
                            "plivo answer hint skipped",
                            extra={"ctx": {
                                "call_id": str(call_id),
                                "current": call.status,
                                "hint": status_hint,
                            }},
                        )
                    else:
                        try:
                            await call_service.transition_call(
                                db, call, to_status,
                                event_data={"source": "plivo", "call_status": status_hint},
                            )
                            await db.commit()
                        except call_service.IllegalTransitionError as exc:
                            log.warning(
                                "plivo answer hint illegal transition",
                                extra={"ctx": {
                                    "call_id": str(call_id),
                                    "error": str(exc),
                                }},
                            )
    return Response(
        content=_answer_xml(call_id),
        media_type="application/xml",
        headers={"Content-Type": "application/xml; charset=utf-8"},
    )


@router.api_route("/status", methods=["GET", "POST"], include_in_schema=False, dependencies=[Depends(require_webhook_token), Depends(limit_request_body)])
async def plivo_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Map a Plivo call-status callback onto the call state machine."""
    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    query = dict(request.query_params)
    call_status = (form.get("CallStatus") or form.get("Status") or query.get("CallStatus") or query.get("Status") or "").lower()
    call_uuid = form.get("CallUUID") or form.get("call_uuid") or query.get("CallUUID") or query.get("call_uuid") or ""

    if not call_uuid:
        log.warning("plivo status without CallUUID", extra={"ctx": {"form": {k: v for k, v in form.items() if k.lower() != 'accountsid'}}})
        return {"ok": True, "skipped": "missing_call_uuid"}

    to_status = _PLIVO_STATUS_MAP.get(call_status)
    if to_status is None:
        log.warning("unmapped plivo status", extra={"ctx": {"call_status": call_status}})
        return {"ok": True, "skipped": "unmapped"}

    call = await call_crud.get_call_by_provider_id(db, call_uuid)
    if call is None:
        log.warning("plivo status for unknown call", extra={"ctx": {"call_uuid": call_uuid}})
        return {"ok": True, "skipped": "unknown_call"}

    outcome = _webhook_outcome(call.status, to_status)
    if outcome != "apply":
        # Stale (out-of-order/late) or duplicate (Plivo retry): never move
        # the call backwards and never run CRM finalisation twice.
        log.info(
            f"plivo status {outcome} ignored",
            extra={"ctx": {
                "call_uuid": call_uuid,
                "current": call.status,
                "incoming": to_status.value,
            }},
        )
        return {"ok": True, "skipped": outcome}

    try:
        await call_service.transition_call(
            db, call, to_status, event_data={"source": "plivo", "call_status": call_status}
        )
        # Persist the live-call CRM outcome (requirements + site visit) when
        # Plivo reports the call completed - covers the caller-hangs-up path.
        if to_status == CallStatus.COMPLETED:
            from app.services.live_outcome import finalize_live_call

            await finalize_live_call(db, call)
        await db.commit()
    except call_service.IllegalTransitionError as exc:
        log.warning("plivo status illegal transition", extra={"ctx": {"error": str(exc)}})
        return {"ok": False, "error": str(exc)}

    return {"ok": True, "call_status": call_status}


@router.post("/recording", include_in_schema=False, dependencies=[Depends(require_webhook_token), Depends(limit_request_body)])
async def plivo_recording(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Attach the Plivo recording URL (session recording) to the right call.

    Plivo posts the recording file details to the ``callbackUrl`` we placed on
    the ``<Record recordSession="true">`` element in the answer XML. The call id
    rides along as a query param so we don't have to guess which call it is.
    """
    try:
        form = dict(await request.form())
    except Exception:
        form = {}
    call_id = request.query_params.get("call_id")
    recording_url = (
        form.get("RecordUrl")
        or form.get("RecordFile")
        or form.get("RecordingUrl")
        or form.get("record_url")
        or form.get("recording_url")
    ) or ""
    if not call_id or not recording_url:
        log.warning("plivo recording callback missing call_id/url",
                    extra={"ctx": {k: v for k, v in form.items() if k.lower() != "accountsid"}})
        return {"ok": True, "skipped": "missing_call_id_or_url"}

    try:
        call_uuid = uuid.UUID(call_id)
    except ValueError:
        return {"ok": True, "skipped": "invalid_call_id"}

    call = await call_crud.get_call(db, call_uuid)
    if call is None:
        log.warning("plivo recording for unknown call", extra={"ctx": {"call_id": call_id}})
        return {"ok": True, "skipped": "unknown_call"}

    call.recording_url = recording_url
    call.recording_enabled = True
    await db.commit()
    log.info("plivo recording url stored", extra={"ctx": {"call_id": call_id}})
    return {"ok": True, "recording_url": recording_url}
