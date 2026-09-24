"""Focused tests for qualification property-type/BHK capture (fix session)."""

import pytest

from app.conversation.engine import ConversationEngine
from app.conversation.intents import classify_intent
from app.conversation.states import ConvState

ENGINE = ConversationEngine()

PERSONA = {
    "company": "Creatik AI",
    "properties": {
        "Sunrise Heights, Noida": "2/3/4 BHK",
        "Green Valley, Jaipur": "2/3 BHK villa/flat",
    },
}

# ------------------------------------------------------------- need-first flow


@pytest.mark.asyncio
async def test_case1_need_first_provides_bhk_not_asked_again():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="I need a 2 BHK flat in Jaipur", persona=PERSONA)
    assert r.state == ConvState.PERMISSION
    slots.update(r.slots or {})
    assert slots.get("bhk") == "2bhk"
    assert slots.get("location") == "Jaipur"

    r = await ENGINE.step(state=r.state, slots=slots, user_input="Sunrise Heights", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("property") == "Sunrise Heights, Noida"
    # property type/BHK already present -> qualification must NOT ask it again.
    assert "kis type ki property" not in r.reply.lower() and "property_type" in slots


@pytest.mark.asyncio
async def test_case2_need_first_provides_villa_not_asked_again():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="I want a villa in Jaipur", persona=PERSONA)
    assert r.state == ConvState.PERMISSION
    slots.update(r.slots or {})
    assert slots.get("property_type") == "villa"

    r = await ENGINE.step(state=r.state, slots=slots, user_input="Sunrise Heights", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("property_type") == "villa"
    assert "kis type ki property" not in r.reply.lower()


# --------------------------------------------------------- qualification flow


@pytest.mark.asyncio
async def test_case3_all_required_transitions_to_present():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="2 BHK flat in Jaipur", persona=PERSONA)
    slots.update(r.slots or {})
    r = await ENGINE.step(state=r.state, slots=slots, user_input="Sunrise Heights", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    r = await ENGINE.step(state=r.state, slots=slots, user_input="50 lakh", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert "Correct?" in r.reply
    # Answering a later question at the recap acts as implicit confirmation.
    r = await ENGINE.step(state=r.state, slots=slots, user_input="3 months", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    r = await ENGINE.step(state=r.state, slots=slots, user_input="investment", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.PRESENT
    assert slots.get("budget") == "50 lakh"
    assert slots.get("timeline") == "3 months"
    assert slots.get("purpose") == "investment"


@pytest.mark.asyncio
async def test_case4_missing_bhk_asks_property_question_once():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="Sunrise Heights", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.PERMISSION
    # PERMISSION + "50 lakh": property already in slots -> QUALIFICATION, budget captured
    r = await ENGINE.step(state=r.state, slots=slots, user_input="50 lakh", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    assert slots.get("property") == "Sunrise Heights, Noida"
    assert slots.get("budget") == "50 lakh"
    # Since bhk is missing, the NEXT question must ask for property type/BHK.
    r = await ENGINE.step(state=r.state, slots=slots, user_input="kuch nahi", persona=PERSONA)
    assert "kis type ki property" in r.reply.lower() or "bhk" in r.reply.lower()


@pytest.mark.asyncio
async def test_case5_all_required_in_one_utterance_goes_to_summary():
    slots = {}
    r = await ENGINE.step(
        state=ConvState.GREETING,
        user_input="2 BHK flat in Jaipur for 50 lakh within 3 months for investment",
        persona=PERSONA,
    )
    slots.update(r.slots or {})
    assert slots.get("bhk") == "2bhk"
    assert slots.get("location") == "Jaipur"
    r = await ENGINE.step(state=r.state, slots=slots, user_input="Sunrise Heights", persona=PERSONA)
    slots.update(r.slots or {})
    # All requirements captured in one shot -> recap before purpose/timeline.
    assert r.state == ConvState.SUMMARY_CONFIRM
    assert slots.get("budget") == "50 lakh"
    assert slots.get("timeline") == "3 months"
    assert slots.get("purpose") == "investment"


# -------------------------------------------------------- classifier level


def test_classifier_captures_all_slots_in_one_utterance():
    r = classify_intent("I need a 2 BHK flat in Jaipur")
    assert r.slots.get("bhk") == "2bhk"
    assert r.slots.get("property_type") == "2bhk"
    assert r.slots.get("location") == "Jaipur"


def test_classifier_bare_digit_months_is_timeline():
    r = classify_intent("3 months")
    assert r.slots.get("timeline") == "3 months"


def test_classifier_llm_slots_do_not_override_project_name():
    from app.conversation.intents import classify_intent

    # "Sunrise Heights" has no deterministic slot (it is a project name), so the
    # project name must never be turned into a location/property_type by rules.
    r = classify_intent("Sunrise Heights")
    assert r.intent == "other"
    assert r.slots == {}


# ------------------------------------------------- need-first inventory match


@pytest.mark.asyncio
async def test_need_first_single_match_presented():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="haan ji", persona=PERSONA)
    assert r.state == ConvState.PERMISSION
    # No property pitch in the permission opener.
    assert "Sunrise" not in r.reply and "Green Valley" not in r.reply
    slots.update(r.slots or {})
    r = await ENGINE.step(state=r.state, slots=slots, user_input="haan, batao", persona=PERSONA)
    slots.update(r.slots or {})
    assert r.state == ConvState.QUALIFICATION
    for text, _state in (
        ("villa in Jaipur", ConvState.QUALIFICATION),
        ("60 lakh", ConvState.SUMMARY_CONFIRM),
        ("yes", ConvState.QUALIFICATION),
        ("investment", ConvState.QUALIFICATION),
        ("2 months", ConvState.PRESENT),
    ):
        r = await ENGINE.step(state=r.state, slots=slots, user_input=text, persona=PERSONA)
        slots.update(r.slots or {})
        assert r.state == _state
    assert "Green Valley" in r.reply


@pytest.mark.asyncio
async def test_need_first_multiple_matches_pick_by_name():
    persona = {
        "company": "Creatik AI",
        "properties": {
            "A Heights, Jaipur": "2 BHK",
            "B Residency, Jaipur": "3 BHK",
        },
    }
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="haan ji", persona=persona)
    slots.update(r.slots or {})
    r = await ENGINE.step(state=r.state, slots=slots, user_input="haan, batao", persona=persona)
    slots.update(r.slots or {})
    for text in ("flat in Jaipur", "50 lakh", "yes", "self use", "1 month"):
        r = await ENGINE.step(state=r.state, slots=slots, user_input=text, persona=persona)
        slots.update(r.slots or {})
    assert r.state == ConvState.PRESENT
    assert "A Heights" in r.reply and "B Residency" in r.reply
    # Picking one by name moves to the visit offer.
    r = await ENGINE.step(state=r.state, slots=slots, user_input="B Residency", persona=persona)
    assert r.state == ConvState.CLOSING
    assert r.slots.get("property") == "B Residency, Jaipur"


@pytest.mark.asyncio
async def test_present_options_are_short_one_liners():
    """PRESENT lists max 3 one-line options (no full inventory detail)."""
    from app.conversation.engine import _render_present

    slots = {"bhk": "2bhk", "property_type": "2bhk", "location": "Noida", "budget": "40 lakh"}
    reply, candidates = _render_present(slots, PERSONA)
    assert 1 <= len(candidates) <= 3
    for name in candidates:
        assert name.split(",")[0].strip() in reply
    assert "Rs. 40 lakh se shuru" not in reply
    assert "Rs. 35 lakh se shuru" not in reply
    assert "pasand aaya" in reply


@pytest.mark.asyncio
async def test_need_first_no_match_offers_callback():
    slots = {}
    r = await ENGINE.step(state=ConvState.GREETING, user_input="haan ji", persona=PERSONA)
    slots.update(r.slots or {})
    r = await ENGINE.step(state=r.state, slots=slots, user_input="haan, batao", persona=PERSONA)
    slots.update(r.slots or {})
    for text in ("5 BHK in Delhi for 80 lakh", "yes", "investment", "3 months"):
        r = await ENGINE.step(state=r.state, slots=slots, user_input=text, persona=PERSONA)
        slots.update(r.slots or {})
    assert r.state == ConvState.CALLBACK
    assert "koi ready option nahi" in r.reply


# ------------------------------------------------------- feminine persona fix


def test_script_is_feminine_consistent():
    from app.conversation import responses as R

    assert "rahi hoon" in R.GREETING
    assert "dungi" in R.PROPERTY_ASK
    assert "doongi" in R.VISIT_DATE_ASK
    assert "karti hoon" in R.NOT_INTERESTED_SCRIPT
    assert "gayi" in R.DNC_CONFIRM
    assert "payi" in R.FALLBACK
    for name in ("GREETING", "PROPERTY_ASK", "VISIT_DATE_ASK",
                 "NOT_INTERESTED_SCRIPT", "DNC_CONFIRM", "FALLBACK"):
        text = getattr(R, name)
        assert "dunga" not in text and "doonga" not in text and "karta hoon" not in text


def test_tts_voice_default_is_female_priya():
    from app.config import Settings

    assert Settings.model_fields["SARVAM_TTS_VOICE"].default == "priya"