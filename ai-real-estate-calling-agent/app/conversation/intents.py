"""Deterministic intent detection + slot extraction.

Rule-based classification is the DEFAULT (cheap, no API cost). Inputs that
cannot be confidently classified return a low-confidence result so the caller
can decide to invoke the LLM fallback instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- intent enum


class Intent(str):
    """Canonical conversation intents recognised by the engine."""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    PROVIDE_PROPERTY = "provide_property"
    BUDGET = "provide_budget"
    BHK = "provide_bhk"
    LOCATION = "provide_location"
    TIMELINE = "provide_timeline"
    PURPOSE = "provide_purpose"
    OBJECTION = "objection"
    CONFIRM_VISIT = "confirm_visit"
    PREFER_DATE = "provide_preferred_date"
    CALLBACK = "request_callback"
    DO_NOT_CALL = "do_not_call"
    GREETING = "greeting"
    CONFIRMATION = "confirmation"   # yes / no / okay
    CLARIFICATION = "clarification" # didn't understand
    OTHER = "other"


class Confidence:
    """Confidence thresholds for rule matching."""

    HIGH = 0.9
    MEDIUM = 0.7
    LOW = 0.4


@dataclass
class IntentResult:
    """Outcome of intent detection."""

    intent: Intent
    confidence: float
    slots: dict[str, str] = field(default_factory=dict)
    used_llm: bool = False
    cost: float = 0.0


# ------------------------------------------------------------------ built-ins


# Affirmative / negative / confirmation phrases (Hinglish).
AFFIRMATIVE = {
    "haan", "ha", "han", "yes", "yep", "yeah", "ji", "hmm", "hai", "theek hai",
    "theek", "hoga", "hona chahiye", "bilkul", "zaroor", "acha", "sahi", "okay",
    "ok", "sure", "mujhe chahiye", "chahiye", "dekhna hoon", "dekhna chahta",
    "dekhenge", "interested", "interest hai", "possible",
}
NEGATIVE = {
    "na", "nahi", "nhi", "no", "nope", "not", "nahi chahiye", "nahi bolta",
    "nahi ji", "bhai nahi", "nahi karna", "koi zarurat nahi", "ruko", "ruko ji",
}
POSITIVE_INTEREST = {
    "interest", "interested", "chahiye", "dekhna", "sun len", "batao", "bataiye",
    "jaankari", "jankari", "details", "detail", "price", "rate", "kya price",
    "kitne ka", "kitne ki", "budget", "rate kya", "plan", "project details",
}
NEGATIVE_INTEREST = {
    "nahi chahiye", "koi zaroorat nahi", "no need", "not interested",
    "interest nahi", "nhi chahiye", "ho gaya", "already", "purchase kar liya",
    "le liya", "abhi nahi", "time nahi", "door hai", "far hai",
}


# Keyword -> intent maps (substring matching).
OBJECTION_MARKERS = {
    "paisa nahi": "budget",
    "paise nahi": "budget",
    "budget nahi": "budget",
    "rates bahut": "price",
    "kitna hai": "price",
    "book nahi": "skeptic",
    "scam": "trust",
    "fraud": "trust",
    "dhoka": "trust",
    "trust nahi": "trust",
    "compare": "competition",
}
DNC_MARKERS = ["do not call", "d mat karo", "call mat karo", "call mat", "pls dont call", "please don't call"]
CALLBACK_MARKERS = ["call back", "call kar lena", "bata do", "thoda baad me", "baad me call", "kal call", "phir call",
                    "baad me", "baad mein", "busy hoon", "busy hu", "free nahi", "free nahin",
                    "phir baat", "phir kabhi", "meeting me", "meeting mein", "drive kar"]

# Phrases that accept a proposed visit / offer.
VISIT_ACCEPT_MARKERS = [
    "visit karna", "site visit", "dekhna hai", "dekhenge", "visit ke liye",
    "haan visit", "visit chahiye", "book karo", "haan batao", "thik hai visit",
    "karni hai", "hoga visit", "karna chahta", "karna chahti",
]


# Number words -> digit (approximate; fine for our slot extraction).
HINGLISH_NUM_MAP = {
    "ek": "1", "do": "2", "teen": "3", "char": "4", "paanch": "5",
    "chhah": "6", "che": "6", "saat": "7", "aath": "8", "nau": "9", "das": "10",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
}


# ------------------------------------------- Devanagari -> Roman normalisation

# STT often returns Hindi in Devanagari script ("हां", "50 लाख"). Every rule
# below is Latin-centric, so normalise common Hindi words to their Roman form
# FIRST - then all existing patterns work unchanged for both scripts.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_DEVANAGARI_WORDS = {
    # Agreement / disagreement.
    "हां": "haan", "हाँ": "haan", "हा": "ha", "जी": "ji", "ठीक": "theek",
    "अच्छा": "acha", "अच्छे": "acha", "सही": "sahi", "बिल्कुल": "bilkul",
    "बिलकुल": "bilkul", "ज़रूर": "zaroor", "जरूर": "zaroor", "ओके": "ok",
    "नहीं": "nahi", "नही": "nahi", "ना": "na", "मत": "mat",
    "नमस्ते": "namaste", "हैलो": "hello", "हेलो": "hello",
    # Money.
    "लाख": "lakh", "करोड़": "crore", "करोड": "crore", "हज़ार": "thousand",
    "हजार": "thousand", "रुपए": "rs", "रुपये": "rs",
    # Property.
    "बीएचके": "bhk", "फ्लैट": "flat", "फ्लेट": "flat", "विला": "villa",
    "अपार्टमेंट": "apartment", "कमरा": "room", "मकान": "house", "घर": "ghar",
    # Time.
    "महीना": "mahina", "महीने": "mahine", "महिने": "mahine",
    "जल्दी": "jaldi", "जल्द": "jaldi", "तुरंत": "jaldi",
    "साल": "saal", "हफ्ता": "hafta", "तारीख": "tarikh",
    # Purpose.
    "रहने": "rehne", "रहना": "rehna", "खुद": "khud", "लिए": "liye",
    "निवेश": "invest", "किराया": "rent", "किराए": "rent",
    # Availability / callback.
    "बाद": "baad", "में": "me", "मे": "me", "अभी": "abhi",
    "समय": "time", "टाइम": "time", "बिज़ी": "busy", "व्यस्त": "busy",
    "मीटिंग": "meeting", "फिर": "phir", "कल": "kal", "आज": "aaj",
    "परसों": "parso", "बजे": "baje",
    # Weekdays / day parts (match _capture_booking vocab).
    "रविवार": "ravivaar", "सोमवार": "somwaar", "मंगलवार": "mangalwaar",
    "बुधवार": "budhwaar", "गुरुवार": "guruwaar", "शुक्रवार": "shukrawaar",
    "शनिवार": "shanivaar", "सुबह": "subah", "सवेरे": "subah",
    "दोपहर": "dopahar", "शाम": "shaam", "रात": "raat",
    # Cities / localities (match _find_location vocab).
    "नोएडा": "noida", "ग्रेटर": "greater", "गुड़गांव": "gurgaon",
    "गुड़गाव": "gurgaon", "गुरुग्राम": "gurugram", "जयपुर": "jaipur",
    "दिल्ली": "delhi", "मुंबई": "mumbai", "पुणे": "pune",
    "मानसरोवर": "mansarovar", "वैशाली": "vaishali", "नगर": "nagar",
    "मालवीय": "malviya", "जगतपुरा": "jagatpura", "सांगानेर": "sanganer",
    "अजमेर": "ajmer", "टोंक": "tonk", "रोड": "road", "पार्क": "park",
    "साइड": "side", "एरिया": "area",
    # Project-name fragments (demo inventory transliteration).
    "सनराइज": "sunrise", "हाइट्स": "heights", "स्काईलाइन": "skyline",
    "रेजिडेंसी": "residency", "ग्रीन": "green", "वैली": "valley",
    "गुरगांव": "gurgaon",
    # Misc frequent words.
    "चाहिए": "chahiye", "बताओ": "batao", "बताइए": "bataiye",
    "देख": "dekh", "देखना": "dekhna", "रहा": "raha", "रही": "rahi",
    "हूं": "hoon", "हूँ": "hoon", "है": "hai", "हो": "ho", "क्या": "kya",
    "कैसा": "kaisa", "कैसी": "kaisi", "कौन": "kaun", "कहां": "kahan",
    "कहाँ": "kahan", "कितना": "kitna", "कितने": "kitne", "बजट": "budget",
    "लोकेशन": "location", "जगह": "location", "तुम": "tum", "आप": "aap",
    "मैं": "main", "मुझे": "mujhe", "मेरा": "mera", "मेरी": "meri",
    "और": "aur", "या": "ya", "के": "ke", "की": "ki", "का": "ka",
    "को": "ko", "से": "se", "पर": "par", "तक": "tak", "लगभग": "around",
}

_DEVANAGARI_RE = re.compile(
    "(" + "|".join(sorted(map(re.escape, _DEVANAGARI_WORDS), key=len, reverse=True)) + ")"
)


def transliterate_hindi(text: str) -> str:
    """Map Devanagari Hindi words/digits to Roman equivalents (best-effort).

    Latin text passes through untouched. Unknown Devanagari words are kept
    as-is so the LLM fallback can still read the original meaning.
    """
    if not text or not re.search(r"[\u0900-\u097F]", text):
        return text
    out = text.translate(_DEVANAGARI_DIGITS)
    return _DEVANAGARI_RE.sub(lambda m: _DEVANAGARI_WORDS[m.group(1)], out)


# ------------------------------------------------------------------ parsing


def _find_bhk(text: str) -> str | None:
    """Extract a BHK variant: '2bhk', '3 bhk', 'do bhk', etc."""
    m = re.search(r"(\d)\s?(?:bhk|rk|bdr)\b", text, re.IGNORECASE)
    if m:
        return f"{m.group(1)}bhk"
    m = re.search(r"\b(one|two|three|four|five|ek|do|teen|char|paanch)\s+bhk\b", text, re.IGNORECASE)
    if m:
        word = m.group(1).lower()
        return f"{HINGLISH_NUM_MAP.get(word, word)}bhk"
    return None


def _find_budget(text: str) -> str | None:
    """Extract a budget figure like '30 lakh', '1 crore', '30L', '1 cr'.

    Ranges ("50-60 lakh", "50–60 lakh") keep both ends: "50-60 lakh".
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-\u2013\u2014]\s*(\d+(?:\.\d+)?)\s?(?:lakh|laakh|lac)", text, re.IGNORECASE)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return f"{lo:.0f}-{hi:.0f} lakh"
    m = re.search(r"(\d+(?:\.\d+)?)\s?(?:lakh|laakh|lac)", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return f"{val:.0f} lakh"
    m = re.search(r"(\d+(?:\.\d+)?)\s?(?:cr|kot|crore)\b", text, re.IGNORECASE)
    if m:
        val = float(m.group(1))
        return f"{val:.1f} crore"
    m = re.search(r"\b(?:upto|up to|till|around|under)\s+(\d+)\b", text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _find_location(text: str) -> str | None:
    """Extract a likely city/locality name (best-effort word pass)."""
    # Common city names / areas; extend as needed.
    cities = {
        "mumbai": "Mumbai", "pune": "Pune", "delhi": "Delhi", "noida": "Noida",
        "gurgaon": "Gurgaon", "gurugram": "Gurugram", "bangalore": "Bangalore",
        "hyderabad": "Hyderabad", "chennai": "Chennai", "kolkata": "Kolkata",
        "ahmedabad": "Ahmedabad", "jaipur": "Jaipur", "indore": "Indore",
        "lucknow": "Lucknow", "chandigarh": "Chandigarh",
        # Jaipur localities (primary market).
        "mansarovar": "Mansarovar", "vaishali": "Vaishali Nagar",
        "malviya nagar": "Malviya Nagar", "c-scheme": "C-Scheme",
        "cscheme": "C-Scheme", "jagatpura": "Jagatpura",
        "ajmer road": "Ajmer Road", "tonk road": "Tonk Road",
        "bapu nagar": "Bapu Nagar", "raja park": "Raja Park",
        "civil lines": "Civil Lines", "sanganer": "Sanganer",
    }
    lowered = text.lower()
    for key, name in cities.items():
        if key in lowered:
            return name
    # Generic Hinglish locality: "<Word> Nagar/Colony/Sector/Road/..." or
    # "<Word> side" (e.g. "Vaishali Nagar side", "Mansarovar side").
    stop = {
        "mere", "aapke", "us", "is", "kis", "iss", "uss", "yeh", "yah",
        "kisi", "city", "area", "side", "aur", "ya", "ke", "ki", "ka",
    }
    m = re.search(
        r"\b([a-z]{3,})\s+(nagar|colony|vihar|enclave|park|town|puram|ganj|"
        r"sector|road|marg|chowk|coloney)\b",
        lowered,
    )
    if m and m.group(1) not in stop:
        return f"{m.group(1).title()} {m.group(2).title()}"
    m = re.search(r"\b([a-z]{3,})\s+side\b", lowered)
    if m and m.group(1) not in stop:
        return m.group(1).title()
    return None


def _months(n: int) -> str:
    """'1 month' vs '2 months' (avoids the '1 months' grammar wart)."""
    return f"{n} month" if n == 1 else f"{n} months"


def _find_timeline(text: str) -> str | None:
    m = re.search(r"\b(?:within|in|around|about)\s+(\d+)\s*(?:months?|mahino|mahine)\b", text, re.IGNORECASE)
    if m:
        return _months(int(m.group(1)))
    m = re.search(r"\b(\d+)\s*(?:months?|mahino|mahine)\b", text, re.IGNORECASE)
    if m:
        return _months(int(m.group(1)))
    m = re.search(r"\b(?:jaldi|soon|asap|immediately)|\b(?:immediate|early)\b", text, re.IGNORECASE)
    if m:
        return "immediate"
    if "explor" in text.lower():
        return "exploring"
    return None


def _find_property_type(text: str) -> str | None:
    """Extract a property type: '1bhk'..'4bhk', apartment/flat/villa, etc."""
    bhk = _find_bhk(text)
    if bhk:
        return bhk
    lowered = text.lower().strip()
    for keyword, label in (
        ("villa", "villa"),
        ("apartment", "apartment"),
        ("flat", "flat"),
    ):
        if keyword in lowered:
            return label
    return None


def _find_purpose(text: str) -> str | None:
    """Extract purchase purpose: self-use, investment, rent, etc (best-effort)."""
    lowered = text.lower().strip()
    if any(m in lowered for m in ("invest", "rent", "flip", "rehne ke liye", "rehna", "khud ke liye", "self", "ghar ke liye")):
        for keyword, label in (
            ("invest", "investment"),
            ("flip", "flip"),
            ("rent", "rent"),
            ("self", "self_use"),
            ("rehna", "self_use"),
            ("rehne ke liye", "self_use"),
            ("khud ke liye", "self_use"),
            ("ghar ke liye", "self_use"),
        ):
            if keyword in lowered:
                return label
    return None


def find_property(text: str, properties: list[str] | None = None) -> str | None:
    """Match the customer's utterance to one of the known project names.

    Substring matching over each property's name (also lower-cased keywords).
    Returns the full property string (including its descriptor) on first match,
    else None. Kept deterministic and free (no LLM).
    """
    if not properties:
        return None
    lowered = transliterate_hindi(text.lower()).strip()
    for prop in properties:
        # Match on the short project title (text before the first comma/dash).
        name = prop.split(",")[0].split("-")[0].strip().lower()
        if name and name in lowered:
            return prop
    return None


# ------------------------------------------------------------------ classify


def classify_intent(text: str, state: str | None = None) -> IntentResult:
    """Best-effort deterministic classification of a user utterance.

    Devanagari Hindi is transliterated to Roman first, so one rule set serves
    both scripts ("हां" and "haan" classify identically).
    """
    lowered = transliterate_hindi(text.lower()).strip()

    # --- explicit do-not-call / callback (highest priority) ---
    if any(m in lowered for m in DNC_MARKERS):
        return IntentResult(Intent.DO_NOT_CALL, Confidence.HIGH)
    if any(m in lowered for m in CALLBACK_MARKERS):
        return IntentResult(Intent.CALLBACK, Confidence.MEDIUM)

    # --- not interested ---
    if any(m in lowered for m in NEGATIVE_INTEREST):
        return IntentResult(Intent.NOT_INTERESTED, Confidence.HIGH)

    # --- requirement slots (capture ALL present in one utterance) ---
    slots: dict[str, str] = {}
    purpose = _find_purpose(lowered)
    bhk = _find_bhk(lowered)
    property_type = _find_property_type(lowered)
    budget = _find_budget(lowered)
    location = _find_location(lowered)
    timeline = _find_timeline(lowered)
    if bhk:
        slots["bhk"] = bhk
        slots["property_type"] = bhk
    elif property_type:
        slots["property_type"] = property_type
    if budget:
        slots["budget"] = budget
    if location:
        slots["location"] = location
    if timeline:
        slots["timeline"] = timeline
    if purpose:
        slots["purpose"] = purpose
    if bhk:
        return IntentResult(Intent.BHK, Confidence.HIGH, slots)
    if property_type:
        return IntentResult(Intent.BHK, Confidence.HIGH, slots)
    if budget:
        return IntentResult(Intent.BUDGET, Confidence.HIGH, slots)
    if location:
        return IntentResult(Intent.LOCATION, Confidence.HIGH, slots)
    if timeline:
        return IntentResult(Intent.TIMELINE, Confidence.HIGH, slots)
    if purpose:
        return IntentResult(Intent.PURPOSE, Confidence.HIGH, slots)

    # --- pure yes / no ---
    words = set(re.findall(r"[a-z']+", lowered))
    if words and words <= AFFIRMATIVE:
        return IntentResult(Intent.CONFIRMATION, Confidence.MEDIUM)
    if words and words <= NEGATIVE:
        return IntentResult(Intent.CONFIRMATION, Confidence.MEDIUM)

    # --- interest signals ---
    if any(m in lowered for m in POSITIVE_INTEREST):
        return IntentResult(Intent.INTERESTED, Confidence.MEDIUM)
    if any(m in lowered for m in NEGATIVE_INTEREST):
        return IntentResult(Intent.NOT_INTERESTED, Confidence.HIGH)

    # --- objection markers ---
    for marker in OBJECTION_MARKERS:
        if marker in lowered:
            return IntentResult(Intent.OBJECTION, Confidence.MEDIUM, {"reason": OBJECTION_MARKERS[marker]})

    # --- greeting / confirmation ---
    if re.search(r"\b(hello|hii|hi|namaste|namaskar)\b", lowered):
        return IntentResult(Intent.GREETING, Confidence.LOW)

    # --- low-confidence / unknown ---
    return IntentResult(Intent.OTHER, Confidence.LOW)


def needs_llm(result: IntentResult) -> bool:
    """True when the deterministic classifier was not confident enough."""
    return result.confidence < Confidence.MEDIUM or result.intent in (Intent.OTHER, Intent.CLARIFICATION)


# Bare smalltalk that carries no information ("hello", "achha", "ji").
# Routing these to the LLM wastes 1-3s per turn for zero benefit: the engine
# already re-asks the pending question deterministically and free.
_BARE_SMALLTALK = frozenset({
    "hello", "hi", "hey", "achha", "acha", "ji", "namaste", "namaskar",
    "hello ji", "hi ji", "hey ji", "achha ji", "acha ji", "namaste ji",
    "haan ji", "yes ji", "ok ji", "ji haan", "ji han",
})


def is_bare_smalltalk(text: str) -> bool:
    """True for information-free greetings/acks (skip the LLM roundtrip)."""
    lowered = re.sub(r"\s+", " ", transliterate_hindi((text or "").lower()).strip())
    return bool(lowered) and lowered in _BARE_SMALLTALK


# Map canonical intent strings (also produced by the LLM JSON fallback) back
# to the Intent enum. Because `Intent` is a plain str subclass (any string is a
# valid instance), we must rely on an explicit allowlist rather than lookup.
_VALID_INTENT_VALUES = {
    "interested", "not_interested", "provide_property", "provide_budget",
    "provide_bhk", "provide_location", "provide_timeline", "objection",
    "confirm_visit", "provide_preferred_date", "request_callback", "do_not_call",
    "greeting", "confirmation", "clarification", "other",
}


def intent_from_str(value: str) -> Intent:
    """Resolve a string (rule or LLM output) to an Intent."""
    if value in _VALID_INTENT_VALUES:
        return Intent(value)
    return Intent.OTHER

