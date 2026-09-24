"""Canned Hinglish responses used by the deterministic flow.

Keeping these local means most turns cost ZERO LLM/TTS-derivative computation;
the same strings are used for both the logical response and (eventually) the
TTS prompt. Placeholders like {name}, {location} are substituted at render time.
"""

# ------------------------------------------------------------------ templates

GREETING = (
    "Namaste! Main {company} se bol rahi hoon. Kya main {first_name} ji se baat "
    "kar rahi hoon?"
)

# Used when the lead name is unknown (never "aap ji").
GREETING_UNNAMED = (
    "Namaste! Main {company} se bol rahi hoon. Kya main aapse baat kar rahi "
    "hoon?"
)

# Availability / permission check right after identity confirmation.
# {first_name} is omitted when the lead name is unknown.
PERMISSION_ASK_NAMED = (
    "Hi {first_name} ji, main {agent_name} bol rahi hoon {company} se. Aapne "
    "property ke regarding inquiry ki thi. Main bas 2 minute mein aapki "
    "requirement samajhna chahti hoon, phir uske according options suggest kar "
    "dungi. Is this a good time?"
)

PERMISSION_ASK = (
    "Namaste! Main {agent_name} bol rahi hoon {company} se. Aapne property ke "
    "regarding inquiry ki thi. Main bas 2 minute mein aapki requirement samajhna "
    "chahti hoon, phir uske according options suggest kar dungi. Is this a good "
    "time?"
)

REPROMPT_IDENTITY = (
    "Forgive me, kya main Sahi person se baat kar rahi hoon? Aapka naam bataiye, "
    "please."
)

INTRO = (
    "Ji, {company} ek trusted real estate services provider hai. Humare paas kai "
    "projects hain - {property_list}. Kya aap kisi property/talash mein hain?"
)

# Need-first discovery: ask the customer's requirement BEFORE pitching.
NEED_ASK = (
    "Bahut badhiya! Pehle main aapki zaroorat samajh leti hoon. Bataiye - "
    "aapko kaisi property chahiye? Jaise kitne BHK, kis location mein, aur "
    "roughly kya budget hai?"
)

# Presented when the customer's need matches inventory (before qualifying rest).
MATCH_FOUND = (
    "Bahut badhiya! Aapki requirement ke hisaab se humare paas {property} - "
    "{property_desc} hai, jo bilkul suitable lag raha hai."
)

# Presented when several inventory options match and the customer must pick.
MATCH_MULTIPLE = (
    "Theek hai. Aapki requirement par humare paas ye options hain - {options}."
)

# Presented when the complete need matches nothing in inventory.
NO_MATCH_OFFER = (
    "Samajh gayi. Abhi aapki is requirement par humare paas koi ready option "
    "nahi hai. Koi baat nahi -"
)

# Ask which specific property the customer is interested in.
PROPERTY_ASK = (
    "Theek hai. Please bataiye, aapko in projects me se kaunsi property/interested hai? "
    "Main aapko uski details bata dungi."
)

# Confirmation after the customer picks a property, before qualifying.
PROPERTY_CONFIRM = (
    "Achha, {property} - {property_desc}. Badhiya choice! Chaliye main aapki "
    "requirements samajh leti hoon."
)

# Polite out-of-scope / unexpected question response. Redirects back to the
# question being asked so the conversation never stalls.
FALLBACK_OUT_OF_SCOPE = (
    "Kshama kijiye, main is baare mein poori jaankari nahi rakh sakti. Agar aap "
    "mere projects, price aur site visit ke baare mein poochhna chahen toh main "
    "usme madad kar sakti hoon."
)

QUALIFICATION_BHK = "Okay. Aapko kaunsa layout chahiye - 2, 3 ya 4 BHK?"
QUALIFICATION_TYPE = (
    "Perfect. Sabse pehle, aap kis type ki property dekh rahe hain - 1 BHK, "
    "2 BHK, 3 BHK, flat ya villa?"
)
QUALIFICATION_BUDGET = "Okay. Aur approximately aapka budget range kya rahega?"
QUALIFICATION_LOCATION = "Theek hai. Aapko kis location ya area mein chahiye?"
QUALIFICATION_TIMELINE = (
    "Theek hai. Aap property kab tak finalize karna prefer karenge? Within 1 month, 2-3 "
    "months, ya abhi just exploring?"
)
QUALIFICATION_PURPOSE = (
    "Theek hai. Aap property self-use ke liye dekh rahe hain ya investment ke liye?"
)

# Short re-asks for bare confirmations ("haan" with no info): the caller is
# still listening, not answering - so ask ONLY the pending slot in a few
# words instead of repeating the whole question again (live callers heard
# the full question twice and felt unheard).
QUALIFICATION_TYPE_SHORT = "2 BHK, 3 BHK, flat ya villa - kuch pasand?"
QUALIFICATION_LOCATION_SHORT = "Kaunsi location dekh rahe hain?"
QUALIFICATION_BUDGET_SHORT = "Budget range kya rahega?"
QUALIFICATION_PURPOSE_SHORT = "Self-use ya investment?"
QUALIFICATION_TIMELINE_SHORT = "Kab tak finalize karna hai?"

# Recap of type + location + budget before moving to purpose/timeline.
NEED_SUMMARY = "Theek hai. Matlab aapko {need} mein chahiye. Correct?"

# Warm acknowledgement prefixed when the customer says a bare "haan/yes"
# without new detail, so the repeated question never sounds robotic.
YES_NUDGE = "Ji, samajh gayi."

# Presented after purpose + timeline: up to 3 matched inventory options.
PRESENT_OPTIONS = (
    "Perfect. Aapki requirement ke according hamare paas ye suitable options "
    "hain - {options}. Main aapko inke details share kar sakti hoon. Bataiye, "
    "inme se koi option pasand aaya?"
)

OBJECTION_BUDGET = (
    "Main samajh sakti hoon. Hume flexible payment plan hai - aapko apne budget "
    "ke hisaab se option milega. Kya main aapko kuch option bata doon?"
)
OBJECTION_PRICE = (
    "Samajh gayi. Prices hamaari market se competitive hain aur booking abhi chhutti offers ke "
    "saath chal rahi hai. Kya main exact price list bhej doon?"
)
OBJECTION_TRUST = (
    "Bilkul samajh sakti hoon - yeh bada decision hai. Aap chahe to site visit "
    "karke khud check kar sakte hain, ya official documents dekh sakte hain."
)
OBJECTION_COMPETITION = (
    "Haan, baazar mein kai options hain. Hume alag banata hai ki hamaara location "
    "connectivity aur delivery time trustworthy hai. Kya main aapko comparison "
    "bhej sakti hoon?"
)
OBJECTION_GENERIC = (
    "Main aapki baat samajh gayi. Kya main aapko thoda detail bata sakti hoon "
    "taaki aap faisla kar saken?"
)

VISIT_OFFER = (
    "Bahut badhiya. Phir ek kaam karte hain - main aapke liye suitable property ka "
    "site visit arrange kar deti hoon, jisse aap actual property dekh saken. "
    "Aapke liye Saturday ya Sunday mein kaunsa convenient rahega?"
)

VISIT_DATE_ASK = (
    "Theek hai. Bataiye, Saturday ya Sunday mein kaunsa din convenient rahega? Main aapke "
    "liye slot book kar doongi."
)

# Single day confirmation with the actual calendar date (fix for the
# confirm-loop: once the customer names a day we lock it with one clear
# confirmation instead of re-asking day/time over and over).
VISIT_DAY_CONFIRM = (
    "Toh {weekday_hi}, {date} ko milte hain - sahi hai?"
)

# Consolidated day + time ask: weekday, actual date and both time choices
# in ONE turn, so booking takes one round instead of three.
VISIT_DAY_TIME_ASK = (
    "Toh {weekday_hi}, {date} ko milte hain - 11 AM ya 4 PM, kya convenient rahega?"
)

# Short time nudge when the day is already locked but no time came yet.
# Deliberately a single phrasing (no rotation): repeating variants of the
# same question is exactly what sounded like a broken loop on live calls.
VISIT_TIME_NUDGE = (
    "Theek hai, {date} ke liye time bataiye - 11 AM ya 4 PM?"
)

# Decisive proposal after repeated no-progress turns (human agents stop
# open-asking and propose: "11 baje lock kar doon?"). One-shot per call:
# yes/neutral books the proposed time, a real time/day overrides it.
VISIT_TIME_PROPOSE = (
    "Toh {date} 11 AM lock kar doon - sahi hai?"
)

VISIT_TIME_ASK = (
    "Theek hai, {date} ko 11 AM ya 4 PM, inmein se kya convenient rahega?"
)

VISIT_CONFIRM = (
    "Perfect! Maine aapka visit {date_time} ke liye note kar liya hai. Confirm "
    "karke bataiye - main {location} par guide ke saath milne ki arrangement kar "
    "doon."
)

# Final booking confirmation: date + time locked, WhatsApp follow-up promised.
VISIT_BOOK_CONFIRM = (
    "Perfect {first_name} ji, maine ye visit book kar diya hai. Visit se "
    "pehle aapko location aur property details WhatsApp par share kar "
    "dungi. Aapse {date} {time} par milte hain. Dhanyawad, Bye!"
)

VISIT_PENDING_CONFIRM = (
    "Theek hai. Kya main aapke visit ki date time confirm kar doon?"
)

CALLBACK_ASK = (
    "Koi baat nahi. Aap bataiye kab call karna sahi rahega - main us same par "
    "waps call kar doon?"
)
CALLBACK_CONFIRM = (
    "Theek hai, main aapko {when} par call kar doon. Dhanyavad!"
)

# PHASE 1.5 live-stream no-response nudges (short, TTS-cheap). Nudge 1 is a
# gentle check; nudge 2 is the goodbye played right before graceful hangup.
NUDGE_SILENCE_1 = (
    "Hello? Aap mujhe sun pa rahe hain? Main {agent} bol rahi hoon {company} "
    "se. Kya abhi baat karne ka good time hai?"
)
NUDGE_SILENCE_2 = (
    "Lagta hai aap busy hain. Koi baat nahi, main baad mein call kar lungi. "
    "Dhanyavad!"
)

NOT_INTERESTED_SCRIPT = (
    "Koi baat nahi, main aapki madad karti hoon jab zaroorat ho. Dhanyavad, "
    "good day!"
)

DNC_CONFIRM = (
    "Bilkul, samajh gayi. Aapko ab koi call nahi aayegi. Dhanyavad ji!"
)

THANK_YOU = (
    "Bahut dhanyavad aapka time dene ke liye. Aapka din shubh ho!"
)

# Graceful stuck-loop exit (no-progress turns reached MAX_STUCK_TURNS): a
# real agent wraps up with a WhatsApp promise instead of nagging forever.
STUCK_EXIT = (
    "Lagta hai main aapki baat sahi se samajh nahi pa rahi hoon. Koi baat "
    "nahi - main aapko saari property details WhatsApp par bhej deti hoon, "
    "aap aaram se dekh lijiyega. Bahut dhanyawad ji, Bye!"
)

FALLBACK = (
    "Kshama kijiye, main theek se samajh nahi payi. Kya aap apni requirement "
    "ek baar phir bataenge?"
)

# Short warm re-ask for unclear/garbled input (no question detected): one
# line, no full repeat-request, no LLM roundtrip. Used when the caller
# mumbled or the line broke - not when they asked a real question.
UNCLEAR_REASK = (
    "Ji, aawaz thodi kat gayi - ek baar phir se bataiye?"
)

SUMMARY_PREFIX = "BHK {bhk}, {location}, budget {budget}, within {timeline}, {purpose}"


# ------------------------------------------------- PHASE 1.4 reprompt pools
# Alternate phrasings so a repeated question never sounds robotic. Index 0 of
# every pool is the primary template above (existing behaviour/tests); the
# engine picks pool[reprompt % len(pool)]. Every variant preserves the key
# substring the flow/tests rely on (see comments).

_VARIANTS: dict[str, list[str]] = {
    "qualification_type": [  # keep "kis type ki property"
        QUALIFICATION_TYPE,
        "Theek hai. Bataiye, aapko kis type ki property chahiye - 1 BHK, "
        "2 BHK, 3 BHK, flat ya villa?",
        "Samajh gayi. Ek baar phir bataiye - kis type ki property dekh rahe "
        "hain, 1 BHK, 2 BHK, 3 BHK, flat ya villa?",
    ],
    "qualification_location": [  # keep "location"/"area"
        QUALIFICATION_LOCATION,
        "Okay. Kaunsi location ya area prefer karenge?",
        "Samajh gayi. Bataiye, kis location ya area mein property chahiye?",
    ],
    "qualification_budget": [  # keep "budget"
        QUALIFICATION_BUDGET,
        "Theek hai. Aapka budget range approximately kya rahega?",
        "Samajh gayi. Ek baar phir bataiye - budget range kya rahega?",
    ],
    "qualification_purpose": [  # keep "self-use"
        QUALIFICATION_PURPOSE,
        "Okay. Ye property self-use ke liye chahiye ya investment ke liye?",
        "Samajh gayi. Bataiye, self-use ke liye dekh rahe hain ya investment "
        "ke liye?",
    ],
    "qualification_timeline": [  # keep "finalize"
        QUALIFICATION_TIMELINE,
        "Okay. Property kab tak finalize karna chahenge? Within 1 month, 2-3 "
        "months, ya abhi just exploring?",
        "Samajh gayi. Bataiye, kab tak finalize karna prefer karenge - "
        "within 1 month, 2-3 months, ya abhi just exploring?",
    ],
    "qualification_bhk": [  # keep "BHK"
        QUALIFICATION_BHK,
        "Theek hai. Kaunsa layout prefer karenge - 2, 3 ya 4 BHK?",
        "Samajh gayi. Bataiye, kaunsa layout chahiye - 2, 3 ya 4 BHK?",
    ],
    "visit_date_ask": [  # keep "Saturday"/"Sunday"
        VISIT_DATE_ASK,
        "Okay. Saturday ya Sunday - kaunsa din theek rahega? Main slot book "
        "kar doongi.",
        "Samajh gayi. Ek baar phir bataiye, Saturday ya Sunday mein kaunsa "
        "din convenient rahega?",
    ],
    "visit_time_ask": [  # keep "11 AM"/"4 PM", needs {date}
        VISIT_TIME_ASK,
        "Okay, {date} ko 11 AM ya 4 PM mein se kaunsa time theek rahega?",
        "Samajh gayi. {date} ko phir se confirm kar dijiye - 11 AM ya 4 PM?",
    ],
    "need_summary": [  # keep "Correct?", needs {need}
        NEED_SUMMARY,
        "Okay. Matlab aapko {need} mein chahiye. Correct?",
        "Samajh gayi. Matlab aapko {need} mein chahiye. Correct?",
    ],
    "yes_nudge": [
        YES_NUDGE,
        "Ji, theek hai.",
        "Ji, samajh gayi, koi baat nahi.",
    ],
    "fallback_out_of_scope": [
        FALLBACK_OUT_OF_SCOPE,
        "Maaf kijiye, main samajh nahi payi. Agar aap mere projects, price "
        "aur site visit ke baare mein poochhen toh main usme madad kar sakti "
        "hoon.",
        "Samajh nahi payi, maaf kijiye. Projects, price ya site visit se "
        "related poochhiye, main turant bata dungi.",
    ],
    "objection_generic": [
        OBJECTION_GENERIC,
        "Ji, aapki baat theek hai. Kya main short mein detail bata doon taaki "
        "aap aasaani se faisla kar saken?",
        "Koi baat nahi. Main bas 30 second mein detail bata deti hoon, phir "
        "aap faisla kar lijiyega?",
    ],
    # Permission re-ask keeps "2 minute" + "good time" in every variant.
    # Needs {agent_name}/{company} (+ {first_name} for named).
    "permission_ask": [
        PERMISSION_ASK,
        "Theek hai. Main {agent_name} bol rahi hoon {company} se - bas 2 "
        "minute mein aapki requirement samajhna chahti hoon. Kya abhi baat "
        "karne ka good time hai?",
        "Samajh gayi. Main {agent_name} hoon {company} se, sirf 2 minute "
        "chahiye requirement ke liye. Bataiye, kya abhi good time hai?",
    ],
    "permission_ask_named": [
        PERMISSION_ASK_NAMED,
        "Theek hai {first_name} ji. Main {agent_name} bol rahi hoon {company} "
        "se - bas 2 minute mein requirement samajhna chahti hoon. Kya abhi "
        "baat karne ka good time hai?",
        "Samajh gayi {first_name} ji. Main {agent_name} hoon {company} se, "
        "sirf 2 minute chahiye. Bataiye, kya abhi good time hai?",
    ],
}


def variant(key: str, attempt: int = 0, **kwargs) -> str:
    """Return alternate phrasing pool[key][attempt % len] rendered.

    attempt=0 is always the primary template (backward compatible).
    """
    pool = _VARIANTS.get(key) or [""]
    try:
        idx = max(0, int(attempt or 0)) % len(pool)
    except (TypeError, ValueError):
        idx = 0
    return render(pool[idx], **kwargs)


def render(template: str, **kwargs) -> str:
    """Render a template with safe substitutions."""
    result = template
    for key, value in kwargs.items():
        placeholder = "{" + key + "}"
        if placeholder in result:
            result = result.replace(placeholder, str(value))
    return result


def build_summary(slots: dict) -> str:
    """Create a short human summary string from collected slots."""
    bhk = slots.get("bhk") or slots.get("property_type") or "any"
    loc = slots.get("location", "your preferred area")
    budget = slots.get("budget", "your budget")
    timeline = slots.get("timeline", "soon")
    purpose = slots.get("purpose", "purchase")
    return render(SUMMARY_PREFIX, bhk=bhk, location=loc, budget=budget, timeline=timeline, purpose=purpose)


def _display_type(slots: dict) -> str:
    """Human display of the property type, e.g. '2 BHK flat' / 'villa'."""
    bhk = (slots.get("bhk") or "").strip()
    ptype = (slots.get("property_type") or "").strip()
    bhk_disp = ""
    if bhk:
        bhk_disp = bhk[:-3].strip().upper() + " BHK" if bhk.lower().endswith("bhk") else bhk
    if bhk and ptype and ptype.lower() != bhk.lower():
        return f"{bhk_disp} {ptype}"
    return bhk_disp or ptype or "property"


def build_need_summary(slots: dict) -> str:
    """Recap type + location + budget for the SUMMARY_CONFIRM step.

    Example: "2 BHK flat, Mansarovar, around 60 lakh".
    """
    loc = slots.get("location", "your preferred area")
    budget = slots.get("budget", "your budget")
    return f"{_display_type(slots)}, {loc}, around {budget}"
