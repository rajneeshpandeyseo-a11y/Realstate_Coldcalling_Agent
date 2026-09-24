"""End-to-end test of the approved example real-call transcript.

Mirrors the user's reference Hinglish call turn-by-turn:
identity -> permission -> type -> location -> budget -> summary confirm ->
purpose -> timeline -> present 1-3 options -> interest -> visit date ->
visit time -> book + WhatsApp promise -> bye.

No network, no real calls: pure engine steps with mocks only.
"""

import pytest

from app.conversation.engine import ConversationEngine, _capture_booking
from app.conversation.intents import classify_intent
from app.conversation.states import ConvState

ENGINE = ConversationEngine()

PERSONA = {
    "company": "ABC Properties",
    "agent_name": "Neha",
    "properties": {
        "Sharma Heights, Mansarovar": "2/3 BHK flat, Rs. 55 lakh se shuru",
        "Vaishali Residency, Vaishali Nagar": "2 BHK flat, Rs. 50 lakh se shuru",
    },
}

NAME = "Rahul Sharma"


async def _say(state, slots, text):
    r = await ENGINE.step(
        state=state, slots=slots, user_input=text,
        persona=PERSONA, caller_name=NAME,
    )
    slots.update(r.slots or {})
    return r


@pytest.mark.asyncio
async def test_example_transcript_full_flow():
    slots = {}
    # CALL START: identity confirmation uses the lead's first name.
    r = await ENGINE.step(
        state=ConvState.GREETING, first_turn=True,
        persona=PERSONA, caller_name=NAME,
    )
    assert r.state == ConvState.GREETING
    assert "Rahul ji" in r.reply
    # Customer confirms identity -> permission / availability check.
    r = await _say(ConvState.GREETING, slots, "Haan, boliye")
    assert r.state == ConvState.PERMISSION
    assert "Rahul ji" in r.reply and "Neha" in r.reply
    assert "2 minute" in r.reply and "good time" in r.reply
    # Permission granted -> property type first.
    r = await _say(r.state, slots, "Haan, batao")
    assert r.state == ConvState.QUALIFICATION
    assert "kis type ki property" in r.reply
    # PROPERTY TYPE -> LOCATION -> BUDGET (no pitch of our projects yet).
    r = await _say(r.state, slots, "2 BHK flat chahiye")
    assert r.state == ConvState.QUALIFICATION
    assert "Sharma Heights" not in r.reply and "Vaishali" not in r.reply
    r = await _say(r.state, slots, "Mansarovar ya Vaishali Nagar side")
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("location") == "Mansarovar"
    r = await _say(r.state, slots, "Around 50-60 lakh")
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "Correct?" in r.reply
    assert "Mansarovar" in r.reply and "50-60 lakh" in r.reply
    # SUMMARY yes -> PURPOSE -> TIMELINE.
    r = await _say(r.state, slots, "Yes")
    assert r.state == ConvState.QUALIFICATION
    assert "self-use" in r.reply
    r = await _say(r.state, slots, "Khud rehne ke liye")
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("purpose") == "self_use"
    r = await _say(r.state, slots, "Maybe 1-2 months")
    # MATCH + PRESENT 1-3 suitable options.
    assert r.state == ConvState.PRESENT
    assert "Sharma Heights" in r.reply and "Vaishali Residency" in r.reply
    # CUSTOMER INTEREST -> SITE VISIT OFFER with weekday choice.
    r = await _say(r.state, slots, "Haan")
    assert r.state == ConvState.CLOSING
    assert "site visit" in r.reply.lower()
    assert "Sunday" in r.reply
    # DATE -> consolidated day + date + time ask in ONE turn.
    r = await _say(r.state, slots, "Sunday")
    assert r.state == ConvState.VISIT_BOOKING
    assert "Ravivaar" in r.reply
    assert "11 AM" in r.reply and "4 PM" in r.reply
    assert slots.get("preferred_date") == "sunday"
    # TIME -> BOOK + WhatsApp promise + bye (CRM update happens downstream).
    r = await _say(r.state, slots, "4 PM")
    assert r.state == ConvState.END
    assert r.termination is True
    assert "Rahul ji" in r.reply
    assert "Sunday" in r.reply and "4 PM" in r.reply
    assert "WhatsApp" in r.reply
    assert slots.get("preferred_time") == "16:00"


@pytest.mark.asyncio
async def test_permission_denied_goes_to_callback():
    slots = {}
    r = await _say(ConvState.GREETING, slots, "haan ji")
    assert r.state == ConvState.PERMISSION
    r = await _say(r.state, slots, "abhi time nahi hai")
    assert r.state == ConvState.CALLBACK


@pytest.mark.asyncio
async def test_permission_busy_goes_to_callback():
    slots = {}
    r = await _say(ConvState.GREETING, slots, "haan ji")
    r = await _say(r.state, slots, "main meeting mein hoon, baad me baat karte hain")
    assert r.state == ConvState.CALLBACK


@pytest.mark.asyncio
async def test_summary_correction_recapitulates():
    slots = {}
    r = await _say(ConvState.GREETING, slots, "haan ji")
    r = await _say(r.state, slots, "haan, batao")
    for text in ("2 BHK flat", "Mansarovar", "50 lakh"):
        r = await _say(r.state, slots, text)
    assert r.state == ConvState.SUMMARY_CONFIRM
    r = await _say(r.state, slots, "nahi, 3 BHK chahiye")
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "3 BHK" in r.reply and "Correct?" in r.reply
    assert slots.get("bhk") == "3bhk"


def test_classifier_budget_range():
    r = classify_intent("Around 50-60 lakh", "QUALIFICATION")
    assert r.slots.get("budget") == "50-60 lakh"


def test_classifier_jaipur_locality():
    assert classify_intent("Mansarovar side", "QUALIFICATION").slots.get("location") == "Mansarovar"
    assert classify_intent("Vaishali Nagar", "QUALIFICATION").slots.get("location") == "Vaishali Nagar"


def test_classifier_exploring_timeline():
    assert classify_intent("abhi just exploring", "QUALIFICATION").slots.get("timeline") == "exploring"


def test_capture_bare_hour_booking():
    assert _capture_booking("4 PM").get("preferred_time") == "16:00"
    assert _capture_booking("11 AM").get("preferred_time") == "11:00"
    assert _capture_booking("Sunday 4 PM").get("preferred_date") == "sunday"
