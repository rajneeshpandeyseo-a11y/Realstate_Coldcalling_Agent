"""Conversation state machine.

The engine is intentionally DETERMINISTIC: it follows a fixed scripted flow
so most turns are handled without any LLM call (the dominant cost driver).
The LLM is only invoked for the few edge cases the rules cannot classify.
"""

import enum


class ConvState(str, enum.Enum):
    """States in the outbound cold-calling conversation flow."""

    GREETING = "GREETING"            # identity confirmation ("kya main X ji se baat kar rahi hoon?")
    PERMISSION = "PERMISSION"        # availability check ("2 minute... Is this a good time?")
    INTRO = "INTRO"                  # pitch the company + projects
    PROPERTY_SELECTION = "PROPERTY_SELECTION"  # ask which property the caller wants
    NEED_FINDING = "NEED_FINDING"    # ask about buying interest (legacy/deprecated)
    QUALIFICATION = "QUALIFICATION"  # ordered slots: type -> location -> budget -> purpose -> timeline
    SUMMARY_CONFIRM = "SUMMARY_CONFIRM"  # recap type+location+budget ("Correct?")
    PRESENT = "PRESENT"              # present 1-3 matched inventory options, check interest
    OBJECTION = "OBJECTION"          # handle objection / hesitation
    CLOSING = "CLOSING"              # propose site visit
    VISIT_BOOKING = "VISIT_BOOKING"  # capture preferred date/time
    CALLBACK = "CALLBACK"            # capture best time to call back
    NOT_INTERESTED = "NOT_INTERESTED"
    DO_NOT_CALL = "DO_NOT_CALL"
    THANK_YOU = "THANK_YOU"          # polite wrap-up
    END = "END"                      # terminal


# Terminal states (the conversation can end here).
TERMINAL_STATES = {
    ConvState.NOT_INTERESTED,
    ConvState.DO_NOT_CALL,
    ConvState.END,
}

# User turn is expected ("awaiting_input") in these states.
AWAITS_INPUT = {
    ConvState.GREETING,
    ConvState.PERMISSION,
    ConvState.INTRO,
    ConvState.PROPERTY_SELECTION,
    ConvState.NEED_FINDING,
    ConvState.QUALIFICATION,
    ConvState.SUMMARY_CONFIRM,
    ConvState.PRESENT,
    ConvState.OBJECTION,
    ConvState.CLOSING,
    ConvState.VISIT_BOOKING,
    ConvState.CALLBACK,
}
