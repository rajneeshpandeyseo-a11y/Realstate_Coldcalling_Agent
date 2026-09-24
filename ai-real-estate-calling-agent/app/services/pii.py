"""PII redaction helpers (Phase 16).

Phone numbers and email addresses are considered PII in this product. These
helpers mask them so that audit logs and any logged payloads never persist raw
contact details. Pure/deterministic so they are trivial to test at zero cost.
"""

from __future__ import annotations

import re

# Indian mobile numbers: optional +91 (or 0 or none) prefix then 10 digits.
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?91[\s-]?|0)?(?:\()?([6-9]\d{4})(?:\)?[\s.-]?)(\d{5})(?!\d)"
)

# Simple email regex (covers most contact emails).
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def mask_phone(text: str) -> str:
    """Mask an Indian mobile number, keeping a few digits for context."""
    if not text:
        return text or ""

    def _repl(m):
        # Keep first two + last two digits of the 10-digit number for context.
        whole = re.sub(r"[^0-9]", "", m.group(0))
        digits = re.sub(r"[^0-9]", "", whole[-10:])
        return "+" + digits[:2] + "****" + digits[-2:]

    return PHONE_RE.sub(_repl, text)


def mask_email(text: str) -> str:
    """Replace an email address with a masked form."""
    if not text:
        return text or ""

    def _repl(m):
        local = m.group(0).split("@")[0]
        domain = m.group(0).split("@")[-1]
        keep = local[:2] if len(local) >= 2 else local
        return f"{keep}***@{domain}"

    return EMAIL_RE.sub(_repl, text)


def redact_pii(text: str) -> str:
    """Mask both phone numbers and email addresses in a string."""
    if not text:
        return text or ""
    return mask_email(mask_phone(text))


def redact_pii_value(value):
    """Redact strings in a nested structure in place (dicts/lists/plain)."""
    if isinstance(value, str):
        return redact_pii(value)
    if isinstance(value, dict):
        return {k: redact_pii_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_pii_value(v) for v in value]
    return value
