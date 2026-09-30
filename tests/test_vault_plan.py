"""Vault selection policy and the transcript Jev sees."""

from __future__ import annotations

from personal_context_mcp.config import Settings
from personal_context_mcp.importers.base import NormalizedConversation, NormalizedMessage
from personal_context_mcp.importers.runner import classification_transcript
from personal_context_mcp.vault_plan import recency_bonus, score


def _settings() -> Settings:
    return Settings(database_url="postgres://unused", classifier="rules")


def test_recency_bonus_halves_every_halflife() -> None:
    s = _settings()
    assert recency_bonus(0, s) == s.vault_recency_bonus
    assert abs(recency_bonus(s.vault_recency_halflife_days, s) - s.vault_recency_bonus / 2) < 1e-9


def test_recent_uncertain_conversations_make_the_vault_old_ones_do_not() -> None:
    s = _settings()
    # Jev is unsure (0.4): a week-old chat qualifies, a two-year-old one does not.
    assert score(0.4, 0.0, 7, s) >= s.vault_min_score
    assert score(0.4, 0.0, 730, s) < s.vault_min_score
    # A clear yes qualifies at any age; close-read worthiness counts too.
    assert score(0.7, 0.0, 730, s) >= s.vault_min_score
    assert score(0.1, 0.6, 730, s) >= s.vault_min_score


def _conversation(n: int, size: int) -> NormalizedConversation:
    return NormalizedConversation(
        provider="grok",
        provider_conversation_id="c",
        title="t",
        created_at=None,
        updated_at=None,
        model=None,
        messages=[
            NormalizedMessage(
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i} " + "x" * size,
                provenance="user_statement",
            )
            for i in range(n)
        ],
    )


def test_transcript_is_whole_when_it_fits() -> None:
    text = classification_transcript(_conversation(6, 10), 10_000)
    assert text.startswith("USER: message 0")
    assert "ASSISTANT: message 5" in text
    assert "omitted" not in text


def test_long_transcript_keeps_both_ends_and_says_so() -> None:
    text = classification_transcript(_conversation(200, 500), 20_000)
    assert len(text) <= 20_100
    assert text.startswith("USER: message 0")
    assert "message 199" in text
    assert "characters from the middle omitted" in text
