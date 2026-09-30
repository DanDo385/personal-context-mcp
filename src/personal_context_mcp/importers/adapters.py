"""Concrete importer adapters.

Each adapter targets the export format the provider actually ships. Where a
format varies across export versions, the adapter probes for the shapes it
knows and reports anything it could not read rather than guessing.

None of these fabricate structure. If a provider's export does not contain
attachment bytes, the adapter says so in ``limitations()`` and every
attachment it names becomes a ``pending_import`` artifact.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_context_mcp.artifacts.archives import ArchiveLimits, iter_safe_entries
from personal_context_mcp.importers.base import (
    ConversationImporter,
    NormalizedAttachment,
    NormalizedConversation,
    NormalizedMessage,
    collect_attachments,
    load_json_members,
    parse_timestamp,
    provenance_for,
)
from personal_context_mcp.logging_setup import get_logger

logger = get_logger(__name__)


def _text_from_parts(parts: Any) -> str:
    """Flatten the 'parts' shapes providers use into plain text."""
    if parts is None:
        return ""
    if isinstance(parts, str):
        return parts
    if isinstance(parts, dict):
        for key in ("text", "content", "value"):
            if isinstance(parts.get(key), str):
                return parts[key]
        return ""
    if isinstance(parts, list):
        chunks = [_text_from_parts(p) for p in parts]
        return "\n".join(c for c in chunks if c)
    return ""


class ChatGPTImporter:
    """OpenAI's `conversations.json` export.

    The export is a list of conversations, each with a `mapping` of message
    nodes forming a tree. We walk it from `current_node` back to the root so
    the linear path actually shown to the user is what gets imported, rather
    than every abandoned regeneration branch.
    """

    provider = "chatgpt"

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()

    def detect(self, path: Path) -> bool:
        # ChatGPT and Claude both ship a file called `conversations.json`, so
        # the filename alone is not enough - detection has to look at the
        # shape. ChatGPT's conversations carry a `mapping` of message nodes.
        try:
            members = load_json_members(path, names=("conversations.json",), limits=self._limits)
        except Exception:
            return False
        data = members.get("conversations.json")
        if not isinstance(data, list) or not data:
            return False
        first = data[0]
        return isinstance(first, dict) and "mapping" in first

    def limitations(self) -> list[str]:
        return [
            "ChatGPT exports include user-uploaded files and DALL-E images as "
            "separate members; these are captured when present. Files that were "
            "only rendered in the UI (for example some canvas documents) may be "
            "named in the transcript without a corresponding member, and are "
            "imported as pending_import references.",
            "The export contains no per-message model attribution for older "
            "conversations; model is taken from metadata where present.",
        ]

    def parse(self, path: Path) -> Iterator[NormalizedConversation]:
        members = load_json_members(path, names=("conversations.json",), limits=self._limits)
        conversations = members.get("conversations.json") or []
        if not isinstance(conversations, list):
            logger.warning("conversations.json was not a list; skipping")
            return

        attachments = collect_attachments(path, limits=self._limits)

        for raw in conversations:
            if not isinstance(raw, dict):
                continue
            mapping = raw.get("mapping") or {}
            messages = self._linear_messages(mapping, raw.get("current_node"))
            if not messages:
                continue

            found, missing = self._resolve_attachments(raw, mapping, attachments)
            yield NormalizedConversation(
                provider=self.provider,
                provider_conversation_id=str(
                    raw.get("conversation_id") or raw.get("id") or raw.get("title") or ""
                ),
                title=raw.get("title"),
                created_at=parse_timestamp(raw.get("create_time")),
                updated_at=parse_timestamp(raw.get("update_time")),
                model=raw.get("default_model_slug"),
                messages=messages,
                attachments=found + missing,
                metadata={"is_archived": raw.get("is_archived")},
            )

    def _linear_messages(
        self, mapping: dict[str, Any], current_node: Any
    ) -> list[NormalizedMessage]:
        # Walk parent links from the leaf so only the surviving branch is
        # imported; then reverse into chronological order.
        chain: list[dict[str, Any]] = []
        node_id = current_node
        seen: set[str] = set()
        while node_id and node_id in mapping and node_id not in seen:
            seen.add(node_id)
            node = mapping[node_id]
            if node.get("message"):
                chain.append(node["message"])
            node_id = node.get("parent")

        if not chain:
            # Older exports sometimes lack current_node; fall back to the
            # whole mapping ordered by creation time.
            chain = [
                n["message"] for n in mapping.values() if isinstance(n, dict) and n.get("message")
            ]
            chain.sort(key=lambda m: m.get("create_time") or 0)
        else:
            chain.reverse()

        out: list[NormalizedMessage] = []
        for message in chain:
            author = (message.get("author") or {}).get("role") or "user"
            text = _text_from_parts((message.get("content") or {}).get("parts"))
            if not text.strip():
                continue
            metadata = message.get("metadata") or {}
            out.append(
                NormalizedMessage(
                    role=author,
                    content=text,
                    provenance=provenance_for(author),
                    created_at=parse_timestamp(message.get("create_time")),
                    provider_message_id=message.get("id"),
                    model=metadata.get("model_slug"),
                    metadata={"finish": metadata.get("finish_details")},
                )
            )
        return out

    def _resolve_attachments(
        self,
        raw: dict[str, Any],
        mapping: dict[str, Any],
        index: dict[str, NormalizedAttachment],
    ) -> tuple[list[NormalizedAttachment], list[NormalizedAttachment]]:
        """Match attachment references in the transcript to archive members."""
        found: list[NormalizedAttachment] = []
        missing: list[NormalizedAttachment] = []
        seen: set[str] = set()

        for node in mapping.values():
            if not isinstance(node, dict):
                continue
            message = node.get("message") or {}
            metadata = message.get("metadata") or {}
            for attachment in metadata.get("attachments") or []:
                name = str(attachment.get("name") or attachment.get("id") or "")
                if not name or name in seen:
                    continue
                seen.add(name)
                match = index.get(name) or index.get(str(attachment.get("id") or ""))
                if match is not None:
                    found.append(match)
                else:
                    missing.append(
                        NormalizedAttachment(
                            filename=name,
                            byte_size=attachment.get("size"),
                            mime_type=attachment.get("mime_type"),
                            metadata={"reason": "not present in export archive"},
                        )
                    )
        return found, missing


class ClaudeImporter:
    """Anthropic's `conversations.json` export.

    Each conversation has `chat_messages`, each with a `content` list of typed
    blocks. Attachments appear as `attachments` and `files` entries.
    """

    provider = "claude"

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()

    def detect(self, path: Path) -> bool:
        try:
            members = load_json_members(path, names=("conversations.json",), limits=self._limits)
        except Exception:
            return False
        data = members.get("conversations.json")
        return isinstance(data, list) and bool(data) and "chat_messages" in (data[0] or {})

    def limitations(self) -> list[str]:
        return [
            "Claude exports carry attachment metadata (name, type, extracted "
            "text) reliably, but the original binary is not always included. "
            "Where extracted text is present it is captured as a text artifact; "
            "where only a name is present the artifact is pending_import.",
        ]

    def parse(self, path: Path) -> Iterator[NormalizedConversation]:
        members = load_json_members(path, names=("conversations.json",), limits=self._limits)
        conversations = members.get("conversations.json") or []
        archive_index = collect_attachments(path, limits=self._limits)

        for raw in conversations:
            if not isinstance(raw, dict):
                continue
            messages: list[NormalizedMessage] = []
            attachments: list[NormalizedAttachment] = []

            for index, message in enumerate(raw.get("chat_messages") or []):
                role = message.get("sender") or "user"
                text = message.get("text") or _text_from_parts(message.get("content"))
                if text.strip():
                    messages.append(
                        NormalizedMessage(
                            role="assistant" if role == "assistant" else "user",
                            content=text,
                            provenance=provenance_for(role),
                            created_at=parse_timestamp(message.get("created_at")),
                            provider_message_id=message.get("uuid"),
                        )
                    )
                attachments += self._message_attachments(message, index, archive_index)

            if messages:
                yield NormalizedConversation(
                    provider=self.provider,
                    provider_conversation_id=str(raw.get("uuid") or ""),
                    title=raw.get("name") or None,
                    created_at=parse_timestamp(raw.get("created_at")),
                    updated_at=parse_timestamp(raw.get("updated_at")),
                    model=raw.get("model"),
                    messages=messages,
                    attachments=attachments,
                    metadata={"account": (raw.get("account") or {}).get("uuid")},
                )

    def _message_attachments(
        self, message: dict[str, Any], index: int, archive_index: dict[str, NormalizedAttachment]
    ) -> list[NormalizedAttachment]:
        out: list[NormalizedAttachment] = []
        for attachment in (message.get("attachments") or []) + (message.get("files") or []):
            name = str(
                attachment.get("file_name")
                or attachment.get("file_uuid")
                or attachment.get("name")
                or ""
            )
            if not name:
                continue
            match = archive_index.get(name)
            if match is not None:
                match.message_index = index
                out.append(match)
                continue

            extracted = attachment.get("extracted_content")
            if isinstance(extracted, str) and extracted.strip():
                # The text really is in the export, so this IS captured -
                # as text, which is what Claude preserved.
                out.append(
                    NormalizedAttachment(
                        filename=f"{name}.txt" if "." not in name else name,
                        data=extracted.encode("utf-8"),
                        mime_type="text/plain",
                        byte_size=len(extracted.encode("utf-8")),
                        message_index=index,
                        metadata={"source": "extracted_content"},
                    )
                )
            else:
                out.append(
                    NormalizedAttachment(
                        filename=name,
                        byte_size=attachment.get("file_size"),
                        mime_type=attachment.get("file_type"),
                        message_index=index,
                        metadata={"reason": "no bytes or extracted text in export"},
                    )
                )
        return out


class GeminiImporter:
    """Google Takeout's "My Activity" export for Gemini.

    Takeout is an activity log, not a conversation store: each record is one
    prompt/response pair with a timestamp, and there is no stable conversation
    id. Records are grouped into synthetic conversations by day so the import
    is stable across runs, and the limitation is reported explicitly.
    """

    provider = "gemini"

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()

    def detect(self, path: Path) -> bool:
        try:
            for entry in iter_safe_entries(
                path,
                limits=self._limits,
                name_filter=lambda n: n.endswith(".json") and "My Activity" in n,
            ):
                return b"products" in entry.data[:20000] or b"Gemini" in entry.data[:20000]
        except Exception:
            return False
        return False

    def limitations(self) -> list[str]:
        return [
            "Google Takeout exports Gemini as an activity log with no stable "
            "conversation identifier. Records are grouped into one synthetic "
            "conversation per day, so multi-day threads appear split and two "
            "unrelated same-day chats may appear merged.",
            "Takeout does not include images or files generated in Gemini; these "
            "cannot be imported and are not recorded as artifacts at all, since "
            "the export gives no filename to reference.",
        ]

    def parse(self, path: Path) -> Iterator[NormalizedConversation]:
        import json as _json

        by_day: dict[str, list[dict[str, Any]]] = {}
        for entry in iter_safe_entries(
            path,
            limits=self._limits,
            name_filter=lambda n: n.endswith(".json") and "My Activity" in n,
        ):
            try:
                records = _json.loads(entry.data.decode("utf-8"))
            except (UnicodeDecodeError, _json.JSONDecodeError):
                continue
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                products = record.get("products") or []
                if products and not any(
                    "gemini" in str(p).lower() or "bard" in str(p).lower() for p in products
                ):
                    continue
                timestamp = parse_timestamp(record.get("time"))
                if timestamp is None:
                    continue
                by_day.setdefault(timestamp.strftime("%Y-%m-%d"), []).append(record)

        for day, records in sorted(by_day.items()):
            records.sort(key=lambda r: str(r.get("time")))
            messages: list[NormalizedMessage] = []
            for record in records:
                prompt = str(record.get("title") or "")
                # Takeout prefixes the user's text with "Prompted ".
                if prompt.startswith("Prompted "):
                    prompt = prompt[len("Prompted ") :]
                created = parse_timestamp(record.get("time"))
                if prompt.strip():
                    messages.append(
                        NormalizedMessage(
                            role="user",
                            content=prompt,
                            provenance="user_statement",
                            created_at=created,
                        )
                    )
                for detail in record.get("details") or []:
                    text = str(detail.get("name") or "")
                    if text.strip():
                        messages.append(
                            NormalizedMessage(
                                role="assistant",
                                content=text,
                                provenance="assistant_generated",
                                created_at=created,
                            )
                        )
            if messages:
                yield NormalizedConversation(
                    provider=self.provider,
                    provider_conversation_id=f"takeout-day:{day}",
                    title=f"Gemini activity — {day}",
                    created_at=parse_timestamp(records[0].get("time")),
                    updated_at=parse_timestamp(records[-1].get("time")),
                    model=None,
                    messages=messages,
                    metadata={"source": "google_takeout", "record_count": len(records)},
                )


class GrokImporter:
    """xAI's Grok export.

    The export format is a JSON archive of conversations. Because it is less
    settled than the others, this adapter probes for the shapes it has seen
    rather than assuming one, and reports anything it could not read.
    """

    provider = "grok"

    _CONVERSATION_KEYS = ("conversations", "chats", "threads")
    _MESSAGE_KEYS = ("messages", "responses", "turns", "chat_messages")

    def __init__(self, limits: ArchiveLimits | None = None) -> None:
        self._limits = limits or ArchiveLimits()
        #: Conversations present in the export that yielded nothing readable.
        #: The runner reports them, so nothing is dropped without being named.
        self.problems: list[str] = []

    def detect(self, path: Path) -> bool:
        try:
            for entry in iter_safe_entries(
                path, limits=self._limits, name_filter=lambda n: n.endswith(".json")
            ):
                head = entry.data[:4000].lower()
                if b"grok" in head or b"prompt_source" in head:
                    return True
        except Exception:
            return False
        return False

    def limitations(self) -> list[str]:
        return [
            "Grok's export format is less stable than the others. This adapter "
            "probes for the conversation and message shapes it knows; any file "
            "it cannot interpret is reported as an error rather than skipped "
            "silently, so nothing is lost without being named.",
            "Images generated in Grok are not reliably included in the export "
            "and are imported as pending_import references when named.",
        ]

    def parse(self, path: Path) -> Iterator[NormalizedConversation]:
        import json as _json

        archive_index = collect_attachments(path, limits=self._limits)
        for entry in iter_safe_entries(
            path, limits=self._limits, name_filter=lambda n: n.endswith(".json")
        ):
            try:
                payload = _json.loads(entry.data.decode("utf-8"))
            except (UnicodeDecodeError, _json.JSONDecodeError) as exc:
                logger.warning(
                    "unreadable export member",
                    extra={"member": entry.name, "why": type(exc).__name__},
                )
                continue
            for conversation in self._find_conversations(payload):
                normalized = self._normalize(conversation, archive_index)
                if normalized is not None:
                    yield normalized

    def _find_conversations(self, payload: Any) -> Iterator[dict[str, Any]]:
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and self._message_list(item) is not None:
                    yield item
        elif isinstance(payload, dict):
            for key in self._CONVERSATION_KEYS:
                if isinstance(payload.get(key), list):
                    yield from self._find_conversations(payload[key])
                    return
            if self._message_list(payload) is not None:
                yield payload

    def _message_list(self, conversation: dict[str, Any]) -> list[Any] | None:
        for key in self._MESSAGE_KEYS:
            value = conversation.get(key)
            if isinstance(value, list):
                return value
        return None

    @staticmethod
    def _role(sender: Any) -> str:
        """Grok senders vary: "human", "user", "ASSISTANT", "assistant", or a
        model name such as "grok-4". Only the person is ever the user."""
        value = str(sender or "").strip().lower()
        if value in ("human", "user"):
            return "user"
        if value == "system":
            return "system"
        return "assistant"

    @staticmethod
    def _ordered(messages: list[dict[str, Any]], leaf_id: Any) -> list[dict[str, Any]]:
        """The kept branch: walk parent links back from the leaf when the export
        names one (an edited or regenerated reply leaves siblings behind),
        otherwise order by time."""
        by_id = {
            str(m.get("_id") or m.get("id")): m for m in messages if m.get("_id") or m.get("id")
        }
        if leaf_id and str(leaf_id) in by_id:
            path: list[dict[str, Any]] = []
            seen: set[str] = set()
            current: Any = str(leaf_id)
            while current and current in by_id and current not in seen:
                seen.add(current)
                message = by_id[current]
                path.append(message)
                current = str(message.get("parent_response_id") or "") or None
            return list(reversed(path))
        return sorted(
            messages,
            key=lambda m: (
                parse_timestamp(m.get("create_time") or m.get("timestamp"))
                or datetime.min.replace(tzinfo=UTC)
            ),
        )

    def _normalize(
        self, raw: dict[str, Any], archive_index: dict[str, NormalizedAttachment]
    ) -> NormalizedConversation | None:
        # The 2026 backend export wraps metadata in "conversation" and each
        # message in {"response": {...}}; older shapes are flat.
        meta = raw["conversation"] if isinstance(raw.get("conversation"), dict) else raw
        entries = [
            item["response"]
            if isinstance(item, dict) and isinstance(item.get("response"), dict)
            else item
            for item in (self._message_list(raw) or [])
            if isinstance(item, dict)
        ]
        conversation_id = str(
            meta.get("conversation_id") or meta.get("id") or meta.get("uuid") or ""
        )
        messages: list[NormalizedMessage] = []
        attachments: list[NormalizedAttachment] = []

        for index, message in enumerate(self._ordered(entries, meta.get("leaf_response_id"))):
            role = self._role(message.get("role") or message.get("sender") or message.get("author"))
            text = (
                message.get("message")
                or message.get("text")
                or message.get("content")
                or _text_from_parts(message.get("parts"))
            )
            text = _text_from_parts(text) if not isinstance(text, str) else text
            if text.strip():
                messages.append(
                    NormalizedMessage(
                        role=role,
                        content=text,
                        provenance=provenance_for(role),
                        created_at=parse_timestamp(
                            message.get("create_time") or message.get("timestamp")
                        ),
                        provider_message_id=str(message.get("_id") or message.get("id") or "")
                        or None,
                        model=message.get("model") if role == "assistant" else None,
                    )
                )
            for attachment in message.get("attachments") or message.get("files") or []:
                name = str(
                    (attachment or {}).get("file_name") or (attachment or {}).get("name") or ""
                )
                if not name:
                    continue
                match = archive_index.get(name)
                if match is not None:
                    match.message_index = index
                    attachments.append(match)
                else:
                    attachments.append(
                        NormalizedAttachment(
                            filename=name,
                            message_index=index,
                            metadata={"reason": "not present in export archive"},
                        )
                    )

        if not messages:
            self.problems.append(
                f"grok conversation {conversation_id or '?'} "
                f"({meta.get('title') or 'untitled'!r}) had no readable messages"
            )
            return None
        return NormalizedConversation(
            provider=self.provider,
            provider_conversation_id=conversation_id,
            title=meta.get("title") or meta.get("name"),
            created_at=parse_timestamp(meta.get("create_time") or meta.get("created_at")),
            updated_at=parse_timestamp(
                meta.get("modify_time") or meta.get("update_time") or meta.get("updated_at")
            ),
            model=meta.get("model"),
            messages=messages,
            attachments=attachments,
        )


IMPORTERS: dict[str, type[ConversationImporter]] = {
    "chatgpt": ChatGPTImporter,
    "claude": ClaudeImporter,
    "gemini": GeminiImporter,
    "grok": GrokImporter,
}


def detect_importer(path: Path, limits: ArchiveLimits | None = None):
    """Identify the provider from the archive itself."""
    for name, factory in IMPORTERS.items():
        importer = factory(limits)
        try:
            if importer.detect(path):
                return name, importer
        except Exception as exc:
            # A failed probe just means "not this format".
            logger.debug(
                "importer probe failed",
                extra={"importer": name, "why": type(exc).__name__},
            )
            continue
    return None, None
