"""
PII redaction for public share pages.
Operates on a string, returns the redacted string + a list of categories scrubbed.

Conservative by design — false positives are acceptable, false negatives are not.
We're operating on intimate communication data; trust collapse kills the product.
"""
import re
from typing import List, Tuple

# Order matters: more specific patterns first so credit cards aren't shortened to "phones".
PATTERNS = [
    # URLs — full http(s) or www.
    ("url", re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE), "[link redacted]"),
    # Emails
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email redacted]"),
    # Credit cards (13–19 digits, with optional separators) — match BEFORE phones
    ("card", re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[card redacted]"),
    # International phones with + prefix
    ("phone", re.compile(r"\+\d[\d\s\-().]{6,}\d"), "[phone redacted]"),
    # Local phones — 10+ digits with optional separators
    ("phone", re.compile(r"\b\d[\d\s\-().]{8,}\d\b"), "[phone redacted]"),
    # OTP-style 4–8 digit standalone codes (after phones so we don't double-mask)
    ("otp", re.compile(r"\b(?:OTP|otp|code|pin|verification)\D{0,6}(\d{4,8})\b"), "[code redacted]"),
    # Bank IBAN-ish / account numbers (long alphanumeric blocks of 12+ that look account-shaped)
    ("account", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"), "[account redacted]"),
    # Common address tokens — best effort, single line containing a street number + word
    ("address", re.compile(r"\b\d{1,5}\s+[A-Z][a-zA-Z]+(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Way|Ct|Court))\b"), "[address redacted]"),
]


def redact(text: str) -> Tuple[str, List[str]]:
    """Returns (redacted_text, categories_scrubbed)."""
    if not text:
        return text or "", []
    out = text
    found: List[str] = []
    for name, pat, replacement in PATTERNS:
        new_out, n = pat.subn(replacement, out)
        if n > 0:
            out = new_out
            if name not in found:
                found.append(name)
    return out, found
