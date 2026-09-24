"""Unit tests for the deterministic conversation engine (PHASE 5)."""

import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.intents import classify_intent, needs_llm
from app.conversation.states import ConvState

ENGINE = ConversationEngine()

PERSONA = {
    "company": "Creatik AI",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK, Rs. 40 lakh se shuru",
        "Skyline Residency, Greater Noida": "2/3 BHK, Rs. 35 lakh se shuru",
    },
}


@pytest.mark.asyncio
async def test_first_turn_greets():
    r = await ENGINE.step(state=ConvState.GREETING, first_turn=True, caller_name="Rahul")
    assert r.state == ConvState.GREETING
    assert "Rahul" in r.reply
    assert r.reply.startswith("Namaste")


@pytest.mark.asyncio
async def test_full_happy_path():
    # Example-transcript flow: identity -> permission -> type -> location ->
    # budget -> summary -> purpose -> timeline -> present -> visit date/time.
    # GREETING -> PERMISSION (availability check, own projects NOT pitched)
    r = await ENGINE.step(
        state=ConvState.GREETING, user_input="haan, main Rahul hoon",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.PERMISSION
    assert "2 minute" in r.reply and "good time" in r.reply
    assert "projects hain" not in r.reply
    # PERMISSION yes -> QUALIFICATION asks property type first
    r = await ENGINE.step(
        state=r.state, slots=r.slots, user_input="Haan, batao",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.QUALIFICATION
    assert "kis type ki property" in r.reply
    # QUALIFICATION: ordered slots type -> location -> budget
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="2 BHK flat chahiye", persona=PERSONA)
    assert r.state == ConvState.QUALIFICATION
    assert "location" in r.reply.lower() or "area" in r.reply.lower()
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="noida", persona=PERSONA)
    assert r.state == ConvState.QUALIFICATION
    assert "budget" in r.reply.lower()
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="40 lakh", persona=PERSONA)
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "Correct?" in r.reply and "Noida" in r.reply and "40 lakh" in r.reply
    # Summary yes -> purpose -> timeline -> PRESENT options
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="Yes", persona=PERSONA)
    assert r.state == ConvState.QUALIFICATION
    assert "self-use" in r.reply
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="Khud rehne ke liye", persona=PERSONA)
    assert r.state == ConvState.QUALIFICATION
    assert "finalize" in r.reply
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="1-2 months", persona=PERSONA)
    assert r.state == ConvState.PRESENT
    assert "Sunrise Heights" in r.reply
    assert r.slots.get("bhk") == "2bhk"
    assert r.slots.get("budget") == "40 lakh"
    assert r.slots.get("location") == "Noida"
    assert r.slots.get("timeline") == "2 months"
    assert r.slots.get("purpose") == "self_use"
    # PRESENT yes -> CLOSING visit offer with weekday choice
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="Haan", persona=PERSONA)
    assert r.state == ConvState.CLOSING
    assert "Sunday" in r.reply
    # CLOSING weekday -> VISIT_BOOKING consolidated day + time ask
    r = await ENGINE.step(state=r.state, slots=r.slots, user_input="Sunday", persona=PERSONA)
    assert r.state == ConvState.VISIT_BOOKING
    assert "Ravivaar" in r.reply
    assert "11 AM" in r.reply and "4 PM" in r.reply
    # VISIT_BOOKING time -> END booked with name + WhatsApp promise
    r = await ENGINE.step(
        state=r.state, slots=r.slots, user_input="4 PM",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.END
    assert r.termination is True
    assert "Rahul ji" in r.reply and "Sunday" in r.reply
    assert "4 PM" in r.reply and "WhatsApp" in r.reply


@pytest.mark.asyncio
async def test_visit_day_time_ask_covers_all_at_once():
    """'Saturday' -> ONE turn with weekday + actual date + both times."""
    from app.conversation.engine import _upcoming_weekday_date

    r = await ENGINE.step(
        state=ConvState.CLOSING, slots={}, user_input="Saturday",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.VISIT_BOOKING
    expected = _upcoming_weekday_date("saturday")
    assert f"Shanivaar, {expected.day} {expected.strftime('%B')}" in r.reply
    assert "11 AM" in r.reply and "4 PM" in r.reply
    assert r.slots.get("preferred_date") == "saturday"


@pytest.mark.asyncio
async def test_visit_day_repeat_moves_to_time_nudge():
    """Repeating the day after the combined ask -> short time nudge."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "saturday"},
        user_input="haan ji, Saturday",
        persona=PERSONA,
    )
    assert r.state == ConvState.VISIT_BOOKING
    assert "11 AM" in r.reply and "4 PM" in r.reply


@pytest.mark.asyncio
async def test_visit_day_disagree_reasks_day():
    """'nahi' to the proposed day -> day asked again, date kept."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "saturday"},
        user_input="nahi, Sunday ko",
        persona=PERSONA,
    )
    assert r.state == ConvState.VISIT_BOOKING
    # New day proposed -> its own consolidated day + time ask.
    assert "Ravivaar" in r.reply
    assert "11 AM" in r.reply and "4 PM" in r.reply
    assert r.slots.get("preferred_date") == "sunday"


@pytest.mark.asyncio
async def test_visit_time_nudge_is_short_and_single():
    """Locked day + no time -> one short nudge, not rotating full questions."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "saturday"},
        user_input="haan ji",
        persona=PERSONA,
    )
    assert r.state == ConvState.VISIT_BOOKING
    assert "ke liye time bataiye" in r.reply
    assert "11 AM" in r.reply and "4 PM" in r.reply


@pytest.mark.asyncio
async def test_visit_booking_end_uses_approved_closing():
    """Day + time -> END with the approved booked/WhatsApp/Bye wording."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "saturday"},
        user_input="Saturday 11 baje",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.END
    assert r.termination is True
    assert "Rahul ji" in r.reply
    assert "book kar diya hai" in r.reply
    assert "WhatsApp" in r.reply
    assert "Dhanyawad, Bye!" in r.reply
    assert "Ji ji" not in r.reply


@pytest.mark.asyncio
async def test_visit_booking_unnamed_has_no_double_ji():
    """No lead name -> no 'Ji ji' doubling in the closing."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "sunday"},
        user_input="Sunday 4 PM",
        persona=PERSONA, caller_name=None,
    )
    assert r.state == ConvState.END
    assert "Ji ji" not in r.reply
    assert "WhatsApp" in r.reply


@pytest.mark.asyncio
async def test_unexpected_question_redirects():
    # An out-of-scope answer should acknowledge and redirect, never stall.
    r = await ENGINE.step(
        state=ConvState.PROPERTY_SELECTION, user_input="aap kahan se ho", persona=PERSONA
    )
    assert r.state == ConvState.PROPERTY_SELECTION
    assert "Kshama" in r.reply
    assert r.changed_state is False


@pytest.mark.asyncio
async def test_do_not_call_terminates_early():
    result = await ENGINE.step(
        state=ConvState.GREETING, user_input="nahi, mujhe call mat karo"
    )
    assert result.state == ConvState.DO_NOT_CALL
    assert result.termination is True


@pytest.mark.asyncio
async def test_not_interested_terminates():
    result = await ENGINE.step(state=ConvState.INTRO, user_input="nahi chahiye")
    assert result.state == ConvState.NOT_INTERESTED
    assert result.termination is True


def test_classify_bhk():
    r = classify_intent("mujhe 2 bhk chahiye", "QUALIFICATION")
    assert r.intent == "provide_bhk"
    assert r.slots["bhk"] == "2bhk"
    assert r.confidence >= 0.7


def test_classify_budget():
    r = classify_intent("budget itna hai, 1 crore tak", "QUALIFICATION")
    assert r.intent == "provide_budget"
    assert "crore" in r.slots["budget"]


def test_classify_timeline_singular_month():
    assert classify_intent("within 1 month", "QUALIFICATION").slots.get("timeline") == "1 month"
    assert classify_intent("2 months", "QUALIFICATION").slots.get("timeline") == "2 months"


def test_classify_dnc_high_priority():
    r = classify_intent("please do not call me again", "GREETING")
    assert r.intent == "do_not_call"


def test_classify_other_low_confidence_needs_llm():
    r = classify_intent("xyz jabberwocky nonsensical text here", "QUALIFICATION")
    assert needs_llm(r) is True


def test_classify_property_type_bhk_sets_property_type():
    r = classify_intent("2 bhk dekhna hai", "QUALIFICATION")
    assert r.slots["bhk"] == "2bhk"
    assert r.slots["property_type"] == "2bhk"


def test_classify_property_type_villa():
    r = classify_intent("mujhe villa chahiye", "QUALIFICATION")
    assert r.slots["property_type"] == "villa"


def test_classify_purpose():
    r = classify_intent("rehne ke liye chahiye", "QUALIFICATION")
    assert r.intent == "provide_purpose"
    assert r.slots["purpose"] == "self_use"


# ------------------------------------------------- Devanagari (Hindi script)


def test_transliterate_leaves_latin_untouched():
    from app.conversation.intents import transliterate_hindi

    assert transliterate_hindi("2 BHK flat in Jaipur") == "2 BHK flat in Jaipur"
    assert transliterate_hindi("") == ""


def test_classify_devanagari_confirmation():
    assert classify_intent("हां", "PERMISSION").intent == "confirmation"
    assert classify_intent("हां, बताओ", "PERMISSION").intent == "interested"


def test_classify_devanagari_bhk():
    r = classify_intent("मुझे 2 बीएचके फ्लैट चाहिए", "QUALIFICATION")
    assert r.slots["bhk"] == "2bhk"


def test_classify_devanagari_budget_range():
    r = classify_intent("50-60 लाख तक", "QUALIFICATION")
    assert r.slots["budget"] == "50-60 lakh"


def test_classify_devanagari_location():
    assert classify_intent("मानसरोवर साइड", "QUALIFICATION").slots["location"] == "Mansarovar"
    assert classify_intent("नोएडा में", "QUALIFICATION").slots["location"] == "Noida"


def test_classify_devanagari_timeline():
    assert classify_intent("1-2 महीने में", "QUALIFICATION").slots["timeline"] == "2 months"


def test_classify_devanagari_purpose():
    r = classify_intent("खुद रहने के लिए", "QUALIFICATION")
    assert r.slots["purpose"] == "self_use"


def test_classify_devanagari_not_interested():
    assert classify_intent("नहीं चाहिए", "PERMISSION").intent == "not_interested"


def test_find_property_devanagari_project_name():
    from app.conversation.intents import find_property

    names = ["Sunrise Heights, Noida", "Green Valley, Jaipur"]
    assert find_property("सनराइज हाइट्स", names) == "Sunrise Heights, Noida"


def test_capture_booking_devanagari():
    from app.conversation.engine import _capture_booking

    out = _capture_booking("रविवार 4 बजे")
    assert out["preferred_date"] == "ravivaar"
    assert out["preferred_time"] == "04:00"


@pytest.mark.asyncio
async def test_devanagari_full_need_flow():
    """Hindi-script speaker completes type->location->budget->summary."""
    slots = {}
    r = await ENGINE.step(state=ConvState.PERMISSION, user_input="हां, बताओ", persona=PERSONA)
    assert r.state == ConvState.QUALIFICATION
    for text in ("मुझे 2 बीएचके फ्लैट चाहिए", "नोएडा में", "40 लाख तक"):
        r = await ENGINE.step(state=r.state, slots=slots, user_input=text, persona=PERSONA)
        slots.update(r.slots or {})
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "2 BHK" in r.reply and "Noida" in r.reply and "40 lakh" in r.reply
    r = await ENGINE.step(state=r.state, slots=slots, user_input="हां", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("summary_ok") == "yes"


@pytest.mark.asyncio
async def test_fixed_provider_never_invokes_llm():
    """LLM_PROVIDER=fixed must never fire an external LLM, even on ambiguous input.

    Tested directly with a FixedLLMProvider (in test env the registry forces the
    mock stand-in via MOCK_MODE), to assert the guard that disables the LLM
    fallback for the `fixed` provider regardless of entry point.
    """
    from app.conversation.engine import ConversationEngine, ProviderLLMFallback
    from app.conversation.states import ConvState
    from app.providers.llm.fixed import FixedLLMProvider

    engine = ConversationEngine(llm=ProviderLLMFallback(FixedLLMProvider()))
    r = await engine.step(
        state=ConvState.GREETING, user_input="xyz jabberwocky nonsensical text"
    )
    assert r.used_llm is False
    assert r.llm_cost == 0.0
    assert r.intent == "other"


def test_strip_leading_ack_collapses_stacked_acks():
    from app.conversation.engine import _strip_leading_ack

    assert _strip_leading_ack("Perfect. Sabse pehle, type?") == "Sabse pehle, type?"
    assert _strip_leading_ack("Ji, samajh gayi. Perfect. Q?") == "Q?"
    assert _strip_leading_ack("Theek hai, Saturday ko?") == "Saturday ko?"
    assert _strip_leading_ack("Aapko location?") == "Aapko location?"
    assert _strip_leading_ack("") == ""


@pytest.mark.asyncio
async def test_bare_yes_has_single_acknowledgement():
    """'haan' with no detail -> one ack + SHORT pending-slot ask, never the
    whole question again."""
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION,
        slots={"bhk": "2bhk", "property_type": "2bhk"},
        user_input="haan",
        persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "Kaunsi location dekh rahe hain?" in r.reply
    assert "kis type ki property" not in r.reply.lower()
    lowered = r.reply.lower()
    assert "samajh gayi" in lowered or "theek hai" in lowered
    assert "samajh gayi. perfect" not in lowered
    assert "theek hai. theek hai" not in lowered


@pytest.mark.asyncio
async def test_bare_yes_asks_only_pending_slot():
    """Empty slots + 'haan' -> type short-form, not the full pitch."""
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION,
        slots={},
        user_input="haan",
        persona=PERSONA,
    )
    assert r.state == ConvState.QUALIFICATION
    assert "kuch pasand?" in r.reply
    assert "kis type ki property" not in r.reply.lower()


@pytest.mark.asyncio
async def test_visit_time_propose_after_repeated_no_progress():
    """Two empty turns on a locked day -> decisive 11 AM proposal (one-shot)."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={"preferred_date": "saturday"},
        user_input="haan ji",
        persona=PERSONA,
        reprompt=3,
    )
    assert r.state == ConvState.VISIT_BOOKING
    assert "lock kar doon" in r.reply
    assert "11 AM" in r.reply
    assert r.slots.get("time_proposed") == "11:00"


@pytest.mark.asyncio
async def test_visit_time_proposal_yes_books():
    """'haan' to the proposal books the proposed time and ends."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={
            "preferred_date": "saturday",
            "time_proposed": "11:00",
            "time_propose_offered": "yes",
        },
        user_input="haan, theek hai",
        persona=PERSONA, caller_name="Rahul Sharma",
    )
    assert r.state == ConvState.END
    assert r.termination is True
    assert "book kar diya hai" in r.reply


@pytest.mark.asyncio
async def test_visit_time_proposal_no_falls_back_to_nudge_once():
    """'nahi' to the proposal -> plain nudge, never proposes twice."""
    r = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots={
            "preferred_date": "saturday",
            "time_proposed": "11:00",
            "time_propose_offered": "yes",
        },
        user_input="nahi",
        persona=PERSONA,
    )
    assert r.state == ConvState.VISIT_BOOKING
    assert "lock kar doon" not in r.reply
    assert "11 AM" in r.reply
    r2 = await ENGINE.step(
        state=ConvState.VISIT_BOOKING,
        slots=r.slots,
        user_input="haan ji",
        persona=PERSONA,
        reprompt=5,
    )
    assert "lock kar doon" not in r2.reply


def test_is_bare_smalltalk():
    from app.conversation.intents import is_bare_smalltalk

    assert is_bare_smalltalk("Hello") is True
    assert is_bare_smalltalk("  achha ji ") is True
    assert is_bare_smalltalk("नमस्ते") is True
    assert is_bare_smalltalk("2 BHK chahiye") is False
    assert is_bare_smalltalk("xyz jabberwocky") is False
    assert is_bare_smalltalk("") is False


@pytest.mark.asyncio
async def test_llm_skipped_for_bare_smalltalk():
    """'Hello' must not burn a 1-3s LLM roundtrip for zero information."""
    from app.conversation.engine import ConversationEngine
    from app.conversation.intents import Intent

    calls = []

    class CountingLLM:
        async def classify(self, text, state):
            calls.append(text)
            from app.conversation.intents import Confidence, IntentResult

            return IntentResult(Intent.OTHER, 0.8, {})

    engine = ConversationEngine(llm=CountingLLM())
    r = await engine._detect("Hello", ConvState.QUALIFICATION.value)
    assert calls == []
    assert r.used_llm is False
    r2 = await engine._detect("xyz jabberwocky", ConvState.QUALIFICATION.value)
    assert len(calls) == 1
    assert r2.used_llm is True


@pytest.mark.asyncio
async def test_stuck_loop_exits_gracefully_after_three_strikes():
    """'haan' x N with zero info -> graceful WhatsApp exit, never infinite."""
    from app.conversation.engine import MAX_STUCK_TURNS

    slots = {}
    replies = []
    last = None
    for _ in range(6):
        last = await ENGINE.step(
            state=ConvState.QUALIFICATION, slots=slots,
            user_input="haan", persona=PERSONA,
        )
        # Replace semantics like ConversationService.save_state (update()
        # alone would keep stale keys the engine already dropped).
        slots.clear()
        slots.update(last.slots or {})
        replies.append(last.reply)
        if last.termination:
            break
    assert last.termination is True
    assert last.state == ConvState.END
    assert "WhatsApp" in last.reply
    assert "Bye!" in last.reply
    # Bounded: exits at exactly MAX_STUCK_TURNS no-progress turns.
    assert len(replies) == MAX_STUCK_TURNS
    assert "_stuck" not in slots


@pytest.mark.asyncio
async def test_stuck_counter_resets_on_progress():
    """An answer between stalls restarts the count (no premature exit)."""
    slots = {}
    r = await ENGINE.step(
        state=ConvState.QUALIFICATION, slots=slots,
        user_input="haan", persona=PERSONA,
    )
    slots.clear()
    slots.update(r.slots or {})
    assert r.termination is False
    r = await ENGINE.step(
        state=r.state, slots=slots, user_input="2 BHK", persona=PERSONA,
    )
    slots.clear()
    slots.update(r.slots or {})
    assert r.termination is False
    assert "_stuck" not in slots
    r = await ENGINE.step(
        state=r.state, slots=slots, user_input="haan", persona=PERSONA,
    )
    assert r.termination is False  # only 1 strike since the reset


@pytest.mark.asyncio
async def test_vague_answers_bounded_no_verbatim_repeat():
    """Difficult customer: terminates bounded, never repeats a line verbatim."""
    slots = {}
    state = ConvState.QUALIFICATION
    replies = []
    for i, text in enumerate(("pata nahi", "kuch bhi", "haan", "achha", "haan ji",
                              "bolo", "haan", "haan", "haan", "haan")):
        # reprompt=i mirrors ConversationService._count_reprompts: repeats
        # rotate phrasing instead of echoing the exact same line.
        r = await ENGINE.step(
            state=state, slots=slots, user_input=text, persona=PERSONA,
            reprompt=i,
        )
        slots.clear()
        slots.update(r.slots or {})
        state = r.state
        replies.append(r.reply)
        if r.termination:
            break
    assert r.termination is True  # always ends, never loops forever
    assert len(replies) <= 6  # bounded well under the input budget
    assert len(set(replies)) == len(replies)  # no exact line twice
