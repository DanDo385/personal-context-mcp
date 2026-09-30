"""Bulk import pipeline.

Order matters and is the same for every provider:

 1. preserve the original archive in restricted cold storage, hashed
 2. detect and parse
 3. insert conversations and messages (idempotent on provider ids)
 4. ingest attachments - captured only when the bytes were really in the
    archive, deduplicated by SHA-256 across the whole corpus
 5. screen sensitivity deterministically, before anything leaves the process
 6. derive a session summary and classify it with conservative historical
    thresholds (a few conversations at a time; identical inputs are answered
    from the classification cache)
 7. create memory candidates only where confidence is high
 8. associate projects, queue anything ambiguous for review
 9. emit a report

Running the same archive twice is a no-op beyond refreshed timestamps.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from personal_context_mcp.artifacts.archives import ArchiveLimits
from personal_context_mcp.artifacts.keys import archive_key
from personal_context_mcp.classifier.policy import Routing, Verdict
from personal_context_mcp.importers.adapters import IMPORTERS, detect_importer
from personal_context_mcp.importers.base import (
    ConversationImporter,
    ImportReport,
    NormalizedAttachment,
    NormalizedConversation,
)
from personal_context_mcp.importers.sensitivity import ScreenResult, screen_texts
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.models import CheckpointFact
from personal_context_mcp.services import Services
from personal_context_mcp.util import sha256_bytes, truncate_for_budget

logger = get_logger(__name__)

#: A summary derived mechanically from a transcript, with no model involved.
#: It is marked `imported_metadata`, never `assistant_generated`, because no
#: assistant wrote it.
_SUMMARY_MESSAGE_BUDGET = 1200


def _hash_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def derive_summary(conversation: NormalizedConversation) -> str:
    """Mechanical summary: the opening exchange plus the closing one.

    Deliberately not model-generated. A bulk import of years of history should
    not cost thousands of LLM calls, and a truthful excerpt is more useful
    than a hallucinated précis. A model can write a better one later, on
    demand, through the normal checkpoint path.
    """
    messages = conversation.messages
    if not messages:
        return ""
    head = messages[:2]
    tail = messages[-2:] if len(messages) > 4 else []
    parts = [f"{m.role}: {truncate_for_budget(m.content, 180)}" for m in head]
    if tail:
        parts.append("…")
        parts += [f"{m.role}: {truncate_for_budget(m.content, 180)}" for m in tail]
    body = "\n".join(parts)
    return truncate_for_budget(
        f"Imported {conversation.provider} conversation with {len(messages)} messages.\n\n{body}",
        _SUMMARY_MESSAGE_BUDGET,
    )


async def screen_conversation(services: Services, conversation_id: Any, texts: list[str]) -> str:
    """Run the deterministic sensitivity screen and record it on the row.

    Only ever raises the stored level (GREATEST over the ordered enum), so a
    re-import can never make a conversation less private than it was.
    """
    settings = services.settings
    default = settings.import_default_sensitivity
    result = (
        screen_texts(texts, default=default)
        if settings.import_sensitivity_screen
        else ScreenResult(level=default)
    )
    stored = await services.db.fetchval(
        """
        UPDATE conversations
        SET sensitivity = GREATEST(sensitivity, $2::pc_sensitivity),
            metadata = metadata || jsonb_build_object('sensitivity_screen', $3::jsonb)
        WHERE id = $1
        RETURNING sensitivity::text
        """,
        conversation_id,
        result.level,
        result.reasons,
    )
    return str(stored or result.level)


async def route_conversation(
    services: Services,
    *,
    conversation_id: Any,
    provider: str,
    title: str | None,
    summary: str,
    message_count: int,
    sensitivity: str,
    report: ImportReport,
    use_cache: bool = True,
) -> Routing | None:
    """Classify one stored conversation and act on the routing.

    Shared by the importer and by ``reclassify-conversations``. Every write is
    idempotent - the summary is upserted, memories deduplicate on content,
    review items on (object, reason) - so running it again on the same
    conversation only refreshes what changed.
    """
    if not summary:
        return None

    state = {
        "title": title or "",
        "provider": provider,
        "summary": summary,
        "decisions": [],
        "facts": [],
        "tasks": [],
        "message_count": message_count,
        "sensitivity": sensitivity,
        "stated_project": None,
    }
    result, routing, event_id = await services.classification.classify_checkpoint(
        state, object_id=conversation_id, historical_import=True, use_cache=use_cache
    )

    project_id = (
        await services.projects.resolve(routing.project_slug) if routing.project_slug else None
    )
    async with services.db.transaction() as conn:
        # A conversation that already has a summary - from an earlier run, or
        # written by deep extraction - keeps its text, open questions and
        # context. Only the fields classification owns are refreshed.
        refreshed = await conn.fetchval(
            """
            UPDATE session_summaries
            SET project_id = coalesce($2, project_id),
                sensitivity = GREATEST(sensitivity, $3::pc_sensitivity),
                classification_status = $4::pc_classification_status,
                updated_at = now()
            WHERE conversation_id = $1
            RETURNING id
            """,
            conversation_id,
            project_id,
            sensitivity,
            "needs_review" if routing.needs_review else "classified",
        )
        if refreshed is None:
            await services.sessions._upsert_summary(
                conn,
                conversation_id=conversation_id,
                provider=provider,
                project_id=project_id,
                title=title,
                summary=summary,
                open_questions=[],
                important_context=[],
                sensitivity=sensitivity,
                needs_review=routing.needs_review,
            )
        if project_id:
            await conn.execute(
                "UPDATE conversations SET project_id = $2 WHERE id = $1",
                conversation_id,
                project_id,
            )
    report.summaries_created += 1

    # Historical imports create memories only when the classifier is
    # confident the conversation carries durable value. The alternative -
    # a memory per old chat - buries the useful ones.
    if routing.save_memory is Verdict.YES and routing.importance >= 0.6 and not result.degraded:
        fact = CheckpointFact(
            content=truncate_for_budget(f"{title or 'Earlier conversation'}: {summary}", 220),
            kind="knowledge",
            subject=routing.project_slug or (title or None),
            provenance="imported_metadata",
        )
        saved = await services.memories.save(
            content=fact.content,
            kind=fact.kind,
            subject=fact.subject,
            project_id=project_id,
            provenance="imported_metadata",
            sensitivity=sensitivity,
            importance=routing.importance,
            source_provider=provider,
            source_conversation_id=conversation_id,
        )
        if saved.outcome == "created":
            report.memories_created += 1

    if (
        not result.degraded
        and routing.deep_extract_p >= services.settings.extraction_min_probability
    ):
        report.deep_extraction_candidates += 1

    if routing.needs_review:
        queued = await services.classification.queue_review(
            object_type="conversation",
            object_id=conversation_id,
            reason=routing.review_reasons[0] if routing.review_reasons else "low_confidence",
            question=f"Imported {provider} conversation {title!r}: where does it belong?",
            suggested=routing.as_json(),
            confidence=routing.confidence,
            classification_event_id=event_id,
        )
        if queued:
            report.reviews_queued += 1
    return routing


class ImportRunner:
    def __init__(self, services: Services) -> None:
        self._services = services
        settings = services.settings
        self._limits = ArchiveLimits(
            max_entries=settings.max_archive_entries,
            max_total_bytes=settings.max_archive_uncompressed_bytes,
            max_entry_bytes=settings.max_artifact_file_bytes,
            max_compression_ratio=settings.max_archive_compression_ratio,
        )
        # Attachments are deduplicated by hash across the whole corpus; two
        # conversations importing the same bytes at once must not both miss
        # the duplicate check.
        self._attachment_lock = asyncio.Lock()

    async def run(
        self,
        archive: Path,
        *,
        provider: str | None = None,
        limit: int | None = None,
        store_archive: bool = True,
    ) -> ImportReport:
        # resolve() and is_file() both hit the filesystem, so they go to a
        # thread together rather than blocking the loop twice.
        archive = await asyncio.to_thread(lambda p: Path(p).expanduser().resolve(), archive)
        if not await asyncio.to_thread(archive.is_file):
            raise FileNotFoundError(f"No such export archive: {archive}")

        # A provider export can be gigabytes; hash it off the event loop.
        digest = await asyncio.to_thread(_hash_file, archive)

        importer: ConversationImporter | None
        if provider:
            factory = IMPORTERS.get(provider)
            if factory is None:
                raise ValueError(
                    f"Unknown provider {provider!r}. Choose one of: " + ", ".join(sorted(IMPORTERS))
                )
            importer = factory(self._limits)
        else:
            provider, importer = detect_importer(archive, self._limits)
            if importer is None:
                raise ValueError(
                    "Could not identify which provider produced this archive. "
                    "Pass the provider explicitly."
                )

        report = ImportReport(provider=provider, archive_sha256=digest)
        report.limitations = importer.limitations()

        existing = await self._services.db.fetchrow(
            "SELECT id, import_status, report FROM source_documents WHERE sha256 = $1", digest
        )
        if existing and existing["import_status"] == "imported":
            report.limitations.insert(
                0,
                "This archive was imported before. Conversations already in the "
                "library were skipped, so nothing was duplicated; any not yet "
                "imported (for example after a --limit trial) were added.",
            )

        source_id = await self._record_source(
            archive, provider=provider, digest=digest, store_archive=store_archive
        )

        # Parsing is sequential; storing and classifying run a few at a time,
        # because each conversation is dominated by a Jev round trip. At most
        # 2 x concurrency parsed conversations are held in memory.
        concurrency = max(1, self._services.settings.import_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        pending: set[asyncio.Task[None]] = set()

        async def import_guarded(conversation: NormalizedConversation) -> None:
            async with semaphore:
                try:
                    await self._import_one(conversation, report)
                except Exception as exc:
                    logger.exception("failed to import conversation")
                    report.errors.append(
                        f"{conversation.provider_conversation_id or '?'}: "
                        f"{type(exc).__name__}: {exc}"
                    )

        for index, conversation in enumerate(importer.parse(archive)):
            if limit is not None and index >= limit:
                break
            report.conversations_seen += 1
            if len(pending) >= concurrency * 2:
                _, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            pending.add(asyncio.create_task(import_guarded(conversation)))
        if pending:
            await asyncio.wait(pending)
        # Conversations the parser could not read are named, never dropped silently.
        report.errors.extend(getattr(importer, "problems", []))

        await self._services.db.execute(
            """
            UPDATE source_documents
            SET import_status = 'imported', conversation_count = $2,
                artifact_count = $3, report = $4
            WHERE id = $1
            """,
            source_id,
            report.conversations_imported,
            report.attachments_captured + report.attachments_referenced,
            report.as_dict(),
        )
        return report

    async def _record_source(
        self, archive: Path, *, provider: str, digest: str, store_archive: bool
    ) -> Any:
        object_key = None
        if store_archive and self._services.settings.storage_backend == "supabase":
            object_key = archive_key(provider=provider, filename=archive.name, sha256=digest)
            try:
                payload = await asyncio.to_thread(archive.read_bytes)
                await self._services.store.put(
                    object_key,
                    payload,
                    mime_type="application/zip",
                    bucket=self._services.settings.supabase_archive_bucket,
                )
            except Exception:
                logger.exception("could not archive the original export; continuing")
                object_key = None

        return await self._services.db.fetchval(
            """
            INSERT INTO source_documents (
                provider, original_filename, byte_size, sha256, object_key, import_status
            ) VALUES ($1,$2,$3,$4,$5,'parsed')
            ON CONFLICT (sha256) DO UPDATE
                SET import_status = 'parsed',
                    object_key = coalesce(source_documents.object_key, EXCLUDED.object_key),
                    updated_at = now()
            RETURNING id
            """,
            provider,
            archive.name,
            (await asyncio.to_thread(archive.stat)).st_size,
            digest,
            object_key,
        )

    async def _import_one(self, conversation: NormalizedConversation, report: ImportReport) -> None:
        services = self._services
        conversation_id, created = await services.sessions.ensure_conversation(
            provider=conversation.provider,
            provider_conversation_id=conversation.provider_conversation_id or None,
            title=conversation.title,
            model=conversation.model,
            imported=True,
            started_at=conversation.created_at,
        )
        report.conversations_imported += 1 if created else 0
        report.conversations_skipped += 0 if created else 1

        inserted = await services.sessions.add_messages(
            conversation_id,
            [
                {
                    "role": m.role,
                    "content": m.content,
                    "provenance": m.provenance,
                    "created_at": m.created_at,
                    "provider_message_id": m.provider_message_id,
                    "model": m.model,
                    "metadata": m.metadata,
                }
                for m in conversation.messages
            ],
            provider=conversation.provider,
        )
        report.messages_imported += inserted

        sensitivity = await screen_conversation(
            services,
            conversation_id,
            [conversation.title or "", *(m.content for m in conversation.messages)],
        )
        if sensitivity != "normal":
            report.conversations_screened_sensitive += 1

        for attachment in conversation.attachments:
            async with self._attachment_lock:
                await self._import_attachment(
                    attachment,
                    conversation=conversation,
                    conversation_id=conversation_id,
                    sensitivity=sensitivity,
                    report=report,
                )

        # Nothing further to do for a conversation we have seen before;
        # `personal-context-mcp reclassify-conversations` revisits old ones.
        if not created:
            return

        await route_conversation(
            services,
            conversation_id=conversation_id,
            provider=conversation.provider,
            title=conversation.title,
            summary=derive_summary(conversation),
            message_count=len(conversation.messages),
            sensitivity=sensitivity,
            report=report,
        )

    async def _import_attachment(
        self,
        attachment: NormalizedAttachment,
        *,
        conversation: NormalizedConversation,
        conversation_id: Any,
        sensitivity: str,
        report: ImportReport,
    ) -> None:
        services = self._services

        if not attachment.captured:
            await services.artifacts.register_reference(
                title=attachment.filename,
                description=(
                    f"Referenced in an imported {conversation.provider} conversation. "
                    "The export did not contain this file's contents."
                ),
                status="pending_import",
                source_provider=conversation.provider,
                source_type="imported",
                source_conversation_id=conversation_id,
                expected_files=[
                    {
                        "filename": attachment.filename,
                        "mime_type": attachment.mime_type,
                        "byte_size": attachment.byte_size,
                    }
                ],
                metadata=attachment.metadata,
            )
            report.attachments_referenced += 1
            return

        assert attachment.data is not None
        digest = sha256_bytes(attachment.data)
        duplicate = await services.artifacts.find_by_hash(digest)
        if duplicate is not None:
            # Same bytes already stored, possibly from another provider's
            # export. Link it to this conversation instead of storing twice.
            await services.artifacts.relate(
                duplicate,
                relation="related_to_conversation",
                target_type="conversation",
                target_id=conversation_id,
                relationship_status="confirmed",
                provenance="imported_metadata",
            )
            return

        record = await services.artifacts.save_files(
            title=attachment.filename,
            description=f"Imported from a {conversation.provider} export.",
            files={attachment.filename: attachment.data},
            source_provider=conversation.provider,
            source_type="imported",
            source_conversation_id=conversation_id,
            original_names={attachment.filename: attachment.filename},
        )
        report.attachments_captured += 1

        # Artifacts from a bulk import are classified but never auto-promoted
        # to the vault; historical thresholds apply.
        await services.classification.classify_artifact(
            {
                "title": record.title,
                "artifact_type": record.artifact_type,
                "source_type": "imported",
                "source_provider": conversation.provider,
                "file_paths": [f.relative_path for f in record.files],
                "conversation_title": conversation.title or "",
                "sensitivity": sensitivity,
            },
            object_id=record.id,
            historical_import=True,
        )
