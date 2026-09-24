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
