"""Deterministic sensitivity screen for imported conversations.

Runs before anything about a conversation leaves the process. It can only
*raise* a conversation's sensitivity, never lower it, and it answers only what
code can answer reliably: whether the transcript contains credentials,
government identifiers, payment card numbers, or talks in plain terms about
health, legal or personal-finance matters.

A conversation screened ``highly_sensitive`` is classified by the local rules
only (see ``PC_NO_EXTERNAL_SENSITIVITY``), is never sent for deep extraction,
and never auto-promotes to the vault. ``private`` still reaches Jev, redacted,
unless ``PC_NO_EXTERNAL_SENSITIVITY`` also lists ``private``.

The patterns live here, in one file, so they can be reviewed the way
``classifier/questions.py`` is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from personal_context_mcp.models import Sensitivity

_ORDER: dict[str, int] = {"normal": 0, "private": 1, "highly_sensitive": 2}

#: Material whose presence alone means the transcript must stay local.
_HIGHLY_SENSITIVE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "api_key",
        re.compile(
            r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
            r"|gh[pousr]_[A-Za-z0-9]{30,}"
            r"|github_pat_[A-Za-z0-9_]{40,}"
            r"|xox[abpr]-[A-Za-z0-9-]{20,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|AIza[0-9A-Za-z_-]{35}"
            r"|ops_[A-Za-z0-9_-]{40,}"
            r"|sb_secret_[A-Za-z0-9_-]{20,})\b"
        ),
    ),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}")),
    ("us_ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    (
        "seed_phrase",
        re.compile(r"\b(?:seed|recovery|mnemonic)\s+(?:phrase|words)\b[^\n]{0,40}:", re.I),
    ),
    (
        "password_disclosure",
        re.compile(r"\b(?:my|the)\s+(?:password|passcode|pin)\s+(?:is|was|=|:)\s*\S{4,}", re.I),
    ),
)

#: Topics that make a conversation personal. Word-boundary keyword lists,
#: kept short on purpose: a false "private" costs little, a false "normal"
#: leaks a diagnosis into a synced vault.
_PRIVATE_TOPICS: dict[str, tuple[str, ...]] = {
    # Phrases, not bare words: "diagnosis" and "symptoms" are everyday
    # debugging vocabulary ("diagnosis of the build failure").
    "health": (
        "diagnosed with",
        "my diagnosis",
        "prescription",
        "prescribed",
        "my doctor",
        "my therapist",
        "therapy session",
        "psychiatrist",
        "antidepressant",
        "my medication",
        "my symptoms",
        "blood test",
        "biopsy",
    ),
    "legal": (
        "my lawyer",
        "my attorney",
        "lawsuit",
        "child custody",  # bare "custody" is crypto self-custody
        "divorce",
        "subpoena",
        "settlement agreement",
        "arrested",
    ),
    "finance": (
        "my salary",
        "tax return",
        "routing number",
        "account number",
        "credit score",
        "my debt",
        "bankruptcy",
        "net worth",
    ),
}
_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    topic: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    for topic, words in _PRIVATE_TOPICS.items()
}

_HIGHLY_SENSITIVE_REASONS = frozenset({name for name, _ in _HIGHLY_SENSITIVE} | {"payment_card"})

_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _is_card_number(digits: str) -> bool:
    """Luhn alone is not enough: about one in ten random numbers pass it, and
    13-digit millisecond timestamps and long ids are everywhere in technical
    chats. Require a real card length for a real card network."""
    if not _luhn_ok(digits) or len(set(digits)) < 2:
        return False
    if len(digits) == 15:
        return digits[:2] in ("34", "37")  # American Express
    if len(digits) == 16:
        two, four = int(digits[:2]), int(digits[:4])
        return (
            digits[0] == "4"  # Visa
            or 51 <= two <= 55
            or 2221 <= four <= 2720  # Mastercard
            or digits.startswith(("6011", "65"))
            or 644 <= int(digits[:3]) <= 649  # Discover
        )
    return False


def _has_card_number(text: str) -> bool:
    for match in _CARD_CANDIDATE.finditer(text):
        if _is_card_number(re.sub(r"\D", "", match.group())):
            return True
    return False


@dataclass(frozen=True)
class ScreenResult:
    level: Sensitivity
    reasons: list[str] = field(default_factory=list)


def raise_to(current: str, floor: str) -> Sensitivity:
    """The stricter of two sensitivity levels."""
    return current if _ORDER[current] >= _ORDER[floor] else floor  # type: ignore[return-value]


def screen_texts(texts: Iterable[str], *, default: Sensitivity = "normal") -> ScreenResult:
    """Screen every message of one conversation; never lower than ``default``."""
    reasons: set[str] = set()
    level: Sensitivity = default
    for text in texts:
        if not text:
            continue
        for name, pattern in _HIGHLY_SENSITIVE:
            if name not in reasons and pattern.search(text):
                reasons.add(name)
        if "payment_card" not in reasons and _has_card_number(text):
            reasons.add("payment_card")
        for topic, pattern in _TOPIC_PATTERNS.items():
            if topic not in reasons and pattern.search(text):
                reasons.add(topic)

    if reasons & _HIGHLY_SENSITIVE_REASONS:
        level = raise_to(level, "highly_sensitive")
    elif reasons:
        level = raise_to(level, "private")
    return ScreenResult(level=level, reasons=sorted(reasons))
