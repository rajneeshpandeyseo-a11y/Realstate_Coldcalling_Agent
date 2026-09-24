"""Telephony provider package.

Exports the abstract interface, call-related result types and event types,
and the concrete implementations so consumers depend only on this package.
"""

from app.providers.base import (
    CallEventType,
    CallInitiationResult,
    TelephonyProvider,
)

__all__ = [
    "TelephonyProvider",
    "CallInitiationResult",
    "CallEventType",
    "PlivoTelephonyProvider",
    "MockTelephonyProvider",
]


def __getattr__(name: str):
    # Lazily import concrete providers to avoid heavy imports at package load.
    if name == "PlivoTelephonyProvider":
        from app.providers.telephony.plivo import PlivoTelephonyProvider

        return PlivoTelephonyProvider
    if name == "MockTelephonyProvider":
        from app.providers.telephony.mock import MockTelephonyProvider

        return MockTelephonyProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
