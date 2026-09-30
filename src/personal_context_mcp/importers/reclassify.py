"""Re-run pass-1 classification over conversations already in the database.

Import classifies each conversation once, when it is first seen. This module
revisits them later - after ``QUESTIONS_VERSION`` changes, after a Jev outage
left results degraded, or after projects were created that old conversations
belong to - working from the stored messages, never from the export again.
The raw archive is never touched.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from personal_context_mcp.classifier.questions import QUESTIONS_VERSION
from personal_context_mcp.importers.base import (
    ImportReport,
    NormalizedConversation,
    NormalizedMessage,
)
from personal_context_mcp.importers.runner import (
    classification_transcript,
    derive_summary,
    route_conversation,
    screen_conversation,
    transcript_budget,
)
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.services import Services

logger = get_logger(__name__)


async def stale_conversations(
    services: Services,
    *,
    limit: int,
    provider: str | None = None,
    include_current: bool = False,
) -> list[UUID]:
    """Imported conversations with no usable classification under the
    current question set: never classified, classified by an older version,
    or classified only while Jev was unavailable."""
    rows = await services.db.fetch(
        """
        SELECT c.id
        FROM conversations c
        WHERE c.imported
          AND ($2::text IS NULL OR c.provider = $2)
          AND ($3 OR NOT EXISTS (
                SELECT 1 FROM classification_events e
                WHERE e.object_type = 'checkpoint'
                  AND e.object_id = c.id
                  AND e.questions_version = $4
                  AND NOT e.degraded))
        ORDER BY c.started_at DESC NULLS LAST
        LIMIT $1
        """,
        limit,
        provider,
        include_current,
        QUESTIONS_VERSION,
    )
    return [row["id"] for row in rows]


async def load_conversation(services: Services, conversation_id: UUID) -> NormalizedConversation:
    head = await services.db.fetchrow(
        """
        SELECT provider, provider_conversation_id, title, model, started_at, updated_at
        FROM conversations WHERE id = $1
        """,
        conversation_id,
    )
    if head is None:
        raise LookupError(f"conversation {conversation_id} not found")
    rows = await services.db.fetch(
        """
        SELECT role, content, provenance::text AS provenance, created_at
        FROM messages WHERE conversation_id = $1 ORDER BY sequence
        """,
        conversation_id,
    )
    return NormalizedConversation(
        provider=head["provider"],
        provider_conversation_id=head["provider_conversation_id"] or "",
        title=head["title"],
        created_at=head["started_at"],
        updated_at=head["updated_at"],
        model=head["model"],
        messages=[
            NormalizedMessage(
                role=row["role"],
                content=row["content"],
                provenance=row["provenance"],
                created_at=row["created_at"],
            )
            for row in rows
        ],
    )


async def reclassify_conversation(
    services: Services,
    conversation_id: UUID,
    report: ImportReport,
    *,
    use_cache: bool = True,
) -> None:
    conversation = await load_conversation(services, conversation_id)
    # Re-screen too: the patterns may have improved since the import, and the
    # screen only ever raises the stored level.
    sensitivity = await screen_conversation(
        services,
        conversation_id,
        [conversation.title or "", *(m.content for m in conversation.messages)],
    )
    if sensitivity != "normal":
        report.conversations_screened_sensitive += 1
    # Classify what the archive says the conversation is about now: the
    # stored summary when one exists (deep extraction writes a far better one
    # than the mechanical excerpt), otherwise a fresh excerpt.
    stored_summary = await services.db.fetchval(
        "SELECT summary FROM session_summaries WHERE conversation_id = $1", conversation_id
    )
    await route_conversation(
        services,
        conversation_id=conversation_id,
        provider=conversation.provider,
        title=conversation.title,
        summary=stored_summary or derive_summary(conversation),
        message_count=len(conversation.messages),
        sensitivity=sensitivity,
        report=report,
        use_cache=use_cache,
        transcript=classification_transcript(conversation, transcript_budget(services)),
    )


async def reclassify_conversations(
    services: Services,
    conversation_ids: list[UUID],
    *,
    concurrency: int | None = None,
    use_cache: bool = True,
) -> ImportReport:
    report = ImportReport(provider="*", archive_sha256="")
    semaphore = asyncio.Semaphore(max(1, concurrency or services.settings.import_concurrency))

    async def one(conversation_id: UUID) -> None:
        async with semaphore:
            report.conversations_seen += 1
            try:
                await reclassify_conversation(
                    services, conversation_id, report, use_cache=use_cache
                )
            except Exception as exc:
                logger.exception("failed to reclassify conversation")
                report.errors.append(f"{conversation_id}: {type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(cid) for cid in conversation_ids))
    return report


def summarise(report: ImportReport) -> dict[str, Any]:
    data = report.as_dict()
    return {
        "conversations": data["conversations"]["seen"],
        "screened_sensitive": data["conversations"]["screened_sensitive"],
        "summaries_refreshed": data["summaries_created"],
        "memories_created": data["memories_created"],
        "reviews_queued": data["reviews_queued"],
        "deep_extraction_candidates": data["deep_extraction_candidates"],
        "errors": data["errors"],
    }
