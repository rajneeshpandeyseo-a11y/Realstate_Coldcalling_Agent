"""Conversation engine orchestrator.

Pure logic (no I/O): given a current state, collected slots, and user input,
produces the next state and the agent's canned response. The LLM fallback is a
pluggable hook invoked only for low-confidence inputs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.conversation.intents import Intent, IntentResult, find_property, needs_llm
from app.conversation import responses as R
from app.conversation.states import ConvState, TERMINAL_STATES, AWAITS_INPUT


def _persona_property_names(persona: dict) -> list[str]:
    """Return the list of project display strings from the persona."""
    props = persona.get("properties")
    if isinstance(props, list):
        return [str(p) for p in props]
    if isinstance(props, dict):
        return list(props.keys())
    return []


def _looks_like_project(value: str, project_names: list[str] | None) -> bool:
    """True when a would-be location is actually a project name.

    Guards the LLM slot merge: an open-vocabulary model once put a project
    name ("Sunrise Heights") into `location`, corrupting a real value.
    Comparison is against project TITLES (text before the first comma) so
    real localities ("Jaipur", "Gopalpura") still pass.
    """
    v = (value or "").strip().lower()
    if not v:
        return False
    for prop in project_names or []:
        title = prop.split(",")[0].split("-")[0].strip().lower()
        if not title:
            continue
        if v == title or v in title or title in v:
            return True
    return False


def _display_properties(persona: dict) -> str:
    """Build a human Hinglish list of projects for the intro pitch."""
    props = persona.get("properties")
    if isinstance(props, dict):
        return ", ".join(f"{name} ({desc})" for name, desc in props.items())
    if isinstance(props, list):
        return ", ".join(str(p) for p in props)
    single = persona.get("project")
    if single:
        return str(single)
    return "hmare real estate projects"


def _property_description(prop: str, persona: dict | None = None) -> str:
    """Return a short descriptor for a matched property, best-effort."""
    if persona:
        props = persona.get("properties")
        if isinstance(props, dict):
            return str(props.get(prop, "details"))
    # Fall back to parsing the descriptor after the first comma/dash.
    base = prop.split(",")[0].split("-")[0].strip()
    return f"details of {base}"


def _match_inventory(slots: dict, persona: dict | None = None) -> list[str]:
    """Rank persona inventory against the customer's stated need.

    Scores each project on location, BHK and property-type overlap with the
    collected slots. Returns full property names with score > 0, best first.
    Pure/deterministic: no LLM, no I/O.
    """
    names = _persona_property_names(persona or {})
    if not names:
        return []
    loc = (slots.get("location") or "").strip().lower()
    bhk = (slots.get("bhk") or "").strip().lower()
    bhk_digit = bhk[0] if bhk and bhk[0].isdigit() else ""
    ptype = (slots.get("property_type") or "").strip().lower()
    props = (persona or {}).get("properties")
    scored: list[tuple[int, str]] = []
    for name in names:
        desc = str(props.get(name, "")) if isinstance(props, dict) else ""
        hay = f"{name} {desc}".lower()
        score = 0
        if loc and loc in hay:
            score += 2
        if bhk_digit and "bhk" in hay and bhk_digit in hay:
            score += 2
        if ptype in ("villa", "flat", "apartment") and ptype in hay:
            score += 1
        if score > 0:
            scored.append((score, name))
    scored.sort(key=lambda item: -item[0])
    return [name for _, name in scored]


def _need_complete_for_match(slots: dict) -> bool:
    """True when the customer stated enough need to judge inventory fit."""
    has_type = bool(slots.get("bhk") or slots.get("property_type"))
    return bool(slots.get("location") and has_type and slots.get("budget"))


def _display_name(caller_name: str | None) -> str:
    """Lead's first name for 'Rahul ji' style address, else ''."""
    if caller_name and caller_name.strip():
        return caller_name.strip().split()[0]
    return ""


def _render_permission(caller_name: str | None, persona: dict, reprompt: int = 0) -> str:
    """Availability-check reply, named when the lead name is known.

    reprompt>0 rotates alternate phrasings so a re-ask never repeats the
    exact same line (PHASE 1.4); 0 is the primary template.
    """
    first = _display_name(caller_name)
    agent_name = (persona or {}).get("agent_name", "Neha")
    company = (persona or {}).get("company", "our team")
    if first:
        return R.variant(
            "permission_ask_named", reprompt,
            first_name=first, agent_name=agent_name, company=company,
        )
    return R.variant(
        "permission_ask", reprompt, agent_name=agent_name, company=company
    )


def _is_yes(result: IntentResult, text: str) -> bool:
    """True when the customer agrees / accepts / shows interest."""
    return result.intent in (
        Intent.CONFIRMATION,
        Intent.CONFIRM_VISIT,
        Intent.INTERESTED,
    ) or _accepts_visit(text)


_BUSY_MARKERS = (
    "time nahi", "abhi nahi", "busy", "free nahi", "free nahin",
    "meeting", "drive kar",
)


def _sounds_busy(text: str) -> bool:
    """True when 'not interested' really means 'not now' (busy)."""
    from app.conversation.intents import transliterate_hindi

    lowered = transliterate_hindi((text or "").lower())
    return any(m in lowered for m in _BUSY_MARKERS)


# Markers that make an out-of-scope utterance a real QUESTION (deserves an
# answer) as opposed to garble/mumble (deserves a short re-ask, no LLM).
_QUESTION_MARKERS = (
    "kya", "kaun", "kitna", "kitne", "kab", "kahan", "kaha", "kaise",
    "kaunsi", "kaunsi", "kyon", "kyun", "kyu", "price", "rate", "company",
    "kaun", "matlab", "kahe",
)


def _looks_like_question(text: str) -> bool:
    """True when the customer asked something (vs mumbled/unclear audio)."""
    from app.conversation.intents import transliterate_hindi

    if "?" in (text or ""):
        return True
    lowered = transliterate_hindi((text or "").lower())
    return any(m in lowered for m in _QUESTION_MARKERS)


# PHASE 1.2: deterministic side-question grounding (zero LLM cost).
# Narrow markers on purpose: generic "aap kahan se ho" still routes to the
# LLM responder (covered by existing tests); only explicit company/price
# questions are answered deterministically here.
_COMPANY_Q_MARKERS = (
    "kaunsi company", "kaun si company", "which company", "company ka",
    "company se", "tum kaun", "aap kaun", "aapki company", "kaun bol",
)
_PRICE_Q_MARKERS = (
    "price", "rate", "cost", "kitne ka", "kitne ki", "kya price",
)


def _is_company_question(text: str) -> bool:
    from app.conversation.intents import transliterate_hindi

    lowered = transliterate_hindi((text or "").lower())
    return any(m in lowered for m in _COMPANY_Q_MARKERS)


def _is_price_question(text: str) -> bool:
    from app.conversation.intents import transliterate_hindi

    lowered = transliterate_hindi((text or "").lower())
    return any(m in lowered for m in _PRICE_Q_MARKERS)


def _grounded_side_answer(
    text: str, slots: dict, persona: dict, reprompt: int = 0
) -> str | None:
    """Grounded short answer + pending question for side-questions.

    Returns None when the utterance is not a company/price side-question.
    Deterministic (no LLM, no cost): company identity from persona, price
    quoted ONLY from the persona inventory, else WhatsApp fallback.
    """
    if not _is_company_question(text) and not _is_price_question(text):
        return None
    pending = _next_qualification_question(slots, reprompt)
    if _is_company_question(text):
        company = (persona or {}).get("company", "our team")
        agent_name = (persona or {}).get("agent_name", "Neha")
        return f"Ji, main {company} se {agent_name} bol rahi hoon. {pending}"
    # Price side-question: quote inventory desc verbatim, never invent.
    candidates = _match_inventory(slots, persona)
    props = (persona or {}).get("properties") or {}
    if candidates and isinstance(props, dict):
        top = candidates[0]
        desc = str(props.get(top, "")).strip()
        if desc:
            return f"Ji, {top} me {desc} hai. {pending}"
    if isinstance(props, dict) and props:
        first_name = next(iter(props.keys()))
        first_desc = str(props[first_name]).strip()
        if first_desc:
            return f"Ji, {first_name} me {first_desc} hai. {pending}"
    return (
        "Ji, exact price list main WhatsApp par share kar dungi. " + pending
    )


def _need_started(slots: dict) -> bool:
    """True when the customer already gave any requirement/project detail."""
    return any(slots.get(k) for k in ("bhk", "property_type", "location", "budget", "property"))


def _short_option(name: str) -> str:
    """One-line option summary: project + area only, no full detail.

    Keeps the PRESENT turn short (call-length control): full inventory
    detail is shared only after the customer shows interest in an option.
    """
    project = name.split(",")[0].strip()
    area = name.split(",", 1)[1].strip() if "," in name else ""
    return f"{project}, {area} mein" if area else project


def _render_present(slots: dict, persona: dict | None) -> tuple[str, list[str]]:
    """Render the PRESENT reply with up to 3 matched inventory options."""
    candidates = _match_inventory(slots, persona)[:3]
    if not candidates:
        return "", []
    options = "; ".join(_short_option(name) for name in candidates)
    return R.render(R.PRESENT_OPTIONS, options=options), candidates


def _display_visit(slots: dict) -> tuple[str, str]:
    """Human display of the booked visit date/time ('Sunday', '4 PM')."""
    import re as _re

    date = (slots.get("preferred_date") or "").strip()
    date_disp = date.title() if date and date.replace(" ", "").isalpha() else (date or "decided day")
    time = (slots.get("preferred_time") or "").strip()
    m = _re.match(r"^(\d{1,2}):(\d{2})$", time)
    if m:
        hour, minute = int(m.group(1)), m.group(2)
        suffix = "AM" if hour < 12 else "PM"
        h12 = hour % 12 or 12
        time_disp = f"{h12} {suffix}" if minute == "00" else f"{h12}:{minute} {suffix}"
    else:
        time_disp = time or "decided time"
    return date_disp, time_disp


# Hindi display names for weekdays (match _capture_booking vocab).
_HINDI_WEEKDAYS = {
    "monday": "Somwaar",
    "tuesday": "Mangalwaar",
    "wednesday": "Budhwaar",
    "thursday": "Guruwaar",
    "friday": "Shukrawaar",
    "saturday": "Shanivaar",
    "sunday": "Ravivaar",
}

_HINDI_DAY_TO_EN = {
    "somwaar": "monday",
    "mangalwaar": "tuesday",
    "budhwaar": "wednesday",
    "guruwaar": "thursday",
    "shukrawaar": "friday",
    "shanivaar": "saturday",
    "ravivaar": "sunday",
}


def _booking_day_en(slots: dict) -> str | None:
    """Normalised English weekday from the booked day (Hindi variants mapped)."""
    day = (slots.get("preferred_date") or "").strip().lower()
    if not day:
        return None
    return _HINDI_DAY_TO_EN.get(day, day)


def _upcoming_weekday_date(day_en: str | None, today=None):
    """Calendar date for the next `day_en` weekday (Asia/Kolkata).

    Same weekday as today means today. Returns None for non-weekdays
    ("today"/"tomorrow"/unknown), where the old ask-time path applies.
    """
    weekdays = [
        "monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday",
    ]
    if day_en not in weekdays:
        return None
    if today is None:
        from datetime import datetime

        try:
            from zoneinfo import ZoneInfo

            today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        except Exception:
            today = datetime.now().date()
    from datetime import timedelta

    return today + timedelta(days=(weekdays.index(day_en) - today.weekday()) % 7)


def _day_time_ask_text(slots: dict) -> str | None:
    """Consolidated day + time ask: weekday, actual date and time choices in
    ONE turn ('Toh Shanivaar, 12 September ko milte hain - 11 AM ya 4 PM?').

    Returns None when no computable weekday is booked (caller falls back to
    the plain time question).
    """
    day_en = _booking_day_en(slots)
    actual = _upcoming_weekday_date(day_en)
    if actual is None:
        return None
    weekday_hi = _HINDI_WEEKDAYS.get(day_en or "", (slots.get("preferred_date") or "").title())
    return R.render(
        R.VISIT_DAY_TIME_ASK,
        weekday_hi=weekday_hi,
        date=f"{actual.day} {actual.strftime('%B')}",
    )


def _is_no(text: str) -> bool:
    """True when the utterance is a bare disagreement ('nahi', 'no')."""
    import re as _re

    from app.conversation.intents import NEGATIVE, transliterate_hindi

    words = set(_re.findall(r"[a-z']+", transliterate_hindi((text or "").lower()).strip()))
    return bool(words) and words <= NEGATIVE


_LEADING_ACK_RE = None


def _short_qual_question(slots: dict) -> str | None:
    """Few-words re-ask of ONLY the pending slot (bare-'haan' follow-ups).

    A bare confirmation means "I'm listening", not an answer: repeating the
    whole question sounds like the agent didn't hear. Returns None when no
    qualification slot is pending (caller falls back to the full question).
    """
    if "bhk" not in slots and "property_type" not in slots:
        return R.QUALIFICATION_TYPE_SHORT
    if "location" not in slots:
        return R.QUALIFICATION_LOCATION_SHORT
    if "budget" not in slots:
        return R.QUALIFICATION_BUDGET_SHORT
    if slots.get("summary_ok") != "yes":
        return None
    if "purpose" not in slots:
        return R.QUALIFICATION_PURPOSE_SHORT
    if "timeline" not in slots:
        return R.QUALIFICATION_TIMELINE_SHORT
    return None


def _strip_leading_ack(text: str) -> str:
    """Drop leading acknowledgement openers ("Okay.", "Perfect.", ...).

    Used when the reply is ALREADY prefixed with an ack/nudge
    ("Ji, samajh gayi. Perfect. ...") so the caller hears one short
    acknowledgement instead of two stacked ones. Loops (max 3) to also
    collapse "Ji, samajh gayi. Perfect. ..." down to the bare question.
    """
    import re as _re

    global _LEADING_ACK_RE
    if _LEADING_ACK_RE is None:
        _LEADING_ACK_RE = _re.compile(
            r"^(?:okay|perfect|theek hai|samajh gayi|bahut badhiya|achha|acha|ji)[,.!]*\s+",
            _re.IGNORECASE,
        )
    out = (text or "").strip()
    for _ in range(3):
        reduced = _LEADING_ACK_RE.sub("", out).strip()
        if reduced == out or not reduced:
            break
        out = reduced
    return out


def _accepts_visit(text: str) -> bool:
    from app.conversation.intents import VISIT_ACCEPT_MARKERS, transliterate_hindi

    lowered = transliterate_hindi(text.lower()).strip()
    return any(m in lowered for m in VISIT_ACCEPT_MARKERS)


def _capture_booking(text: str) -> dict[str, str]:
    """Best-effort, deterministic booking-slot extraction (Phase 13).

    Pure logic (no I/O): extracts a preferred time-of-day and weekday from the
    customer's natural-language booking utterance. The raw text is preserved by
    the caller so nothing is lost. Kept here so the engine has no dependency on
    persistence layers.
    """
    import re

    from app.conversation.intents import transliterate_hindi

    lowered = transliterate_hindi(text.lower()).strip()
    time_val: str | None = None
    date_val: str | None = None

    m = re.search(r"(\d{1,2})[:.](\d{2})\s*(am|pm)?", lowered)
    if m:
        hour = int(m.group(1))
        minute = m.group(2)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        time_val = f"{hour:02d}:{minute}"
    else:
        # Bare hour ("4 PM", "11 AM", "4 baje") - common in visit replies.
        m2 = re.search(r"\b(\d{1,2})\s*(am|pm|baje|bajhe|baj)\b", lowered)
        if m2:
            hour = int(m2.group(1))
            ap = m2.group(2)
            if ap == "pm" and hour < 12:
                hour += 12
            elif ap == "am" and hour == 12:
                hour = 0
            time_val = f"{hour:02d}:00"
    if time_val is None:
        for period in ("morning", "subah", "savare"):
            if period in lowered:
                time_val = "morning"
                break
        if time_val is None:
            for period in ("afternoon", "dopahar", "do pahar"):
                if period in lowered:
                    time_val = "afternoon"
                    break
        if time_val is None:
            for period in ("evening", "shaam", "sham", "raat"):
                if period in lowered:
                    time_val = "evening"
                    break

    for day in (
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
        "sunday", "somwaar", "mangalwaar", "budhwaar", "guruwaar",
        "shukrawaar", "shanivaar", "ravivaar",
    ):
        if day in lowered:
            date_val = day
            break
    if date_val is None:
        if "parso" in lowered or "parson" in lowered:
            date_val = "day_after_tomorrow"
        elif "aaj" in lowered:
            date_val = "today"
        elif "kal" in lowered:
            date_val = "tomorrow"

    out: dict[str, str] = {}
    if time_val:
        out["preferred_time"] = time_val
    if date_val:
        out["preferred_date"] = date_val
    return out


@dataclass
class TurnResult:
    """Output of one conversation turn."""

    state: ConvState
    reply: str
    changed_state: bool = False
    intent: Intent | None = None
    input_confidence: float = 0.0
    used_llm: bool = False
    llm_cost: float = 0.0
    termination: bool = False
    slots: dict[str, str] = field(default_factory=dict)


class LLMFallback:
    """Abstract fallback: (text, state) -> IntentResult."""

    async def classify(self, text: str, state: str) -> IntentResult:
        raise NotImplementedError


# JSON prompt used for the LLM fallback. Kept minimal to reduce token cost.
_LLM_SYSTEM = (
    "You classify short Hinglish real-estate cold-call utterances. "
    "Reply with a single JSON object only. "
    'Fields: "intent" (one of: interested, not_interested, provide_property, '
    'provide_budget, provide_bhk, provide_location, provide_timeline, objection, '
    "confirm_visit, provide_preferred_date, request_callback, do_not_call, "
    "confirmation, clarification, other), "
    '"confidence" (0.0-1.0), and optional "slots" object with keys '
    "bhk, property_type, budget, location, timeline, purpose. "
    "Normalise slot values to Latin script: bhk like '2bhk', property_type one "
    "of 'villa'/'flat'/'apartment'/NXbhk, budget like '50 lakh' or '50-60 lakh', "
    "timeline like '2 months'/'immediate'/'exploring', purpose 'self_use' or "
    "'investment'. Never put a project/building name into location."
)


class ProviderLLMFallback(LLMFallback):
    """Fallback that delegates to the configured LLM provider (Phase 6).

    Invoked only for low-confidence/ambiguous inputs, keeping the deterministic
    rule path as the default and minimising LLM cost.
    """

    def __init__(self, provider=None):
        from app.providers import get_llm_provider

        self.provider = provider or get_llm_provider()

    async def classify(self, text: str, state: str) -> IntentResult:
        from app.conversation.intents import IntentResult, Confidence, intent_from_str

        # In `fixed` mode no external LLM is ever invoked: the rule engine is the
        # only classifier, guaranteeing zero LLM cost even for ambiguous input.
        if self.provider.name() == "fixed":
            return IntentResult(Intent.OTHER, Confidence.LOW)

        prompt = f'state={state} text={text or ""}'
        try:
            llm_result = await self.provider.complete(
                prompt,
                system=_LLM_SYSTEM,
                max_tokens=120,
                temperature=0.0,
            )
        except Exception as exc:  # pragma: no cover - defensive
            from app.logging_config import get_logger

            get_logger("app.conversation.engine").warning(
                "llm fallback failed, using OTHER", extra={"ctx": {"error": str(exc)}}
            )
            return IntentResult(Intent.OTHER, Confidence.LOW)

        parsed = _parse_llm_json(llm_result.text)
        if parsed is None:
            return IntentResult(Intent.OTHER, Confidence.LOW)
        intent = intent_from_str(parsed.get("intent", ""))
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        slots = {
            k: str(v)
            for k, v in (parsed.get("slots") or {}).items()
            if k in {"bhk", "property_type", "budget", "location", "timeline", "purpose"}
        }
        llm_cost = 0.0
        if llm_result.cost is not None:
            try:
                llm_cost = float(llm_result.cost.amount)
            except (TypeError, ValueError):
                llm_cost = 0.0
        return IntentResult(
            intent,
            max(confidence, Confidence.LOW),
            slots,
            cost=llm_cost,
        )


@dataclass
class AnswerResult:
    """A grounded natural-language answer from the LLM responder."""

    text: str
    cost: float = 0.0


class LLMResponder:
    """Abstract responder: answer an unexpected customer utterance in words."""

    async def answer(
        self, text: str, state: str, persona: dict, pending_question: str
    ) -> AnswerResult | None:
        raise NotImplementedError


_RESPONDER_SYSTEM = (
    "You are Neha, a warm friendly female Hindi (Roman script) real-estate "
    "telesales agent chatting on a phone call - NOT a computer, never say "
    "you are an AI/model, never open with sorry/excuse. Always speak "
    "feminine ('bol rahi hoon'), colloquial Hinglish with softeners like "
    "'Ji', 'bilkul', 'koi baat nahi'. "
    "Answer the customer's side-question in 1-2 SHORT warm Hinglish lines "
    "(max 40 words total), grounded ONLY in the Company/Projects given. "
    "Company identity question -> answer with Company + agent name. "
    "Price question -> quote ONLY the price written in Projects; if not listed, "
    "say exact price list WhatsApp par share kar dungi. "
    "Never invent prices, availability, or project details. "
    "Always end by asking the Follow-up question exactly as given, so the call "
    "returns to the pending requirement. "
    "If you cannot answer, say details WhatsApp par share kar dungi and ask the "
    "follow-up question."
)


class ProviderLLMResponder(LLMResponder):
    """LLM-backed responder (Gemini) for out-of-scope customer questions.

    Invoked ONLY when deterministic rules yield OTHER, so normal turns stay
    free. The reply is grounded in the persona (company + real projects) and
    always steers back to the pending question, so the call never stalls and
    the caller gets a real answer instead of a canned sorry.
    """

    def __init__(self, provider=None):
        from app.providers import get_llm_provider

        self.provider = provider or get_llm_provider()

    async def answer(
        self, text: str, state: str, persona: dict, pending_question: str
    ) -> AnswerResult | None:
        if self.provider.name() == "fixed":
            return None
        company = (persona or {}).get("company", "our team")
        agent_name = (persona or {}).get("agent_name", "Neha")
        projects = _display_properties(persona or {})
        prompt = (
            f"Company: {company}. Agent: {agent_name}. Projects: {projects}. "
            f"Stage: {state}. Customer said: {text!r}. "
            f"Task: Answer briefly in Hinglish (feminine 'bol rahi hoon'), "
            f"grounded only in Company/Projects above, then ask this "
            f"follow-up question exactly: {pending_question!r}"
        )
        try:
            res = await self.provider.complete(
                prompt,
                system=_RESPONDER_SYSTEM,
                max_tokens=90,
                temperature=0.2,
            )
        except Exception:
            return None
        reply = (getattr(res, "text", "") or "").strip().strip('"').strip()
        if not reply:
            return None
        # Guard the side-question pattern: the caller must always hear the
        # pending requirement question again, else the call stalls. If the
        # model forgot it, append verbatim (cheap, deterministic).
        pending = (pending_question or "").strip()
        if pending and pending not in reply:
            # Cap runaway answers so TTS stays short; pending Q is never cut.
            if len(reply) > 400:
                reply = reply[:400].rstrip()
            reply = f"{reply} {pending}"
        cost = 0.0
        if getattr(res, "cost", None) is not None:
            try:
                cost = float(res.cost.amount)
            except (TypeError, ValueError):
                cost = 0.0
        return AnswerResult(reply, cost)


def _parse_llm_json(text: str) -> dict | None:
    """Robustly extract a JSON object from an LLM reply."""
    if not text:
        return None
    # Strip markdown fences if the model wrapped the reply.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].lstrip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        value = json.loads(cleaned[start : end + 1])
        return value if isinstance(value, dict) else None
    except (json.JSONDecodeError, ValueError):
        from app.logging_config import get_logger

        get_logger("app.conversation.engine").warning(
            "could not parse LLM JSON fallback output", extra={"ctx": {"text": text[:200]}}
        )
        return None


# Next requirement question in the fixed example-transcript order:
# property type/BHK -> location -> budget -> (summary confirm) -> purpose -> timeline.
def _next_qualification_question(slots: dict, reprompt: int = 0) -> str:
    """Step through requirement slots deterministically in a fixed order.

    Order: property type/BHK -> location -> budget -> purpose -> timeline.
    (The type+location+budget recap is a SUMMARY_CONFIRM state transition and
    inventory matching/presentation is the PRESENT state.)
    reprompt>0 rotates alternate phrasings (PHASE 1.4); 0 is primary.
    """
    if "bhk" not in slots and "property_type" not in slots:
        return R.variant("qualification_type", reprompt)
    if "location" not in slots:
        return R.variant("qualification_location", reprompt)
    if "budget" not in slots:
        return R.variant("qualification_budget", reprompt)
    if "purpose" not in slots:
        return R.variant("qualification_purpose", reprompt)
    if "timeline" not in slots:
        return R.variant("qualification_timeline", reprompt)
    return R.VISIT_OFFER  # all slots present; caller moves to PRESENT


# Consecutive no-progress turns tolerated before the stuck-loop guard ends
# the call gracefully (see ConversationEngine.step). Three strikes: a real
# agent asks ~twice, then wraps up instead of nagging forever.
MAX_STUCK_TURNS = 3


class ConversationEngine:
    """Stateless driver: (state, slots, input) -> (state, reply)."""
    def __init__(
        self,
        llm: LLMFallback | None = None,
        responder: LLMResponder | None = None,
    ):
        self.llm = llm
        self.responder = responder

    async def _other_reply(
        self, text: str, state: str, pending_question: str, persona: dict,
        reprompt: int = 0,
    ) -> tuple[str, bool, float]:
        """Answer an unexpected customer utterance.

        Tries the LLM responder first (a real answer grounded in our projects
        that steers back to the pending question); falls back to the canned
        sorry + redirect when no responder is wired or it fails. Returns
        (reply, used_llm, llm_cost). The canned fallback rotates phrasing
        with reprompt so repeats never sound identical (PHASE 1.4).
        """
        if not _looks_like_question(text):
            # Mumble/garble, not a question: one short warm re-ask, no LLM
            # roundtrip, no full "repeat everything" demand.
            return R.UNCLEAR_REASK + " " + pending_question, False, 0.0
        if self.responder is not None and (text or "").strip():
            try:
                ans = await self.responder.answer(text, state, persona or {}, pending_question)
                if ans is not None and ans.text.strip():
                    return ans.text.strip(), True, ans.cost
            except Exception:
                pass
        return R.variant("fallback_out_of_scope", reprompt) + " " + pending_question, False, 0.0

    async def _detect(
        self,
        text: str,
        state: str,
        project_names: list[str] | None = None,
    ) -> IntentResult:
        from app.conversation.intents import classify_intent, is_bare_smalltalk

        result = classify_intent(text, state)
        if needs_llm(result) and self.llm is not None and not is_bare_smalltalk(text):
            try:
                llm_result = await self.llm.classify(text, state)
                if llm_result and llm_result.confidence >= 0.7:
                    from app.conversation.intents import transliterate_hindi

                    llm_result.used_llm = True
                    llm_result.cost = getattr(llm_result, "cost", 0.0) or 0.0
                    # Rules own every slot they filled. The LLM may fill the
                    # REST (matters for Devanagari input the rules transliterate
                    # imperfectly) - except `location`, which stays rule-owned:
                    # open-vocabulary guesses once corrupted a real value (a
                    # project name became a location). LLM values are
                    # transliterated so both scripts normalise identically.
                    merged = dict(result.slots)
                    for k, v in (llm_result.slots or {}).items():
                        if k in merged or not v:
                            continue
                        if k == "location":
                            # Refined trust (slot-skipping fix): a location the
                            # rules missed (e.g. a bare unknown locality like
                            # "Gopalpura") is accepted from the LLM - unless it
                            # looks like a project name, which keeps the
                            # documented corruption guard intact.
                            loc = transliterate_hindi(str(v)).strip()
                            if len(loc) >= 3 and not _looks_like_project(
                                loc, project_names
                            ):
                                merged[k] = loc
                            continue
                        if k in {"bhk", "property_type", "budget", "timeline", "purpose"}:
                            merged[k] = transliterate_hindi(str(v)).strip()
                    llm_result.slots = merged
                    return llm_result
            except Exception:
                pass
        return result

    async def step(
        self,
        *,
        state: ConvState,
        slots: dict[str, str] | None = None,
        user_input: str | None = None,
        first_turn: bool = False,
        caller_name: str | None = None,
        persona: dict | None = None,
        reprompt: int = 0,
    ) -> TurnResult:
        """Compute the next state and reply for one turn.

        reprompt counts how many times the current question was already
        asked (PHASE 1.4): 0 renders the primary phrasing (backward
        compatible), higher values rotate 2-3 alternates so a repeated
        question never repeats the exact same line. Forward moves to a NEW
        question always use the primary phrasing.

        Stuck-loop guard: consecutive turns that add no information and
        change nothing increment a `_stuck` counter in slots; after
        MAX_STUCK_TURNS the call ends with a graceful WhatsApp exit
        instead of looping forever (a real agent wraps up, never nags).
        """
        slots = dict(slots or {})
        before = {k: v for k, v in slots.items() if not k.startswith("_")}
        try:
            prev_stuck = int(slots.get("_stuck", 0) or 0)
        except (TypeError, ValueError):
            prev_stuck = 0
        turn = await self._step_inner(
            state=state,
            slots=slots,
            user_input=user_input,
            first_turn=first_turn,
            caller_name=caller_name,
            persona=persona,
            reprompt=reprompt,
        )
        after = turn.slots if isinstance(turn.slots, dict) else slots
        if turn.termination or turn.changed_state:
            if isinstance(after, dict):
                after.pop("_stuck", None)
            return turn
        keys = {
            k for k in list(before) + list(after or {})
            if not k.startswith("_")
        }
        progressed = any(before.get(k) != (after or {}).get(k) for k in keys)
        if progressed:
            if isinstance(after, dict):
                after.pop("_stuck", None)
            return turn
        streak = prev_stuck + 1
        if isinstance(after, dict):
            after["_stuck"] = streak
            turn.slots = after
        if streak >= MAX_STUCK_TURNS:
            if isinstance(after, dict):
                after.pop("_stuck", None)
            return TurnResult(
                state=ConvState.END,
                reply=R.STUCK_EXIT,
                changed_state=True,
                intent=turn.intent,
                input_confidence=turn.input_confidence,
                termination=True,
                slots=after if isinstance(after, dict) else {},
                used_llm=turn.used_llm,
                llm_cost=turn.llm_cost,
            )
        return turn

    async def _step_inner(
        self,
        *,
        state: ConvState,
        slots: dict[str, str] | None = None,
        user_input: str | None = None,
        first_turn: bool = False,
        caller_name: str | None = None,
        persona: dict | None = None,
        reprompt: int = 0,
    ) -> TurnResult:
        """Single-turn state machine (see step() for the public contract)."""
        slots = dict(slots or {})
        persona = persona or {}
        try:
            reprompt = max(0, int(reprompt or 0))
        except (TypeError, ValueError):
            reprompt = 0

        # ---- first turn: agent greets and asks identity ----
        if first_turn:
            company = persona.get("company", "our team")
            first = (caller_name or "").split()[0] if caller_name else ""
            if first:
                reply = R.render(R.GREETING, company=company, first_name=first)
            else:
                reply = R.render(R.GREETING_UNNAMED, company=company)
            return TurnResult(
                state=ConvState.GREETING,
                reply=reply,
                changed_state=True,
            )

        if state in (ConvState.GREETING,):
            # Identity confirmation done - move to the availability check.
            # Our projects are NEVER pitched here; the customer speaks first.
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            # Preserve any requirement given right after the greeting (e.g.
            # "I need a 2 BHK flat") so it survives into qualification.
            for k, v in (result.slots or {}).items():
                slots[k] = v
            matched = find_property(user_input or "", _persona_property_names(persona))
            if matched is not None:
                slots["property"] = matched
            return TurnResult(
                state=ConvState.PERMISSION,
                reply=_render_permission(caller_name, persona),
                changed_state=True,
                intent=result.intent,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.PERMISSION:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                # "abhi time nahi / busy" at the permission check means "not
                # now", not "never" - offer a callback instead of ending.
                if _sounds_busy(user_input or ""):
                    return TurnResult(
                        state=ConvState.CALLBACK,
                        reply=R.CALLBACK_ASK,
                        changed_state=True,
                        intent=result.intent,
                        input_confidence=result.confidence,
                        slots=slots,
                        used_llm=result.used_llm,
                        llm_cost=result.cost,
                    )
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Preserve anything the customer volunteered with their answer.
            for k, v in (result.slots or {}).items():
                slots[k] = v
            matched = slots.get("property") or find_property(
                user_input or "", _persona_property_names(persona)
            )
            if matched is not None:
                slots["property"] = matched
            # Already engaged (gave a requirement / named a project): skip the
            # permission re-ask and continue the ordered flow (this also
            # routes already-complete needs to SUMMARY_CONFIRM, never past it).
            if _need_started(slots):
                turn = self._advance_qualification(slots, result, persona)
                turn.changed_state = True
                return turn
            if _is_yes(result, user_input or ""):
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=R.QUALIFICATION_TYPE,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Unclear answer: try a real grounded answer first, else re-ask
            # permission with the canned redirect. Never stalls.
            pending = _render_permission(caller_name, persona, reprompt)
            reply, used_llm, llm_cost = await self._other_reply(
                user_input or "", state.value, pending, persona, reprompt
            )
            return TurnResult(
                state=ConvState.PERMISSION,
                reply=reply,
                changed_state=False,
                intent=Intent.OTHER,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=used_llm,
                llm_cost=llm_cost,
            )

        if state == ConvState.INTRO:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            # Preserve any requirement the customer already gave on this first
            # answer (e.g. "I need a 2 BHK flat") so it is not lost while we
            # still ask which specific project they are interested in.
            for k, v in (result.slots or {}).items():
                slots[k] = v
            # If the customer already named the project (here or during the
            # greeting), go straight to qualification instead of re-asking.
            matched = slots.get("property") or find_property(user_input or "", _persona_property_names(persona))
            if matched is not None:
                slots["property"] = matched
                desc = _property_description(matched, persona)
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=R.render(R.PROPERTY_CONFIRM, property=matched, property_desc=desc)
                    + " "
                    + _next_qualification_question(slots),
                    changed_state=True,
                    intent=Intent.PROVIDE_PROPERTY,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            return TurnResult(
                state=ConvState.PROPERTY_SELECTION,
                reply=R.PROPERTY_ASK,
                changed_state=True,
                intent=result.intent,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.PROPERTY_SELECTION:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            matched = find_property(user_input or "", _persona_property_names(persona))
            if matched is not None:
                slots["property"] = matched
                # Keep any BHK/property-type detail the customer gave alongside
                # the project choice (e.g. "3 BHK Sunrise Heights").
                for k, v in (result.slots or {}).items():
                    slots[k] = v
                desc = _property_description(matched, persona)
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=R.render(R.PROPERTY_CONFIRM, property=matched, property_desc=desc)
                    + " "
                    + _next_qualification_question(slots),
                    changed_state=True,
                    intent=Intent.PROVIDE_PROPERTY,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Unexpected / out-of-scope answer (e.g. a property-type/BHK detail
            # instead of a project name): preserve any requirement captured and
            # acknowledge + redirect back to picking a project (single ack).
            for k, v in (result.slots or {}).items():
                slots[k] = v
            return TurnResult(
                state=ConvState.PROPERTY_SELECTION,
                reply=R.FALLBACK_OUT_OF_SCOPE + " " + _strip_leading_ack(R.PROPERTY_ASK),
                changed_state=False,
                intent=Intent.OTHER,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.NEED_FINDING:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            # Preserve any requirement the customer already gave (e.g. "3 bhk").
            for k, v in (result.slots or {}).items():
                slots[k] = v
            names = _persona_property_names(persona)
            # 1) Explicit project choice (here or carried from greeting) wins.
            direct = slots.get("property") or find_property(user_input or "", names)
            if direct is not None:
                slots["property"] = direct
                desc = _property_description(direct, persona)
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=R.render(R.PROPERTY_CONFIRM, property=direct, property_desc=desc)
                    + " "
                    + _next_qualification_question(slots),
                    changed_state=True,
                    intent=Intent.PROVIDE_PROPERTY,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # 2) Match the stated need against our inventory.
            candidates = _match_inventory(slots, persona)
            if len(candidates) == 1:
                slots["property"] = candidates[0]
                desc = _property_description(candidates[0], persona)
                reply = (
                    R.render(R.MATCH_FOUND, property=candidates[0], property_desc=desc)
                    + " "
                    + _next_qualification_question(slots)
                )
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=reply,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            if len(candidates) > 1:
                options = ", ".join(candidates)
                return TurnResult(
                    state=ConvState.PROPERTY_SELECTION,
                    reply=R.render(R.MATCH_MULTIPLE, options=options) + " " + _strip_leading_ack(R.PROPERTY_ASK),
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # 3) No match yet: complete need with no fit -> callback offer;
            # otherwise keep asking the next need question.
            if _need_complete_for_match(slots):
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.NO_MATCH_OFFER + " " + R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            return TurnResult(
                state=ConvState.NEED_FINDING,
                reply=_next_qualification_question(slots),
                changed_state=False,
                intent=result.intent,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.QUALIFICATION:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                # PHASE 1.6: "baad mein"/"busy hoon" mid-qualification means
                # "not now" - route to callback like every other state does
                # (previously fell through and re-asked the requirement).
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # A project name may arrive on any turn ("3 BHK Sunrise Heights")
            # - capture it even when the intent itself is out-of-scope.
            direct = find_property(user_input or "", _persona_property_names(persona))
            if direct is not None:
                slots["property"] = direct
            # PHASE 1.2: deterministic side-question grounding (zero LLM cost).
            # "aap kaunsi company se ho" / "price kitna hai" with no new slots
            # gets a short grounded answer + pending question, even when the
            # rule classifier labels it INTERESTED instead of OTHER.
            if not result.slots and direct is None:
                side = _grounded_side_answer(user_input or "", slots, persona, reprompt)
                if side is not None:
                    return TurnResult(
                        state=ConvState.QUALIFICATION,
                        reply=side,
                        changed_state=False,
                        intent=result.intent,
                        input_confidence=result.confidence,
                        slots=slots,
                        used_llm=False,
                        llm_cost=0.0,
                    )
            if result.intent == Intent.OTHER and direct is None:
                # Unexpected / out-of-scope question: answer it in words first
                # (LLM responder), else acknowledge + re-ask the requirement.
                question = _next_qualification_question(slots, reprompt)
                reply, used_llm, llm_cost = await self._other_reply(
                    user_input or "", state.value, question, persona, reprompt
                )
                return TurnResult(
                    state=ConvState.QUALIFICATION,
                    reply=reply,
                    changed_state=False,
                    intent=Intent.OTHER,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=used_llm,
                    llm_cost=llm_cost,
                )
            # merge newly parsed slots
            for k, v in result.slots.items():
                slots[k] = v
            turn = self._advance_qualification(slots, result, persona)
            if (
                result.intent == Intent.CONFIRMATION
                and not result.slots
                and turn.state == ConvState.QUALIFICATION
                and not turn.changed_state
            ):
                # Bare "haan/yes" with no detail means "I'm listening", not an
                # answer: acknowledge warmly, then ask ONLY the pending slot
                # in a few words (never the whole question again).
                short = _short_qual_question(slots)
                turn.reply = R.variant("yes_nudge", reprompt) + " " + (
                    short if short is not None else _strip_leading_ack(turn.reply)
                )
            return turn

        if state == ConvState.SUMMARY_CONFIRM:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            before = {k: slots.get(k) for k in ("bhk", "property_type", "location", "budget")}
            had_purpose, had_timeline = "purpose" in slots, "timeline" in slots
            for k, v in (result.slots or {}).items():
                slots[k] = v
            corrected = any(slots.get(k) != before.get(k) for k in before)
            if corrected:
                # Customer corrected a detail ("nahi, 3 BHK") - re-recap.
                return TurnResult(
                    state=ConvState.SUMMARY_CONFIRM,
                    reply=R.variant("need_summary", reprompt, need=R.build_need_summary(slots)),
                    changed_state=False,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            progressed = ("purpose" in slots and not had_purpose) or (
                "timeline" in slots and not had_timeline
            )
            if _is_yes(result, user_input or "") or progressed:
                # Explicit "yes" - or the customer jumped ahead by answering a
                # later question (e.g. gave the timeline): implicit confirm.
                # Summary accepted - summary_ok marks the recap done so the
                # flow never asks it again (ignored by CRM persistence).
                slots["summary_ok"] = "yes"
                return self._advance_qualification(slots, result, persona)
            if result.intent == Intent.OTHER:
                return TurnResult(
                    state=ConvState.SUMMARY_CONFIRM,
                    reply=R.variant("need_summary", reprompt, need=R.build_need_summary(slots)),
                    changed_state=False,
                    intent=Intent.OTHER,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Disagreement without a correction ("nahi", "galat hai"): ask what
            # to fix instead of looping the same recap.
            return TurnResult(
                state=ConvState.SUMMARY_CONFIRM,
                reply=(
                    R.variant("need_summary", reprompt, need=R.build_need_summary(slots))
                    + " Bataiye, type, location ya budget mein kya correct karna hai?"
                ),
                changed_state=False,
                intent=result.intent,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.PRESENT:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            for k, v in (result.slots or {}).items():
                slots[k] = v
            # Customer picked one of the presented options by name.
            picked = find_property(user_input or "", _persona_property_names(persona))
            if picked is not None:
                slots["property"] = picked
                return TurnResult(
                    state=ConvState.CLOSING,
                    reply=R.VISIT_OFFER,
                    changed_state=True,
                    intent=Intent.PROVIDE_PROPERTY,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            reply, candidates = _render_present(slots, persona)
            if not candidates:
                # Complete need matches nothing in inventory -> callback offer.
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.NO_MATCH_OFFER + " " + R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            if result.intent == Intent.OTHER:
                grounded, used_llm, llm_cost = await self._other_reply(
                    user_input or "", state.value, reply, persona, reprompt
                )
                return TurnResult(
                    state=ConvState.PRESENT,
                    reply=grounded,
                    changed_state=False,
                    intent=Intent.OTHER,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=used_llm,
                    llm_cost=llm_cost,
                )
            if _is_yes(result, user_input or ""):
                # Customer likes an option -> pin the top match as the property
                # (downstream visit/CRM location) and offer the site visit.
                if not slots.get("property"):
                    top = _match_inventory(slots, persona)
                    if top:
                        slots["property"] = top[0]
                return TurnResult(
                    state=ConvState.CLOSING,
                    reply=R.VISIT_OFFER,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Hesitation / objection: acknowledge, keep the options on table
            # (the ack opener of the options line is stripped - one ack only).
            return TurnResult(
                state=ConvState.PRESENT,
                reply=R.variant("objection_generic", reprompt) + " " + _strip_leading_ack(reply),
                changed_state=False,
                intent=result.intent,
                input_confidence=result.confidence,
                slots=slots,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        if state == ConvState.CLOSING:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # A weekday answer ("Sunday") locks the day with one actual-date
            # confirmation; a complete "Sunday 4 PM" books immediately.
            # Booking info wins over the utterance intent (a day/time IS the
            # answer); otherwise out-of-scope questions get a grounded reply.
            _closing_booking = _capture_booking(user_input or "")
            if result.intent == Intent.OTHER and not _closing_booking:
                # Unexpected / out-of-scope question during closing: answer it
                # in words first, else acknowledge + steer back to the offer.
                grounded, used_llm, llm_cost = await self._other_reply(
                    user_input or "", state.value, R.VISIT_OFFER, persona, reprompt
                )
                return TurnResult(
                    state=ConvState.CLOSING,
                    reply=grounded,
                    changed_state=False,
                    intent=Intent.OTHER,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=used_llm,
                    llm_cost=llm_cost,
                )
            if result.intent == Intent.OBJECTION and not _closing_booking:
                # Price/trust objection during closing - acknowledge, stay.
                return TurnResult(
                    state=ConvState.CLOSING,
                    reply=R.variant("objection_generic", reprompt),
                    changed_state=False,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            return self._visit_progress(
                slots, result, user_input or "", reprompt, caller_name,
                from_closing=True,
            )

        if state == ConvState.VISIT_BOOKING:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            if result.intent == Intent.NOT_INTERESTED:
                return self._terminate(ConvState.NOT_INTERESTED, R.NOT_INTERESTED_SCRIPT, result)
            if result.intent == Intent.CALLBACK:
                return TurnResult(
                    state=ConvState.CALLBACK,
                    reply=R.CALLBACK_ASK,
                    changed_state=True,
                    intent=result.intent,
                    input_confidence=result.confidence,
                    slots=slots,
                    used_llm=result.used_llm,
                    llm_cost=result.cost,
                )
            # Day/time booking progression (single-confirm policy).
            return self._visit_progress(
                slots, result, user_input or "", reprompt, caller_name,
                from_closing=False,
            )

        if state == ConvState.CALLBACK:
            result = await self._detect(user_input or "", state.value, _persona_property_names(persona))
            if result.intent == Intent.DO_NOT_CALL:
                return self._terminate(ConvState.DO_NOT_CALL, R.DNC_CONFIRM, result)
            return TurnResult(
                state=ConvState.END,
                reply=R.render(R.CALLBACK_CONFIRM, when=user_input or "aapke bataye hue time"),
                changed_state=True,
                termination=True,
                intent=result.intent,
                input_confidence=result.confidence,
                used_llm=result.used_llm,
                llm_cost=result.cost,
            )

        # default / safety - generic fallback
        return TurnResult(
            state=state,
            reply=R.FALLBACK_OUT_OF_SCOPE,
            changed_state=False,
            intent=Intent.OTHER,
            input_confidence=0.0,
        )

    def _advance_qualification(
        self, slots: dict, result: IntentResult, persona: dict
    ) -> TurnResult:
        """Move to the next step of the ordered qualification flow.

        Order: type -> location -> budget -> SUMMARY_CONFIRM recap ->
        purpose -> timeline -> PRESENT (inventory match + options).
        """
        base = dict(
            intent=result.intent,
            input_confidence=result.confidence,
            slots=slots,
            used_llm=result.used_llm,
            llm_cost=result.cost,
        )
        if "bhk" not in slots and "property_type" not in slots:
            return TurnResult(state=ConvState.QUALIFICATION, reply=R.QUALIFICATION_TYPE, **base)
        if "location" not in slots:
            return TurnResult(state=ConvState.QUALIFICATION, reply=R.QUALIFICATION_LOCATION, **base)
        if "budget" not in slots:
            return TurnResult(state=ConvState.QUALIFICATION, reply=R.QUALIFICATION_BUDGET, **base)
        if slots.get("summary_ok") != "yes":
            return TurnResult(
                state=ConvState.SUMMARY_CONFIRM,
                reply=R.render(R.NEED_SUMMARY, need=R.build_need_summary(slots)),
                changed_state=True,
                **base,
            )
        if "purpose" not in slots:
            return TurnResult(state=ConvState.QUALIFICATION, reply=R.QUALIFICATION_PURPOSE, **base)
        if "timeline" not in slots:
            return TurnResult(state=ConvState.QUALIFICATION, reply=R.QUALIFICATION_TIMELINE, **base)
        reply, candidates = _render_present(slots, persona)
        if not candidates:
            return TurnResult(
                state=ConvState.CALLBACK,
                reply=R.NO_MATCH_OFFER + " " + R.CALLBACK_ASK,
                changed_state=True,
                **base,
            )
        return TurnResult(
            state=ConvState.PRESENT, reply=reply, changed_state=True, **base
        )

    def _visit_progress(
        self,
        slots: dict,
        result: IntentResult,
        user_input: str,
        reprompt: int,
        caller_name: str | None,
        from_closing: bool,
    ) -> TurnResult:
        """Shared day/time booking progression (CLOSING -> VISIT_BOOKING).

        Consolidated policy: a newly named day gets ONE combined day + time
        ask ("Shanivaar, 12 September ko milte hain - 11 AM ya 4 PM?"), so
        booking takes one round instead of three. Later repeats fall back
        to a short nudge, then a decisive proposal - never rotating full
        questions.
        """
        base = dict(
            intent=result.intent,
            input_confidence=result.confidence,
            slots=slots,
            used_llm=result.used_llm,
            llm_cost=result.cost,
        )
        had_date = slots.get("preferred_date")
        booking = _capture_booking(user_input or "")
        new_day = bool(booking.get("preferred_date")) and booking["preferred_date"] != had_date
        for k, v in booking.items():
            if v:
                slots[k] = v
        # Legacy hygiene: one-shot confirm flag from the older flow.
        slots.pop("date_confirm_pending", None)
        if slots.get("preferred_date") and slots.get("preferred_time"):
            slots.pop("time_proposed", None)
            slots.pop("time_propose_offered", None)
            slots.pop("time_propose_declined", None)
            return self._book_visit_turn(slots, result, caller_name)
        if new_day:
            combined = _day_time_ask_text(slots)
            if combined is not None:
                return TurnResult(
                    state=ConvState.VISIT_BOOKING,
                    reply=combined,
                    changed_state=from_closing,
                    **base,
                )
            # Non-weekday (today/tomorrow/...) - ask the time straight away.
            date_disp, _ = _display_visit(slots)
            return TurnResult(
                state=ConvState.VISIT_BOOKING,
                reply=R.render(R.VISIT_TIME_ASK, date=date_disp),
                changed_state=from_closing,
                **base,
            )
        if slots.get("preferred_date"):
            # Day locked but time still missing.
            if slots.get("time_proposed"):
                # Outstanding proposal: anything but "no" books the proposed
                # time (a real day/time in the same breath overrides first,
                # handled by the branches above).
                if _is_no(user_input or ""):
                    slots.pop("time_proposed", None)
                    slots["time_propose_declined"] = "yes"
                    date_disp, _ = _display_visit(slots)
                    return TurnResult(
                        state=ConvState.VISIT_BOOKING,
                        reply=R.render(R.VISIT_TIME_NUDGE, date=date_disp),
                        changed_state=from_closing,
                        **base,
                    )
                slots["preferred_time"] = slots.pop("time_proposed")
                slots.pop("time_propose_offered", None)
                slots.pop("time_propose_declined", None)
                return self._book_visit_turn(slots, result, caller_name)
            if _is_no(user_input or ""):
                # Rejects the proposed day - ask the day again.
                return TurnResult(
                    state=ConvState.VISIT_BOOKING,
                    reply=R.variant("visit_date_ask", reprompt),
                    changed_state=from_closing,
                    **base,
                )
            if (
                not new_day
                and reprompt >= 2
                and not slots.get("time_propose_offered")
                and not slots.get("time_propose_declined")
            ):
                # Asked twice with nothing usable back: stop open-asking and
                # propose the default morning slot like a human agent would.
                slots["time_proposed"] = "11:00"
                slots["time_propose_offered"] = "yes"
                date_disp, _ = _display_visit(slots)
                return TurnResult(
                    state=ConvState.VISIT_BOOKING,
                    reply=R.render(R.VISIT_TIME_PROPOSE, date=date_disp),
                    changed_state=from_closing,
                    **base,
                )
            # Day already locked, time still missing - one short nudge.
            date_disp, _ = _display_visit(slots)
            return TurnResult(
                state=ConvState.VISIT_BOOKING,
                reply=R.render(R.VISIT_TIME_NUDGE, date=date_disp),
                changed_state=from_closing,
                **base,
            )
        if from_closing and _is_yes(result, user_input or ""):
            # Accepts the visit but named no day - ask Saturday/Sunday.
            return TurnResult(
                state=ConvState.VISIT_BOOKING,
                reply=R.VISIT_DATE_ASK,
                changed_state=True,
                **base,
            )
        # No day yet (or only a time) - ask Saturday/Sunday.
        return TurnResult(
            state=ConvState.VISIT_BOOKING,
            reply=R.variant("visit_date_ask", reprompt),
            changed_state=from_closing,
            **base,
        )

    def _book_visit_turn(
        self, slots: dict, result: IntentResult, caller_name: str | None
    ) -> TurnResult:
        """Date + time are both locked - confirm the booking and end the call."""
        date_disp, time_disp = _display_visit(slots)
        first = _display_name(caller_name)
        reply = R.render(
            R.VISIT_BOOK_CONFIRM,
            first_name=first or "Ji",
            date=date_disp,
            time=time_disp,
        ).replace("Ji ji", "Ji")
        return TurnResult(
            state=ConvState.END,
            reply=reply,
            changed_state=True,
            intent=result.intent,
            input_confidence=result.confidence,
            termination=True,
            slots=slots,
            used_llm=result.used_llm,
            llm_cost=result.cost,
        )

    def _terminate(self, state: ConvState, reply: str, result: IntentResult) -> TurnResult:
        return TurnResult(
            state=state,
            reply=reply,
            changed_state=True,
            intent=result.intent,
            input_confidence=result.confidence,
            termination=True,
            used_llm=result.used_llm,
            llm_cost=result.cost,
        )

    @staticmethod
    def is_terminal(state: ConvState) -> bool:
        return state in TERMINAL_STATES
