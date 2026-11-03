"""Local redaction that runs before any text leaves the machine.

Pattern matching catches the obvious, structured secrets (emails, phone numbers,
API keys, card-like numbers). It cannot judge meaning, so semantic checks such as
"does this reveal unreleased pricing?" are left to the decision model, which only
ever sees the redacted text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Order matters: more specific patterns run first so a token is not half-matched
# by a broader rule.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PRIVATE_KEY", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("API_KEY", re.compile(
        r"\b(?:sk|pk|rk|tsk|ghp|gho|ghs|github_pat|xox[abpr]|AKIA|AIza)[-_A-Za-z0-9]{12,}\b"
    )),
    ("SECRET_ASSIGNMENT", re.compile(
        # (?!\[) skips values an earlier rule already replaced, e.g. "api_key=[API_KEY]"
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*['\"]?(?!\[)[^\s'\"]{6,}"
    )),
    ("CONNECTION_STRING", re.compile(r"(?i)\b(?:server|data source|accountkey|sharedaccesssignature)=[^;\s]+(?:;[^;\s]+)*")),
    ("EMAIL", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("CARD_NUMBER", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Candidate phone numbers; _phone_ok() decides which candidates really are phones.
    ("PHONE", re.compile(r"(?<![\w+])\+?\(?\d[\d\s().-]{7,18}\d(?!\w)")),
    ("IP_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


@dataclass
class RedactionResult:
    text: str
    findings: dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        return bool(self.findings)


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


_DATE = re.compile(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}$|^\d{1,2}[-./]\d{1,2}[-./]\d{2,4}$")


def _phone_ok(raw: str) -> bool:
    """Accept +-prefixed or separated numbers with 10-15 digits; reject dates and bare ids."""
    candidate = raw.strip()
    digits = re.sub(r"\D", "", candidate)
    if not 10 <= len(digits) <= 15 or _DATE.match(candidate):
        return False
    has_separator = bool(re.search(r"[\s().-]", candidate))
    return candidate.startswith("+") or has_separator


def redact(text: str) -> RedactionResult:
    """Replace structured secrets with typed placeholders like ``[EMAIL]``."""
    findings: dict[str, int] = {}
    out = text
    for label, pattern in _PATTERNS:
        def _sub(match: re.Match[str], label: str = label) -> str:
            raw = match.group(0)
            if label == "CARD_NUMBER":
                digits = re.sub(r"\D", "", raw)
                if not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                    return raw  # long number that is not a card, e.g. an order id
            elif label == "PHONE" and not _phone_ok(raw):
                return raw
            findings[label] = findings.get(label, 0) + 1
            return f"[{label}]"

        out = pattern.sub(_sub, out)
    return RedactionResult(text=out, findings=findings)
