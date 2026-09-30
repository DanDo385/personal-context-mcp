"""Deterministic sensitivity screen for imported conversations."""

from __future__ import annotations

import pytest

from personal_context_mcp.classifier.base import Answer, ClassificationResult
from personal_context_mcp.classifier.policy import PolicyEngine, Verdict
from personal_context_mcp.config import Settings
from personal_context_mcp.importers.sensitivity import raise_to, screen_texts


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("here is my key sk-ant-api03-" + "a" * 40, "api_key"),
        ("token ghp_" + "b" * 36, "api_key"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nabc", "private_key"),
        ("my ssn is 123-45-6789", "us_ssn"),
        ("card 4111 1111 1111 1111 exp 12/29", "payment_card"),
        ("my password is hunter22!", "password_disclosure"),
        ("seed phrase words: apple banana ...", "seed_phrase"),
    ],
)
def test_credentials_and_identifiers_are_highly_sensitive(text: str, reason: str) -> None:
    result = screen_texts(["an ordinary opening line", text])
    assert result.level == "highly_sensitive"
    assert reason in result.reasons


@pytest.mark.parametrize(
    "text",
    [
        "My therapist suggested journaling.",
        "I was diagnosed with asthma last year.",
        "My lawyer says the settlement agreement is fine.",
        "What should go on my tax return?",
    ],
)
def test_personal_topics_are_private(text: str) -> None:
    assert screen_texts([text]).level == "private"


def test_ordinary_technical_chat_stays_normal() -> None:
    result = screen_texts(
        [
            "How do I add pgvector to Supabase?",
            "Run CREATE EXTENSION vector; then create an ivfflat index.",
            "Order 1234 5678 9012 3456 is not a card number.",  # fails Luhn
        ]
    )
    assert result.level == "normal"
    assert result.reasons == []


def test_screen_never_lowers_the_default() -> None:
    assert screen_texts(["nothing here"], default="private").level == "private"
    assert screen_texts(["my therapist"], default="highly_sensitive").level == "highly_sensitive"


def test_raise_to_only_raises() -> None:
    assert raise_to("normal", "private") == "private"
    assert raise_to("highly_sensitive", "private") == "highly_sensitive"


def _settings() -> Settings:
    return Settings(database_url="postgres://unused", classifier="rules")


def test_deep_extraction_noul_drives_the_triage_verdict() -> None:
    yes = ClassificationResult(answers={"deep_extraction": Answer("noul", 0.93)}, classifier="jev")
    no = ClassificationResult(answers={"deep_extraction": Answer("noul", 0.04)}, classifier="jev")
    missing = ClassificationResult(answers={}, classifier="rules")
    policy = PolicyEngine(_settings(), historical_import=True)
    assert policy.route(yes).deep_extract is Verdict.YES
    assert policy.route(no).deep_extract is Verdict.NO
    assert policy.route(missing).deep_extract is Verdict.UNSURE


@pytest.mark.parametrize(
    "text",
    [
        "Move the coins into self-custody before the exchange halts withdrawals.",
        "The diagnosis of the build failure pointed at a stale lockfile.",
        "Symptoms of the memory leak appear after an hour.",
        "created_at 1790785531871 and id 4539578763621486123",  # ms timestamp, 19-digit id
        "Order 5555 5555 5555 4443 is not Luhn-valid.",
    ],
)
def test_technical_vocabulary_is_not_personal(text: str) -> None:
    assert screen_texts([text]).level == "normal"


@pytest.mark.parametrize(
    "card", ["4111 1111 1111 1111", "5555 5555 5555 4444", "3782 822463 10005"]
)
def test_real_card_formats_are_caught(card: str) -> None:
    assert screen_texts([f"card {card}"]).level == "highly_sensitive"


def test_historical_imports_do_not_queue_destination_reviews() -> None:
    """Destination only places vault notes, which historical imports never
    write, so an unsure destination must not queue every import for review."""
    unsure = ClassificationResult(
        answers={"destination": Answer("choice", "research", confidence=0.6)},
        classifier="jev",
    )
    historical = PolicyEngine(_settings(), historical_import=True).route(unsure)
    live = PolicyEngine(_settings()).route(unsure)
    assert "ambiguous_destination" not in historical.review_reasons
    assert "ambiguous_destination" in live.review_reasons
    assert historical.destination == "research"
