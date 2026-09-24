"""PHASE 1.4: reprompt alternates - repeats never echo the exact same line."""

import pytest

from app.conversation import responses as R
from app.conversation.engine import ConversationEngine
from app.conversation.states import ConvState

ENGINE = ConversationEngine()

PERSONA = {
    "company": "Creatik AI",
    "agent_name": "Neha",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
    },
}


def test_variant_primary_is_default_and_backward_compatible():
    assert R.variant("qualification_type", 0) == R.QUALIFICATION_TYPE
    assert R.variant("qualification_location", 0) == R.QUALIFICATION_LOCATION
    assert R.variant("qualification_budget", 0) == R.QUALIFICATION_BUDGET
    assert R.variant("qualification_purpose", 0) == R.QUALIFICATION_PURPOSE
    assert R.variant("qualification_timeline", 0) == R.QUALIFICATION_TIMELINE
    assert R.variant("visit_date_ask", 0) == R.VISIT_DATE_ASK
    assert R.variant("yes_nudge", 0) == R.YES_NUDGE
    assert R.variant("fallback_out_of_scope", 0) == R.FALLBACK_OUT_OF_SCOPE
    assert R.variant("objection_generic", 0) == R.OBJECTION_GENERIC


def test_variant_rotates_and_wraps():
    a0 = R.variant("qualification_location", 0)
    a1 = R.variant("qualification_location", 1)
    a2 = R.variant("qualification_location", 2)
    assert len({a0, a1, a2}) == 3
    assert R.variant("qualification_location", 3) == a0
    assert R.variant("qualification_location", -1) == a0


@pytest.mark.parametrize(
    "key,needle",
    [
        ("qualification_type", "kis type ki property"),
        ("qualification_location", "location"),
        ("qualification_budget", "budget"),
        ("qualification_purpose", "self-use"),
        ("qualification_timeline", "finalize"),
        ("visit_date_ask", "Saturday"),
        ("need_summary", "Correct?"),
    ],
)
def test_every_variant_keeps_key_substring(key, needle):
    pool = R._VARIANTS[key]
    assert len(pool) >= 3
    kwargs = {"need": "2 BHK flat, Noida, around 40 lakh"} if key == "need_summary" else {}
    for i in range(len(pool)):
        assert needle in R.variant(key, i, **kwargs)


def test_visit_time_variants_keep_choices():
    for i in range(3):
        text = R.variant("visit_time_ask", i, date="Sunday")
        assert "11 AM" in text and "4 PM" in text and "Sunday" in text


def test_permission_variants_keep_substrings():
    for i in range(3):
        named = R.variant(
            "permission_ask_named", i,
            first_name="Rahul", agent_name="Neha", company="Creatik AI",
        )
        assert "2 minute" in named and "good time" in named and "Rahul" in named
        plain = R.variant(
            "permission_ask", i, agent_name="Neha", company="Creatik AI"
        )
        assert "2 minute" in plain and "good time" in plain


@pytest.mark.asyncio
async def test_qualification_other_reprompt_varies():
    r0 = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={}, reprompt=0,
        user_input="xyz jabberwocky nonsensical text here", persona=PERSONA,
    )
    r1 = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={}, reprompt=1,
        user_input="xyz jabberwocky nonsensical text here", persona=PERSONA,
    )
    assert r0.state == r1.state == ConvState.QUALIFICATION
    assert r0.reply != r1.reply
    assert "kis type ki property" in r0.reply
    assert "kis type ki property" in r1.reply


@pytest.mark.asyncio
async def test_bare_confirmation_nudge_varies():
    r0 = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk"}, reprompt=0,
        user_input="haan", persona=PERSONA,
    )
    r1 = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={"bhk": "2bhk"}, reprompt=2,
        user_input="haan", persona=PERSONA,
    )
    assert r0.reply != r1.reply
    assert "location" in r0.reply.lower() or "area" in r0.reply.lower()
    assert "location" in r1.reply.lower() or "area" in r1.reply.lower()


@pytest.mark.asyncio
async def test_summary_recap_varies():
    slots = {"bhk": "2bhk", "location": "Noida", "budget": "40 lakh"}
    r0 = await ENGINE.step(
        state=ConvState.SUMMARY_CONFIRM, slots=dict(slots), reprompt=0,
        user_input="xyz jabberwocky nonsense", persona=PERSONA,
    )
    r1 = await ENGINE.step(
        state=ConvState.SUMMARY_CONFIRM, slots=dict(slots), reprompt=1,
        user_input="xyz jabberwocky nonsense", persona=PERSONA,
    )
    assert r0.state == r1.state == ConvState.SUMMARY_CONFIRM
    assert r0.reply != r1.reply
    assert "Correct?" in r0.reply and "Correct?" in r1.reply


@pytest.mark.asyncio
async def test_visit_date_reask_varies():
    r0 = await ENGINE.step(
        state=ConvState.VISIT_BOOKING, slots={}, reprompt=0,
        user_input="xyz jabberwocky nonsense", persona=PERSONA,
    )
    r1 = await ENGINE.step(
        state=ConvState.VISIT_BOOKING, slots={}, reprompt=1,
        user_input="xyz jabberwocky nonsense", persona=PERSONA,
    )
    assert r0.state == r1.state == ConvState.VISIT_BOOKING
    assert r0.reply != r1.reply
    assert "Saturday" in r0.reply and "Saturday" in r1.reply


@pytest.mark.asyncio
async def test_forward_progress_stays_primary():
    """New questions (forward moves) always use primary phrasing."""
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots={}, reprompt=5,
        user_input="2 BHK flat chahiye", persona=PERSONA,
    )
    assert r.reply == R.QUALIFICATION_LOCATION


@pytest.mark.asyncio
async def test_service_reprompt_rotates_repeats(client, db_sessionmaker):
    """Two identical OTHER turns through the service give different replies."""
    import uuid as _uuid

    from app.services.conversation import ConversationService

    # Unique phone per run: never collides with stale rows from other runs.
    _suffix = f"{_uuid.uuid4().int % 100000:05d}"
    lead_resp = await client.post(
        "/api/v1/leads",
        json={"name": "Reprompt", "phone": f"+91960{_suffix}", "city": "Jaipur"},
    )
    assert lead_resp.status_code == 201, lead_resp.text
    lead = lead_resp.json()
    call_resp = await client.post("/api/v1/calls", json={"lead_id": lead["id"]})
    assert call_resp.status_code == 201, call_resp.text
    call = call_resp.json()
    call_id = _uuid.UUID(call["id"])
    svc = ConversationService(ConversationEngine())
    async with db_sessionmaker() as db:
        sid = await svc.new_session(db, call_id=call_id, persona=PERSONA)
        await db.commit()
    async with db_sessionmaker() as db:
        await svc.process_turn(
            db, session_id=sid, call_id=call_id,
            user_text="haan ji", persona=PERSONA,
        )
        await db.commit()
    async with db_sessionmaker() as db:
        r = await svc.process_turn(
            db, session_id=sid, call_id=call_id,
            user_text="haan, batao", persona=PERSONA,
        )
        await db.commit()
        assert r.state == ConvState.QUALIFICATION
    async with db_sessionmaker() as db:
        t1 = await svc.process_turn(
            db, session_id=sid, call_id=call_id,
            user_text="xyz jabberwocky nonsensical text here", persona=PERSONA,
        )
        await db.commit()
    async with db_sessionmaker() as db:
        t2 = await svc.process_turn(
            db, session_id=sid, call_id=call_id,
            user_text="xyz jabberwocky nonsensical text here", persona=PERSONA,
        )
        await db.commit()
    assert t1.state == t2.state == ConvState.QUALIFICATION
    assert t1.reply != t2.reply
