"""Enumerated types used across the domain.

Keeping enums in one place so the state machine, models, schemas and APIs
all reference the same canonical values.
"""

import enum


class LeadStatus(str, enum.Enum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    DISQUALIFIED = "disqualified"
    NOT_INTERESTED = "not_interested"
    CALLBACK_REQUESTED = "callback_requested"
    DO_NOT_CALL = "do_not_call"


class LeadScore(str, enum.Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    NOT_INTERESTED = "not_interested"


class CallStatus(str, enum.Enum):
    QUEUED = "queued"
    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NO_ANSWER = "no_answer"
    NO_RESPONSE = "no_response"
    BUSY = "busy"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CALLBACK_REQUESTED = "callback_requested"
    DO_NOT_CALL = "do_not_call"


class CallDirection(str, enum.Enum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class ConversationSpeaker(str, enum.Enum):
    AGENT = "agent"
    CUSTOMER = "customer"


class SiteVisitStatus(str, enum.Enum):
    REQUESTED = "requested"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"


class FollowUpStatus(str, enum.Enum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class ProviderType(str, enum.Enum):
    TELEPHONY = "telephony"
    STT = "stt"
    TTS = "tts"
    LLM = "llm"


class CostComponent(str, enum.Enum):
    TELEPHONY = "telephony"
    STT = "stt"
    TTS = "tts"
    LLM = "llm"
