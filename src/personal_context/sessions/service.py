"""Conversations, messages and the session checkpoint.

``session_checkpoint`` is the write path every provider shares. It must be
**idempotent**: a model that checkpoints twice with the same content must not
produce two summaries, two decisions and two tasks. Idempotency comes from
deterministic keys - (provider, provider_conversation_id) for the
conversation, content hashes for decisions, memories and list items - rather
than from the caller remembering what it already sent.

The whole checkpoint runs in one transaction, so a failure halfway through
leaves nothing behind.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from personal_context.audit.service import AuditService
from personal_context.auth.principal import Principal
from personal_context.classifier.policy import Verdict
from personal_context.classifier.service import ClassificationService
from personal_context.config import Settings
from personal_context.db.pool import Database
from personal_context.embeddings.service import EmbeddingService
from personal_context.lists.service import ListService
from personal_context.logging_setup import get_logger
from personal_context.memory.service import MemoryService
from personal_context.models import (
    CheckpointArtifactRef,
    CheckpointDecision,
    CheckpointFact,
    CheckpointListUpdate,
    CheckpointProjectUpdate,
    CheckpointResult,
    CheckpointTask,
)
from personal_context.notes.service import NoteService
from personal_context.obsidian.paths import VaultPaths
from personal_context.obsidian.queue import SyncQueue
from personal_context.projects.service import ProjectService
from personal_context.util import sha256_text, truncate_for_budget, utcnow

logger = get_logger(__name__)

#: How much of a summary is worth sending to the classifier.
_CLASSIFIER_SUMMARY_TOKENS = 3000


class SessionService:
    def __init__(
        self,
        db: Database,
        *,
        settings: Settings,
        memories: MemoryService,
        projects: ProjectService,
        lists: ListService,
        notes: NoteService,
        classification: ClassificationService,
        embeddings: EmbeddingService,
        sync_queue: SyncQueue,
        vault_paths: VaultPaths,
        audit: AuditService,
    ) -> None:
        self._db = db
        self._settings = settings
        self._memories = memories
        self._projects = projects
        self._lists = lists
        self._notes = notes
        self._classification = classification
        self._embeddings = embeddings
        self._queue = sync_queue
        self._paths = vault_paths
        self._audit = audit

    # ------------------------------------------------------ conversations

    async def ensure_conversation(
        self,
        *,
        provider: str,
        provider_conversation_id: str | None,
        title: str | None = None,
        model: str | None = None,
        project_id: UUID | None = None,
        imported: bool = False,
        started_at: Any = None,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, bool]:
        """Idempotent on (provider, provider_conversation_id).

        When the provider gives us no conversation id - which several of them
        do not - we mint a stable synthetic one from the provider, the title
        and the day, so repeated checkpoints in one session still collapse
        onto one conversation instead of creating a new row each time.
        """
        executor = conn or self._db
        external_id = provider_conversation_id or (
            f"synthetic:{sha256_text(f'{provider}:{title or ""}:{utcnow():%Y-%m-%d}')[:32]}"
        )
        row = await executor.fetchrow(
            """
            INSERT INTO conversations (
                provider, provider_conversation_id, title, model, project_id,
                imported, started_at, import_key
            ) VALUES ($1,$2,$3,$4,$5,$6,coalesce($7, now()),$8)
            ON CONFLICT (provider, provider_conversation_id)
                WHERE provider_conversation_id IS NOT NULL
            DO UPDATE SET
                title = coalesce(EXCLUDED.title, conversations.title),
                model = coalesce(EXCLUDED.model, conversations.model),
                project_id = coalesce(EXCLUDED.project_id, conversations.project_id),
                updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            provider,
            external_id,
            title,
            model,
            project_id,
            imported,
            started_at,
            f"{provider}:{external_id}",
        )
        if row is None:  # pragma: no cover - the upsert always returns a row
            raise RuntimeError("conversation upsert returned no row")
        return row["id"], bool(row["created"])

    async def add_messages(
        self,
        conversation_id: UUID,
        messages: list[dict[str, Any]],
        *,
        provider: str,
        conn: asyncpg.Connection | None = None,
    ) -> int:
        """Insert raw messages (layer 1). Idempotent on provider message id."""
        executor = conn or self._db
        inserted = 0
        start = await executor.fetchval(
            "SELECT coalesce(max(sequence), -1) + 1 FROM messages WHERE conversation_id = $1",
            conversation_id,
        )
        for offset, message in enumerate(messages):
            content = (message.get("content") or "").strip()
            if not content:
                continue
            role = message.get("role") or "user"
            provenance = message.get("provenance") or (
                "user_statement"
                if role == "user"
                else "tool_result"
                if role == "tool"
                else "assistant_generated"
            )
            result = await executor.fetchval(
                """
                INSERT INTO messages (
                    conversation_id, provider, provider_message_id, model, role,
                    content, content_sha256, provenance, sequence, created_at, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::pc_provenance,$9,
                          coalesce($10, now()),$11)
                ON CONFLICT (conversation_id, provider_message_id)
                    WHERE provider_message_id IS NOT NULL
                DO NOTHING
                RETURNING id
                """,
                conversation_id,
                provider,
                message.get("provider_message_id"),
                message.get("model"),
                role,
                content,
                sha256_text(content),
                provenance,
                message.get("sequence", start + offset),
                message.get("created_at"),
                message.get("metadata") or {},
            )
            if result:
                inserted += 1
        return inserted

    # -------------------------------------------------------- checkpoint

    async def checkpoint(
        self,
        *,
        provider: str,
        principal: Principal,
        provider_conversation_id: str | None = None,
        title: str | None = None,
        summary: str | None = None,
        decisions: list[CheckpointDecision] | None = None,
        facts: list[CheckpointFact] | None = None,
        tasks: list[CheckpointTask] | None = None,
        list_updates: list[CheckpointListUpdate] | None = None,
        project_updates: list[CheckpointProjectUpdate] | None = None,
        open_questions: list[str] | None = None,
        artifacts: list[CheckpointArtifactRef] | None = None,
        important_context: list[str] | None = None,
        model: str | None = None,
        sensitivity: str = "normal",
        historical_import: bool = False,
    ) -> CheckpointResult:
        decisions = decisions or []
        facts = facts or []
        tasks = tasks or []
        list_updates = list_updates or []
        project_updates = project_updates or []
        open_questions = open_questions or []
        artifacts = artifacts or []
        important_context = important_context or []

        created: dict[str, int] = {}
        updated: dict[str, int] = {}
        skipped: dict[str, int] = {}
        notes_out: list[str] = []

        def bump(bucket: dict[str, int], key: str, n: int = 1) -> None:
            if n:
                bucket[key] = bucket.get(key, 0) + n

        # ---- 1. classify first, so routing informs what we write ---------
        stated_project = next(
            (u.project for u in project_updates if u.project),
            next((d.project for d in decisions if d.project), None),
        )
        state = self._checkpoint_state(
            provider=provider,
            title=title,
            summary=summary,
            decisions=decisions,
            facts=facts,
            tasks=tasks,
            open_questions=open_questions,
            important_context=important_context,
            sensitivity=sensitivity,
            stated_project=stated_project,
        )
        result, routing, event_id = await self._classification.classify_checkpoint(
            state, historical_import=historical_import
        )

        # ---- 2. everything else in one transaction ----------------------
        async with self._db.transaction() as conn:
            routed_project_id = (
                await self._projects.resolve(routing.project_slug) if routing.project_slug else None
            )
            conversation_id, conv_created = await self.ensure_conversation(
                provider=provider,
                provider_conversation_id=provider_conversation_id,
                title=title,
                model=model,
                project_id=routed_project_id,
                imported=historical_import,
                conn=conn,
            )
            bump(created if conv_created else updated, "conversations")

            # -- project updates (explicit beats inferred) ----------------
            for update in project_updates:
                project_id = await self._projects.resolve(update.project)
                if project_id is None:
                    project_id, _ = await self._projects.create(
                        name=update.project,
                        goals=update.goals,
                        source_provider=provider,
                        conn=conn,
                    )
                    bump(created, "projects")
                else:
                    await self._projects.update(
                        update.project, status=update.status, goals=update.goals, conn=conn
                    )
                    bump(updated, "projects")
                if update.event:
                    if await self._projects.add_event(
                        project_id,
                        summary=update.event,
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        conn=conn,
                    ):
                        bump(created, "project_events")
                    else:
                        bump(skipped, "project_events")
                routed_project_id = routed_project_id or project_id

            if routed_project_id:
                await conn.execute(
                    "UPDATE conversations SET project_id = coalesce(project_id, $2) WHERE id = $1",
                    conversation_id,
                    routed_project_id,
                )
                await self._projects.touch(routed_project_id, conn=conn)

            # -- session summary (layer 2) --------------------------------
            summary_id = None
            if summary and summary.strip():
                summary_id, summary_created = await self._upsert_summary(
                    conn,
                    conversation_id=conversation_id,
                    provider=provider,
                    project_id=routed_project_id,
                    title=title,
                    summary=summary.strip(),
                    open_questions=open_questions,
                    important_context=important_context,
                    sensitivity=sensitivity,
                    needs_review=routing.needs_review,
                )
                bump(created if summary_created else updated, "session_summaries")

            # -- decisions ------------------------------------------------
            for decision in decisions:
                project_id = await self._projects.resolve(decision.project) or routed_project_id
                inserted = await conn.fetchval(
                    """
                    INSERT INTO decisions (
                        statement, statement_sha256, rationale, project_id,
                        source_provider, source_conversation_id, provenance
                    ) VALUES ($1,$2,$3,$4,$5,$6,'user_statement'::pc_provenance)
                    ON CONFLICT (statement_sha256) DO NOTHING
                    RETURNING id
                    """,
                    decision.statement.strip(),
                    sha256_text(decision.statement.strip().lower()),
                    decision.rationale,
                    project_id,
                    provider,
                    conversation_id,
                )
                bump(created if inserted else skipped, "decisions")
                # A decision is also durable memory; storing it once in each
                # place is what makes it retrievable by both paths.
                if inserted:
                    saved = await self._memories.save(
                        content=decision.statement.strip(),
                        kind="decision",
                        subject=decision.project or routing.project_slug,
                        project_id=project_id,
                        provenance="user_statement",
                        sensitivity=sensitivity,
                        importance=max(0.7, routing.importance),
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        conn=conn,
                    )
                    bump(created if saved.outcome == "created" else updated, "memories")

            # -- facts / durable memory -----------------------------------
            if routing.save_memory is not Verdict.NO or facts:
                for fact in facts:
                    project_id = await self._projects.resolve(fact.project) or routed_project_id
                    saved = await self._memories.save(
                        content=fact.content,
                        kind=fact.kind,
                        subject=fact.subject,
                        project_id=project_id,
                        provenance=fact.provenance,
                        sensitivity=fact.sensitivity,
                        importance=(
                            fact.importance
                            if fact.importance is not None
                            else max(0.4, routing.importance)
                        ),
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        confidence=routing.confidence or None,
                        conn=conn,
                    )
                    bump(
                        created
                        if saved.outcome == "created"
                        else skipped
                        if saved.outcome == "duplicate"
                        else updated,
                        "memories",
                    )
            elif facts:  # pragma: no cover - defensive
                bump(skipped, "memories", len(facts))

            # -- tasks ----------------------------------------------------
            for task in tasks:
                project_id = await self._projects.resolve(task.project) or routed_project_id
                list_ref = task.list_ref or await self._default_task_list(conn, project_id)
                list_id = await self._lists.get_or_create(list_ref, list_type="task", conn=conn)
                _, item_created = await self._lists.add_item(
                    list_id=list_id,
                    text=task.text,
                    priority=task.priority,
                    due_date=task.due_date,
                    source_provider=provider,
                    source_conversation_id=conversation_id,
                    conn=conn,
                )
                bump(created if item_created else skipped, "tasks")

            # -- list updates ---------------------------------------------
            for list_update in list_updates:
                resolved_list_id = await self._lists.resolve(list_update.list_ref)
                if resolved_list_id is None:
                    resolved_list_id, _ = await self._lists.create(
                        name=list_update.list_ref, conn=conn
                    )
                    bump(created, "lists")
                    notes_out.append(
                        f"Created list {list_update.list_ref!r}; no existing list "
                        "matched. Call list_search first if that was not intended."
                    )
                for text in list_update.add:
                    _, item_created = await self._lists.add_item(
                        list_id=resolved_list_id,
                        text=text,
                        source_provider=provider,
                        source_conversation_id=conversation_id,
                        conn=conn,
                    )
                    bump(created if item_created else skipped, "list_items")
                for reference in list_update.complete:
                    item_id = await self._lists.resolve_item(resolved_list_id, reference)
                    if item_id:
                        await self._lists.update_item(item_id, status="done", conn=conn)
                        bump(updated, "list_items")
                    else:
                        bump(skipped, "list_items")
                        notes_out.append(
                            f"No item on {list_update.list_ref!r} matched {reference!r}; "
                            "nothing was completed."
                        )
                for reference in list_update.remove:
                    item_id = await self._lists.resolve_item(resolved_list_id, reference)
                    if item_id:
                        await self._lists.update_item(item_id, status="dropped", conn=conn)
                        bump(updated, "list_items")

            # -- artifact associations ------------------------------------
            for artifact_ref in artifacts:
                if artifact_ref.artifact_id is None:
                    bump(skipped, "artifacts")
                    notes_out.append(
                        f"Artifact {artifact_ref.title!r} was referenced without an id; "
                        "capture it with artifact_save_* or artifact_register_reference "
                        "first, then checkpoint."
                    )
                    continue
                await conn.execute(
                    """
                    INSERT INTO artifact_relationships (
                        artifact_id, relation, target_type, target_id, provenance
                    ) VALUES ($1,$2,'conversation',$3,'assistant_generated'::pc_provenance)
                    ON CONFLICT DO NOTHING
                    """,
                    artifact_ref.artifact_id,
                    artifact_ref.relation,
                    conversation_id,
                )
                if routed_project_id:
                    await conn.execute(
                        "UPDATE artifacts SET project_id = coalesce(project_id, $2) WHERE id = $1",
                        artifact_ref.artifact_id,
                        routed_project_id,
                    )
                bump(updated, "artifacts")

        # ---- 3. vault promotion, outside the transaction ----------------
        queued = 0
        review_items = 0
        if (
            summary_id
            and routing.promote_to_vault is Verdict.YES
            and self._settings.obsidian_enabled
        ):
            queued += await self._queue_summary_note(
                conversation_id=conversation_id,
                summary_id=summary_id,
                title=title or "Session summary",
                summary=summary or "",
                destination=routing.destination or "inbox",
                project_slug=routing.project_slug,
                provider=provider,
                classification_event_id=event_id,
                open_questions=open_questions,
            )

        if routing.needs_review:
            review_items = await self._queue_reviews(
                routing, conversation_id=conversation_id, event_id=event_id
            )

        await self._audit.record(
            tool="session_checkpoint",
            action="create",
            object_type="conversation",
            object_id=conversation_id,
            principal=principal,
            model=model,
            classification_event_id=event_id,
            resulting_version={"created": created, "updated": updated, "skipped": skipped},
            provenance="assistant_generated",
            metadata={"routing": routing.as_json()},
        )

        if result.degraded:
            notes_out.append(
                "The semantic classifier was unavailable, so routing fell back to "
                "deterministic rules and nothing was promoted to the vault. The "
                "checkpoint itself was saved in full and is queued for "
                "reclassification."
            )

        return CheckpointResult(
            conversation_id=conversation_id,
            session_summary_id=summary_id,
            created=created,
            updated=updated,
            skipped=skipped,
            classification={
                "classifier": result.classifier,
                "model": result.model,
                "degraded": result.degraded,
                **routing.as_json(),
            },
            queued_vault_jobs=queued,
            review_items=review_items,
            notes=notes_out,
        )

    # ---------------------------------------------------------- internals

    def _checkpoint_state(
        self,
        *,
        provider: str,
        title: str | None,
        summary: str | None,
        decisions: list[CheckpointDecision],
        facts: list[CheckpointFact],
        tasks: list[CheckpointTask],
        open_questions: list[str],
        important_context: list[str],
        sensitivity: str,
        stated_project: str | None,
    ) -> dict[str, Any]:
        """Compact, named-field state for the classifier.

        Named JSON fields rather than one blob: the docs are explicit that a
        structured state answers better when the context has several parts.
        """
        return {
            "title": title or "",
            "provider": provider,
            "summary": truncate_for_budget(summary or "", _CLASSIFIER_SUMMARY_TOKENS),
            "decisions": [d.statement for d in decisions][:20],
            "facts": [f.content for f in facts][:30],
            "tasks": [t.text for t in tasks][:20],
            "open_questions": open_questions[:10],
            "important_context": important_context[:10],
            "sensitivity": sensitivity,
            "stated_project": stated_project,
        }

    async def _upsert_summary(
        self,
        conn: asyncpg.Connection,
        *,
        conversation_id: UUID,
        provider: str,
        project_id: UUID | None,
        title: str | None,
        summary: str,
        open_questions: list[str],
        important_context: list[str],
        sensitivity: str,
        needs_review: bool,
    ) -> tuple[UUID, bool]:
        embedding = await self._embeddings.embed_one(
            f"{title or ''}\n\n{summary}", sensitivity=sensitivity
        )
        row = await conn.fetchrow(
            """
            INSERT INTO session_summaries (
                conversation_id, provider, project_id, title, summary, summary_sha256,
                open_questions, important_context, sensitivity,
                classification_status, embedding
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::pc_sensitivity,
                      $10::pc_classification_status,$11)
            ON CONFLICT (conversation_id) DO UPDATE SET
                -- A later checkpoint in the same session extends the record
                -- rather than replacing it, unless the text is identical.
                summary = CASE
                    WHEN session_summaries.summary_sha256 = EXCLUDED.summary_sha256
                        THEN session_summaries.summary
                    ELSE session_summaries.summary || E'\n\n' || EXCLUDED.summary END,
                summary_sha256 = EXCLUDED.summary_sha256,
                title = coalesce(EXCLUDED.title, session_summaries.title),
                project_id = coalesce(EXCLUDED.project_id, session_summaries.project_id),
                open_questions = EXCLUDED.open_questions,
                important_context = EXCLUDED.important_context,
                classification_status = EXCLUDED.classification_status,
                embedding = coalesce(EXCLUDED.embedding, session_summaries.embedding),
                version = session_summaries.version + 1,
                updated_at = now()
            RETURNING id, (xmax = 0) AS created
            """,
            conversation_id,
            provider,
            project_id,
            title,
            summary,
            sha256_text(summary),
            open_questions,
            important_context,
            sensitivity,
            "needs_review" if needs_review else "classified",
            embedding,
        )
        await conn.execute(
            "UPDATE conversations SET summary = $2, updated_at = now() WHERE id = $1",
            conversation_id,
            truncate_for_budget(summary, 400),
        )
        return row["id"], bool(row["created"])

    async def _default_task_list(self, conn: asyncpg.Connection, project_id: UUID | None) -> str:
        if project_id is None:
            return "tasks"
        slug = await conn.fetchval("SELECT slug FROM projects WHERE id = $1", project_id)
        return f"{slug}-tasks" if slug else "tasks"

    async def _queue_summary_note(
        self,
        *,
        conversation_id: UUID,
        summary_id: UUID,
        title: str,
        summary: str,
        destination: str,
        project_slug: str | None,
        provider: str,
        classification_event_id: UUID | None,
        open_questions: list[str],
    ) -> int:
        if destination == "archive_only":
            return 0

        body = summary.strip()
        if open_questions:
            body += "\n\n## Open questions\n\n" + "\n".join(f"- {q}" for q in open_questions)

        note_id, _, version = await self._notes.upsert(
            title=title,
            content=body,
            note_class=destination if destination != "archive_only" else "inbox",
            project_id=await self._projects.resolve(project_slug) if project_slug else None,
            source_provider=provider,
            source_conversation_id=conversation_id,
            provenance="assistant_generated",
            append=True,
        )
        vault_path = self._paths.note(
            note_class=destination, title=title, project_slug=project_slug
        )
        note = await self._notes.get(str(note_id))
        await self._db.execute(
            "UPDATE notes SET vault_path = $2 WHERE id = $1 AND vault_path IS NULL",
            note_id,
            vault_path,
        )
        job = await self._queue.enqueue(
            job_type="write_note",
            object_type="note",
            object_id=note_id,
            vault_path=vault_path,
            payload={"note_id": str(note_id), "version": version},
            content_sha256=sha256_text(note.content or body),
            source_provider=provider,
            classification_event_id=classification_event_id,
        )
        await self._db.execute(
            """
            UPDATE session_summaries
            SET metadata = metadata || jsonb_build_object('vault_note_id', $2::text)
            WHERE id = $1
            """,
            summary_id,
            str(note_id),
        )
        return 1 if job else 0

    async def _queue_reviews(
        self, routing: Any, *, conversation_id: UUID, event_id: UUID | None
    ) -> int:
        count = 0
        for reason in routing.review_reasons:
            question = {
                "ambiguous_project": "Which project does this session belong to?",
                "ambiguous_destination": "Where should this session's write-up be filed?",
                "sensitive_promotion": (
                    "This session looks sensitive but was marked worth promoting. "
                    "Write it to the vault?"
                ),
                "classifier_requested_review": "The classifier flagged this for review.",
                "classifier_degraded": (
                    "Classified by deterministic fallback only; confirm the routing."
                ),
                "historical_import_promotion_withheld": (
                    "Historical import: vault promotion was withheld. Promote anyway?"
                ),
            }.get(reason, "Confirm the routing for this session.")
            if await self._classification.queue_review(
                object_type="conversation",
                object_id=conversation_id,
                reason=reason,
                question=question,
                suggested=routing.as_json(),
                confidence=routing.confidence,
                classification_event_id=event_id,
            ):
                count += 1
        return count

    # ------------------------------------------------------------- reads

    async def recent_summaries(
        self, *, limit: int = 10, provider: str | None = None, project_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT s.id, s.title, s.summary, s.provider, s.updated_at,
                   s.open_questions, c.provider_conversation_id, p.slug AS project_slug
            FROM session_summaries s
            JOIN conversations c ON c.id = s.conversation_id
            LEFT JOIN projects p ON p.id = s.project_id
            WHERE ($1::text IS NULL OR s.provider = $1)
              AND ($2::uuid IS NULL OR s.project_id = $2)
            ORDER BY s.updated_at DESC LIMIT $3
            """,
            provider,
            project_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def conversation_messages(
        self, conversation_id: UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch(
            """
            SELECT role, content, provenance, sequence, created_at, model
            FROM messages WHERE conversation_id = $1
            ORDER BY sequence LIMIT $2
            """,
            conversation_id,
            limit,
        )
        return [dict(r) for r in rows]
