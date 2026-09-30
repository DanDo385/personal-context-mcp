"""Pass 2 of the historical backfill: deep extraction by a frontier model.

Pass 1 (Jev, during import) reads only a mechanical excerpt and answers
``deep_extraction``: would a close reading of the whole transcript yield
decisions, facts or findings the excerpt misses? Only conversations it marked
*yes* reach this module. Here a Claude model reads the full transcript and
returns one structured payload, which is written through the ordinary
``session_checkpoint`` path - the same single transaction, the same
idempotency keys, the same classification and review routing as a live
session.

Division of labour, as in the design notes: Jev scores and routes; the
frontier model summarises and extracts; Python enforces the schema, the
privacy policy and provenance.

What this module deliberately does not do:

* send anything whose sensitivity is listed in ``PC_NO_EXTERNAL_SENSITIVITY``;
* turn years-old action items into open tasks - most are long done, so they
  are kept as context ("Unfinished at the time: ...") instead;
* overwrite anything the person wrote: the excerpt summary it replaces was
  generated mechanically by the importer.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

from personal_context_mcp.auth.principal import Principal
from personal_context_mcp.classifier.questions import DESTINATION_CRITERIA
from personal_context_mcp.config import Settings
from personal_context_mcp.embeddings.base import redact
from personal_context_mcp.importers.reclassify import load_conversation
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.models import CheckpointDecision, CheckpointFact
from personal_context_mcp.services import Services
from personal_context_mcp.util import sha256_text, utcnow

logger = get_logger(__name__)

#: Bump when the prompt or payload schema changes; recorded per conversation
#: so a later run can find everything extracted under an older version.
EXTRACTION_VERSION = "2026-09-24.1"

#: Server-side refusal fallback (see the Claude API docs): a declined request
#: is re-run on a model chosen by refusal category inside the same call.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

#: Per million tokens, input / output, for the dry-run estimate only.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


# --------------------------------------------------------------------------
# Payload schema (sent to the API as a JSON schema, validated on return)
# --------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExtractedDecision(_Strict):
    statement: str
    rationale: str | None


class ExtractedFact(_Strict):
    content: str
    kind: Literal["fact", "preference", "knowledge", "relationship"]
    subject: str | None
    stated_by: Literal["user", "assistant"]


class ExtractionPayload(_Strict):
    summary: str
    project: str | None
    decisions: list[ExtractedDecision]
    facts: list[ExtractedFact]
    unfinished_actions: list[str]
    open_questions: list[str]
    topics: list[str]
    note_title: str
    note_markdown: str
    tags: list[str]


SYSTEM_PROMPT = """\
You are reading one past conversation between a person and an AI assistant, \
taken from their chat history. Extract what a future assistant would need to \
know without rereading it. The output goes into the person's long-term \
memory, so it must be accurate and self-contained.

- summary: 3-8 sentences on what was worked out, written for someone who \
cannot see the conversation. Past tense. No preamble.
- project: the ongoing project this conversation is part of, named as the \
person refers to it, or null if it is not project work.
- decisions: choices that were actually settled, with the reason if one was \
given. Not options that were only discussed.
- facts: durable information that should outlive the conversation: stated \
preferences, facts about the person's setup or circumstances, and reusable \
technical or research findings. Each must stand alone. stated_by is "user" \
for something the person said, "assistant" for a conclusion or finding the \
assistant produced. Skip anything true only in the moment.
- unfinished_actions: things the person intended to do that the conversation \
does not show as done.
- open_questions: questions left unresolved at the end.
- topics: 1-5 short concept names this conversation is substantially about, \
suitable as wiki page titles (e.g. "MCP OAuth", "pgvector").

Then write the Obsidian note this conversation becomes, for the vault section \
named in the request:
- note_title: a clear, specific title of 3-8 words describing the subject (not \
a vague chat title like "New conversation"). Conversations on the same subject \
should get the same title, so they merge into one note.
- note_markdown: the note itself, written as a reference someone will reread \
without the conversation. Open with a 2-4 sentence overview, then use ## \
sections that fit the content (for example Key points, How it works, Steps, \
Decisions, Options compared, Open questions). Be generous: keep every detail, \
command, number, name and link worth having later. Markdown only: no front \
matter and no top-level # title.
- tags: 3-8 lowercase Obsidian tags, words joined with hyphens, nested with / \
where useful (for example mcp, obsidian, defi/pendle, agents/hermes). No #.

Use empty lists where nothing qualifies. Do not invent anything the transcript \
does not support."""


#: Vault folders a note can be written to (keys of DESTINATION_CRITERIA that
#: are real notes; archive_only never reaches here).
_NOTE_CLASSES = {"project", "research", "wiki", "concept", "resource", "daily", "inbox"}

FOLLOWUP_LIST = "From past chats"


def _clean_tags(tags: list[str]) -> list[str]:
    """Obsidian tags: lowercase, no spaces or leading #, nested with /."""
    out: list[str] = []
    for tag in tags:
        clean = re.sub(r"[^a-z0-9/_-]+", "-", tag.strip().lstrip("#").lower()).strip("-/")
        if clean and not clean.isdigit() and clean not in out:
            out.append(clean)
    return out[:8]


# --------------------------------------------------------------------------
# Candidate selection
# --------------------------------------------------------------------------


@dataclass
class Candidate:
    conversation_id: UUID
    title: str | None
    chars: int
    importance: float | None
    destination: str = "inbox"


async def extraction_candidates(
    services: Services,
    *,
    limit: int,
    min_importance: float = 0.0,
    redo: bool = False,
) -> list[Candidate]:
    """Imported conversations whose latest pass-1 deep_extraction probability
    is at least PC_EXTRACTION_MIN_PROBABILITY, most important first,
    excluding anything the privacy policy keeps local."""
    rows = await services.db.fetch(
        """
        SELECT c.id, c.title,
               (SELECT coalesce(sum(length(m.content)), 0)
                  FROM messages m WHERE m.conversation_id = c.id) AS chars,
               (ev.metadata->'routing'->>'importance')::float AS importance,
               ev.p
        FROM conversations c
        JOIN LATERAL (
            SELECT e.metadata,
                   coalesce((e.answers->'deep_extraction'->>'value')::float, 0) AS p
            FROM classification_events e
            WHERE e.object_type = 'checkpoint' AND e.object_id = c.id AND NOT e.degraded
            ORDER BY e.created_at DESC LIMIT 1
        ) ev ON true
        WHERE c.imported
          AND ev.p >= $5
          AND coalesce((ev.metadata->'routing'->>'importance')::float, 0) >= $2
          AND NOT (c.sensitivity::text = ANY($3::text[]))
          AND ($4 OR NOT c.metadata ? 'extraction')
        ORDER BY importance DESC NULLS LAST, ev.p DESC, c.started_at DESC NULLS LAST
        LIMIT $1
        """,
        limit,
        min_importance,
        list(services.settings.no_external_sensitivity),
        redo,
        services.settings.extraction_min_probability,
    )
    return [
        Candidate(
            conversation_id=row["id"],
            title=row["title"],
            chars=int(row["chars"]),
            importance=row["importance"],
        )
        for row in rows
    ]


async def vault_targets(services: Services, *, limit: int, redo: bool = False) -> list[Candidate]:
    """The conversations Jev's answers put in the vault (vault_plan), best
    first, not yet extracted unless ``redo``. Each carries the folder Jev chose."""
    from personal_context_mcp.vault_plan import vault_candidates

    chosen = await vault_candidates(services.db, services.settings)
    ids = [c.conversation_id for c in chosen]
    rows = await services.db.fetch(
        """
        SELECT c.id, c.metadata ? 'extraction' AS done,
               (SELECT coalesce(sum(length(m.content)), 0)
                  FROM messages m WHERE m.conversation_id = c.id) AS chars
        FROM conversations c WHERE c.id = ANY($1::uuid[])
        """,
        ids,
    )
    info = {row["id"]: row for row in rows}
    out: list[Candidate] = []
    for c in chosen:
        row = info.get(c.conversation_id)
        if row is None or (row["done"] and not redo):
            continue
        out.append(
            Candidate(
                conversation_id=c.conversation_id,
                title=c.title,
                chars=int(row["chars"]),
                importance=c.score,
                destination=c.destination,
            )
        )
        if len(out) >= limit:
            break
    return out


def estimate_cost(candidates: list[Candidate], settings: Settings) -> dict[str, Any]:
    """Rough upper bound, for deciding whether to run. ~3.5 chars per token;
    ~2k output tokens including thinking per conversation."""
    input_tokens = sum(
        min(c.chars, settings.extraction_max_transcript_chars) for c in candidates
    ) / 3.5 + 900 * len(candidates)
    output_tokens = 2000 * len(candidates)
    price_in, price_out = _PRICES.get(settings.extraction_model, (5.0, 25.0))
    return {
        "conversations": len(candidates),
        "model": settings.extraction_model,
        "est_input_tokens": int(input_tokens),
        "est_output_tokens": output_tokens,
        "est_cost_usd": round(input_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out, 2),
    }


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------


def render_transcript(messages: list[tuple[str, str]], max_chars: int) -> tuple[str, bool]:
    """``role: text`` lines. Over budget, keep the opening and the end - where
    problems are stated and where they are settled - and say what was cut."""
    lines = [f"{role.upper()}: {text.strip()}" for role, text in messages if text.strip()]
    full = "\n\n".join(lines)
    if len(full) <= max_chars:
        return full, False
    head = full[: int(max_chars * 0.4)]
    tail = full[-int(max_chars * 0.6) :]
    omitted = len(full) - len(head) - len(tail)
    return f"{head}\n\n[... {omitted} characters from the middle omitted ...]\n\n{tail}", True


@dataclass
class ExtractionReport:
    attempted: int = 0
    extracted: int = 0
    refused: int = 0
    failed: int = 0
    decisions: int = 0
    memories: int = 0
    notes: int = 0
    followups: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "extracted": self.extracted,
            "refused": self.refused,
            "failed": self.failed,
            "decisions_created": self.decisions,
            "memories_created": self.memories,
            "notes_written": self.notes,
            "followups_added": self.followups,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "errors": self.errors[:50],
        }


class DeepExtractor:
    def __init__(self, services: Services, client: anthropic.AsyncAnthropic | None = None):
        settings = services.settings
        self._services = services
        self._settings = settings
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._principal = Principal(subject="system:deep-extraction", provider="system")

    async def aclose(self) -> None:
        await self._client.close()

    async def _ask(
        self, title: str | None, transcript: str, destination: str = "inbox"
    ) -> tuple[Any, ExtractionPayload | None]:
        response = await self._client.beta.messages.create(
            model=self._settings.extraction_model,
            max_tokens=16000,
            betas=[_FALLBACK_BETA],
            fallbacks="default",
            system=SYSTEM_PROMPT,
            output_config={
                # Extraction is careful reading, not open-ended reasoning.
                "effort": "medium",
                "format": {
                    "type": "json_schema",
                    "schema": ExtractionPayload.model_json_schema(),
                },
            },
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Conversation title: {title or '(untitled)'}\n"
                        f"Vault section for the note: {destination}: "
                        f"{DESTINATION_CRITERIA.get(destination, '')}\n\n"
                        f"<transcript>\n{transcript}\n</transcript>"
                    ),
                }
            ],
        )
        if response.stop_reason == "refusal":
            return response, None
        if response.stop_reason == "max_tokens":
            raise RuntimeError("extraction hit max_tokens")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return response, ExtractionPayload.model_validate(json.loads(text))

    async def extract_one(
        self, conversation_id: UUID, report: ExtractionReport, destination: str = "inbox"
    ) -> None:
        services = self._services
        report.attempted += 1
        conversation = await load_conversation(services, conversation_id)
        sensitivity = await services.db.fetchval(
            "SELECT sensitivity::text FROM conversations WHERE id = $1", conversation_id
        )
        # Checked again here, not only in candidate selection: this is the
        # last point before the transcript leaves the process.
        if sensitivity in self._settings.no_external_sensitivity:
            raise PermissionError(f"sensitivity {sensitivity} is kept local")

        transcript, truncated = render_transcript(
            [(m.role, redact(m.content)) for m in conversation.messages],
            self._settings.extraction_max_transcript_chars,
        )
        response, payload = await self._ask(conversation.title, transcript, destination)
        usage = getattr(response, "usage", None)
        report.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        report.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)

        record: dict[str, Any] = {
            "version": EXTRACTION_VERSION,
            "model": getattr(response, "model", self._settings.extraction_model),
            "at": utcnow().isoformat(),
            "truncated": truncated,
            "request_id": getattr(response, "_request_id", None),
        }
        if payload is None:
            report.refused += 1
            details = getattr(response, "stop_details", None)
            record["status"] = "refused"
            record["refusal_category"] = getattr(details, "category", None)
            await self._mark(conversation_id, record)
            return

        # The excerpt summary the importer wrote is replaced, not appended to:
        # setting it first makes the checkpoint below see an identical hash.
        await services.db.execute(
            """
            UPDATE session_summaries
            SET summary = $2, summary_sha256 = $3, provenance = 'assistant_generated',
                updated_at = now()
            WHERE conversation_id = $1
            """,
            conversation_id,
            payload.summary,
            sha256_text(payload.summary),
        )

        important_context = [f"Unfinished at the time: {a}" for a in payload.unfinished_actions]
        result = await services.sessions.checkpoint(
            provider=conversation.provider,
            principal=self._principal,
            provider_conversation_id=conversation.provider_conversation_id or None,
            title=conversation.title,
            summary=payload.summary,
            decisions=[
                CheckpointDecision(
                    statement=d.statement, rationale=d.rationale, project=payload.project
                )
                for d in payload.decisions
            ],
            facts=[
                CheckpointFact(
                    content=f.content,
                    kind=f.kind,
                    subject=f.subject,
                    provenance=(
                        "user_statement" if f.stated_by == "user" else "assistant_generated"
                    ),
                    project=payload.project,
                    sensitivity=sensitivity,  # type: ignore[arg-type]
                )
                for f in payload.facts
            ],
            open_questions=payload.open_questions,
            important_context=important_context,
            model=record["model"],
            sensitivity=sensitivity or "normal",
            historical_import=True,
        )
        created = result.created or {}
        report.decisions += int(created.get("decisions", 0))
        report.memories += int(created.get("memories", 0))
        report.extracted += 1

        note_id = await self._write_note(
            conversation_id, conversation, payload, destination, sensitivity
        )
        report.notes += 1
        report.followups += await self._add_followups(conversation_id, conversation, payload)

        record.update(
            status="extracted",
            project=payload.project,
            topics=payload.topics,
            note_id=str(note_id),
            counts={
                "decisions": len(payload.decisions),
                "facts": len(payload.facts),
                "unfinished_actions": len(payload.unfinished_actions),
                "open_questions": len(payload.open_questions),
            },
        )
        await self._mark(conversation_id, record)

    async def _write_note(
        self,
        conversation_id: UUID,
        conversation: Any,
        payload: ExtractionPayload,
        destination: str,
        sensitivity: str | None,
    ) -> UUID:
        """Save the note Claude wrote and queue it for the vault.

        Jev chose the folder; Claude named the project and wrote the text.
        Notes with the same title merge (append), so repeated subjects
        consolidate into one note rather than many near-duplicates.
        """
        services = self._services
        project_id: UUID | None = None
        project_slug: str | None = None
        if payload.project and payload.project.strip():
            project_id, _ = await services.projects.create(
                name=payload.project.strip(), source_provider=conversation.provider
            )
            project_slug = await services.db.fetchval(
                "SELECT slug FROM projects WHERE id = $1", project_id
            )
        note_class = destination if destination in _NOTE_CLASSES else "inbox"
        title = payload.note_title.strip() or (conversation.title or "Untitled conversation")
        note_id, _, _ = await services.notes.upsert(
            title=title,
            content=payload.note_markdown,
            note_class=note_class,
            project_id=project_id,
            sensitivity=sensitivity or "normal",
            source_provider=conversation.provider,
            source_conversation_id=conversation_id,
            provenance="assistant_generated",
            author="deep-extraction",
            append=True,
        )
        happened = max((m.created_at for m in conversation.messages if m.created_at), default=None)
        await services.db.execute(
            """
            UPDATE notes SET metadata = metadata || $2::jsonb,
                   vault_path = coalesce(vault_path, $3)
            WHERE id = $1
            """,
            note_id,
            {
                "tags": _clean_tags(payload.tags),
                "topics": payload.topics,
                "source_date": happened.date().isoformat() if happened else None,
            },
            services.vault_paths.note(
                note_class=note_class, title=title, project_slug=project_slug
            ),
        )
        vault_path = await services.db.fetchval(
            "SELECT vault_path FROM notes WHERE id = $1", note_id
        )
        if services.settings.obsidian_enabled:
            await services.sync_queue.enqueue(
                job_type="write_note",
                object_type="note",
                object_id=note_id,
                vault_path=vault_path,
                payload={"note_id": str(note_id)},
                source_provider=conversation.provider,
            )
        return note_id

    async def _add_followups(
        self, conversation_id: UUID, conversation: Any, payload: ExtractionPayload
    ) -> int:
        """Unfinished actions from old chats go to one shared list, where any
        app (and the vault) can see, tick off or drop them."""
        if not payload.unfinished_actions:
            return 0
        services = self._services
        list_id, _ = await services.lists.create(
            name=FOLLOWUP_LIST,
            description="Things you meant to do, found in past conversations across your AI apps.",
            list_type="task",
        )
        happened = max((m.created_at for m in conversation.messages if m.created_at), default=None)
        where = (
            f"{conversation.provider}, {happened:%Y-%m-%d}" if happened else conversation.provider
        )
        added = 0
        for action in payload.unfinished_actions:
            _, created = await services.lists.add_item(
                list_id=list_id,
                text=action.strip(),
                notes=f'From "{conversation.title or "a conversation"}" ({where})',
                source_provider=conversation.provider,
                source_conversation_id=conversation_id,
                provenance="assistant_generated",
            )
            added += int(created)
        return added

    async def _mark(self, conversation_id: UUID, record: dict[str, Any]) -> None:
        await self._services.db.execute(
            """
            UPDATE conversations
            SET metadata = metadata || jsonb_build_object('extraction', $2::jsonb),
                updated_at = now()
            WHERE id = $1
            """,
            conversation_id,
            record,
        )

    async def run(
        self,
        conversation_ids: list[UUID],
        *,
        concurrency: int | None = None,
        destinations: dict[UUID, str] | None = None,
    ) -> ExtractionReport:
        report = ExtractionReport()
        semaphore = asyncio.Semaphore(max(1, concurrency or self._settings.extraction_concurrency))

        async def one(conversation_id: UUID) -> None:
            async with semaphore:
                try:
                    await self.extract_one(
                        conversation_id,
                        report,
                        (destinations or {}).get(conversation_id, "inbox"),
                    )
                except (anthropic.APIError, ValidationError, ValueError, RuntimeError) as exc:
                    report.failed += 1
                    report.errors.append(f"{conversation_id}: {type(exc).__name__}: {exc}")
                    logger.warning(
                        "deep extraction failed",
                        extra={
                            "conversation_id": str(conversation_id),
                            "error": type(exc).__name__,
                        },
                    )
                except PermissionError as exc:
                    report.errors.append(f"{conversation_id}: skipped: {exc}")

        await asyncio.gather(*(one(cid) for cid in conversation_ids))
        return report
