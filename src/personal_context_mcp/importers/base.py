"""Provider export importers.

Each adapter detects its own format, parses it, and normalises into one
internal shape. Two rules, both of which exist because getting them wrong
destroys history:

* **Idempotency.** Every conversation carries a deterministic key derived from
  the provider and its own id. Re-running an import updates rather than
  duplicates. The archive's SHA-256 is recorded too, so a repeated import of
  the same file is detected before parsing.
* **Honesty about attachments.** An export that names a file it does not
  contain produces a ``pending_import`` artifact, never a captured one.

Formats are read from real export archives. Where a provider's current export
does not contain something we want, the adapter says so in its report rather
than inventing a structure for it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from personal_context_mcp.artifacts.archives import ArchiveLimits, iter_safe_entries
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.util import guess_mime

logger = get_logger(__name__)


@dataclass
class NormalizedMessage:
    role: str
    content: str
    provenance: str
    created_at: datetime | None = None
    provider_message_id: str | None = None
    model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedAttachment:
    """A file an export mentions. ``data`` is None when only named."""

    filename: str
    data: bytes | None = None
    mime_type: str | None = None
    byte_size: int | None = None
    message_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def captured(self) -> bool:
        return self.data is not None


@dataclass
class NormalizedConversation:
    provider: str
    provider_conversation_id: str
    title: str | None
    created_at: datetime | None
    updated_at: datetime | None
    model: str | None
    messages: list[NormalizedMessage] = field(default_factory=list)
    attachments: list[NormalizedAttachment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImportReport:
    provider: str
    archive_sha256: str
    conversations_seen: int = 0
    conversations_imported: int = 0
    conversations_skipped: int = 0
    messages_imported: int = 0
    attachments_captured: int = 0
    attachments_referenced: int = 0
    summaries_created: int = 0
    memories_created: int = 0
    reviews_queued: int = 0
    conversations_screened_sensitive: int = 0
    deep_extraction_candidates: int = 0
    limitations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "archive_sha256": self.archive_sha256,
            "conversations": {
                "seen": self.conversations_seen,
                "imported": self.conversations_imported,
                "skipped_unchanged": self.conversations_skipped,
                "screened_sensitive": self.conversations_screened_sensitive,
            },
            "messages_imported": self.messages_imported,
            "attachments": {
                "captured": self.attachments_captured,
                "referenced_only": self.attachments_referenced,
            },
            "summaries_created": self.summaries_created,
            "memories_created": self.memories_created,
            "reviews_queued": self.reviews_queued,
            "deep_extraction_candidates": self.deep_extraction_candidates,
            "limitations": self.limitations,
            "errors": self.errors[:50],
        }


@runtime_checkable
class ConversationImporter(Protocol):
    provider: str

    def __init__(self, limits: ArchiveLimits | None = None) -> None: ...

    def detect(self, path: Path) -> bool: ...

    def parse(self, path: Path) -> Iterator[NormalizedConversation]: ...

    def limitations(self) -> list[str]: ...


# --------------------------------------------------------------- utilities


def parse_timestamp(value: Any) -> datetime | None:
    """Accept the several shapes providers use for a timestamp."""
    if value is None:
        return None
    if isinstance(value, dict):
        # MongoDB extended JSON, used by Grok: {"$date": {"$numberLong": "<ms>"}}
        # or {"$date": "<iso>"} or {"$date": <ms>}.
        inner = value.get("$date", value.get("$numberLong"))
        if isinstance(inner, dict):
            inner = inner.get("$numberLong")
        if isinstance(inner, str) and inner.lstrip("-").isdigit():
            inner = int(inner)
        return parse_timestamp(inner) if inner is not None else None
    if isinstance(value, (int, float)):
        # Heuristic: values past ~2001 in seconds; anything far larger is ms.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def provenance_for(role: str) -> str:
    return {
        "user": "user_statement",
        "assistant": "assistant_generated",
        "model": "assistant_generated",
        "tool": "tool_result",
        "system": "imported_metadata",
    }.get(role.lower(), "imported_metadata")


def iter_json_members(
    archive: Path, *, match: Callable[[str], bool], limits: ArchiveLimits
) -> Iterator[tuple[str, Any]]:
    """Yield (basename, parsed JSON) for members whose basename satisfies ``match``.

    One member is held in memory at a time, so a large sharded export is read
    as a stream.
    """

    def wants(name: str) -> bool:
        return match(name.rsplit("/", 1)[-1])

    for entry in iter_safe_entries(archive, limits=limits, name_filter=wants):
        base = entry.name.rsplit("/", 1)[-1]
        try:
            yield base, json.loads(entry.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("unparsable export member", extra={"member": base, "why": str(exc)})


def load_json_members(
    archive: Path, *, names: tuple[str, ...], limits: ArchiveLimits
) -> dict[str, Any]:
    """Read named JSON members out of an export archive, safely."""
    wanted = set(names)
    return dict(iter_json_members(archive, match=wanted.__contains__, limits=limits))


def collect_attachments(
    archive: Path,
    *,
    limits: ArchiveLimits,
    skip_suffixes: tuple[str, ...] = (".json",),
    skip_paths: tuple[str, ...] = (),
) -> dict[str, NormalizedAttachment]:
    """Index every non-JSON file in an export by its basename.

    Providers reference attachments inconsistently - sometimes by id, sometimes
    by filename - so the index is keyed on both. ``skip_paths`` leaves out
    members (by path prefix) that are not attachments at all.
    """
    index: dict[str, NormalizedAttachment] = {}
    for entry in iter_safe_entries(
        archive,
        limits=limits,
        name_filter=lambda n: (
            not n.lower().endswith(skip_suffixes) and not n.startswith(skip_paths)
        ),
    ):
        basename = entry.name.rsplit("/", 1)[-1]
        if not basename:
            continue
        attachment = NormalizedAttachment(
            filename=basename,
            data=entry.data,
            mime_type=guess_mime(basename),
            byte_size=entry.byte_size,
            metadata={"archive_member": entry.name, "sha256": entry.sha256},
        )
        index[basename] = attachment
        # ChatGPT names the file "file-abc.dat" but references it as "file-abc".
        index.setdefault(basename.rsplit(".", 1)[0], attachment)
        # Also index by the leading id segment, e.g. "file-abc123-photo.png".
        stem = basename.split("-", 1)[0]
        if stem and stem not in index:
            index[stem] = attachment
    return index
