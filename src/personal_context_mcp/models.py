"""Shared domain types.

These are the shapes that cross the MCP boundary. Field descriptions matter:
they become the tool schema the model reads, so they are written as
instructions to a caller rather than as notes to a maintainer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Provenance = Literal[
    "user_statement",
    "assistant_generated",
    "tool_result",
    "external_source",
    "imported_metadata",
    "system",
]
Sensitivity = Literal["normal", "private", "highly_sensitive"]
ProviderId = Literal["chatgpt", "claude", "gemini", "grok", "system"]
StorageStatus = Literal["captured", "referenced", "pending_import", "failed"]

MemoryKind = Literal["fact", "preference", "relationship", "decision", "knowledge"]
NoteClass = Literal[
    "project",
    "research",
    "wiki",
    "concept",
    "resource",
    "daily",
    "inbox",
    "session_index",
]
ListType = Literal["task", "reading", "shopping", "idea", "people", "general"]
ItemStatus = Literal["open", "done", "dropped", "blocked"]
ArtifactType = Literal[
    "web_project",
    "code_project",
    "document",
    "dataset",
    "image",
    "video",
    "audio",
    "presentation",
    "spreadsheet",
    "archive",
    "notebook",
    "diagram",
    "other",
]
ArtifactSourceType = Literal[
    "uploaded_by_user", "generated_by_model", "imported", "external_reference"
]


class Base(BaseModel):
    # populate_by_name lets a field carry a caller-facing alias (e.g. "list")
    # while keeping a Python-safe attribute name.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ------------------------------------------------------------------ results


class Provenance_(Base):
    """Where a returned piece of context came from.

    Always attached to retrieval results so the reading model can tell your
    own words apart from another model's conclusions.
    """

    kind: Provenance
    provider: str | None = None
    conversation_id: UUID | None = None
    recorded_at: datetime | None = None
    confidence: float | None = None


class ContextItem(Base):
    """One retrieved item in a context package."""

    type: Literal[
        "memory",
        "summary",
        "decision",
        "project",
        "task",
        "list_item",
        "note",
        "message",
        "artifact",
    ]
    id: UUID
    title: str | None = None
    content: str
    score: float | None = None
    project: str | None = None
    provenance: Provenance_
    estimated_tokens: int = 0
    #: When it was decided, or last updated. Recency is ranked on this.
    effective_at: datetime | None = None
    #: Which bootstrap layer returned it: handoff, relevant or raw.
    layer: str | None = None
    #: Decisions: "active", or "superseded" with the id that replaced it.
    status: str | None = None
    superseded_by: UUID | None = None
    #: Newer items on the same subject that may overrule this one. Ask the user.
    conflicts_with: list[UUID] = Field(default_factory=list)
    #: Session summaries: questions left open in that session.
    open_questions: list[str] = Field(default_factory=list)
    #: Why this item is here when that is not obvious (e.g. an untagged session).
    reason: str | None = None


class ProjectCandidate(Base):
    slug: str
    name: str
    reason: str


class ProjectChoice(Base):
    """Which project the package is about, and how that was decided."""

    slug: str | None = None
    name: str | None = None
    #: given (the caller passed it), inferred (read from the query), none.
    source: Literal["given", "inferred", "none"]
    reason: str
    #: Set when the query could mean more than one project.
    candidates: list[ProjectCandidate] = Field(default_factory=list)


class LayerReport(Base):
    name: str
    status: Literal["ran", "skipped"]
    reason: str
    items: int = 0


class ConflictReport(Base):
    """Two items on the same subject where the newer may overrule the older."""

    kind: Literal["superseded", "possible_conflict"]
    #: Older first, newer second.
    ids: list[UUID]
    older: str
    newer: str
    newer_provenance: str
    advice: str


class ContextPackage(Base):
    """Compact package returned by context_bootstrap / context_for_topic."""

    query: str
    items: list[ContextItem] = Field(default_factory=list)
    token_budget: int
    estimated_tokens: int
    layers_used: list[str] = Field(default_factory=list)
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)
    #: The search terms actually used, after dropping filler words.
    keywords: list[str] = Field(default_factory=list)
    project: ProjectChoice | None = None
    #: Every layer, including the ones skipped, with the reason.
    layers: list[LayerReport] = Field(default_factory=list)
    conflicts: list[ConflictReport] = Field(default_factory=list)


class MemoryRecord(Base):
    id: UUID
    content: str
    kind: MemoryKind
    subject: str | None
    project: str | None
    importance: float
    status: str
    provenance: Provenance_
    created_at: datetime
    updated_at: datetime


class MemorySaveResult(Base):
    id: UUID
    outcome: Literal["created", "updated", "superseded", "duplicate"]
    reason: str
    superseded_id: UUID | None = None


class ProjectRecord(Base):
    id: UUID
    slug: str
    name: str
    description: str | None = None
    status: str
    goals: str | None = None
    last_active_at: datetime
    open_task_count: int = 0
    decision_count: int = 0


class ListRecord(Base):
    id: UUID
    slug: str
    name: str
    description: str | None = None
    list_type: ListType
    project: str | None = None
    status: str
    open_items: int = 0
    total_items: int = 0


class ListItemRecord(Base):
    id: UUID
    list_id: UUID
    list_slug: str
    text: str
    status: ItemStatus
    priority: int
    position: int
    due_date: date | None = None
    notes: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class DecisionRecord(Base):
    id: UUID
    statement: str
    rationale: str | None
    project: str | None
    status: str
    decided_at: datetime
    provenance: Provenance_


class NoteRecord(Base):
    id: UUID
    slug: str
    title: str
    note_class: NoteClass
    project: str | None
    vault_path: str | None
    version: int
    updated_at: datetime
    content: str | None = None


class ArtifactFileRecord(Base):
    relative_path: str
    mime_type: str
    byte_size: int
    sha256: str
    storage_status: StorageStatus
    original_filename: str | None = None


class ArtifactRecord(Base):
    id: UUID
    slug: str
    title: str
    description: str | None
    artifact_type: ArtifactType
    source_type: ArtifactSourceType
    source_provider: str | None
    project: str | None
    storage_status: StorageStatus
    classification_status: str
    version: int
    file_count: int
    total_bytes: int
    created_at: datetime
    files: list[ArtifactFileRecord] = Field(default_factory=list)
    #: Present only when storage_status == "captured"; never invented.
    capture_note: str | None = None


# ------------------------------------------------------- checkpoint payloads


class CheckpointDecision(Base):
    statement: str = Field(description="The decision, stated in one sentence.")
    rationale: str | None = Field(
        default=None, description="Why it was decided, if it was discussed."
    )
    project: str | None = Field(
        default=None, description="Project slug or name this decision belongs to."
    )
    provenance: Literal["user_statement", "assistant_generated"] = Field(
        default="user_statement",
        description=(
            "user_statement when the user made or confirmed the decision; "
            "assistant_generated for your own recommendation the user has not confirmed."
        ),
    )


class CheckpointFact(Base):
    content: str = Field(
        description=(
            "A durable fact worth remembering in future sessions. State it "
            "self-containedly - it will be read without this conversation."
        )
    )
    kind: MemoryKind = "fact"
    subject: str | None = Field(
        default=None,
        description=(
            "Short handle for the topic or entity this is about, used to "
            "deduplicate against existing memories (e.g. 'obsidian-vault')."
        ),
    )
    provenance: Provenance = Field(
        default="user_statement",
        description=(
            "user_statement for something the user told you; "
            "assistant_generated for your own conclusion; "
            "external_source for something you looked up."
        ),
    )
    project: str | None = None
    importance: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Leave unset to let the classifier decide."
    )
    sensitivity: Sensitivity = "normal"


class CheckpointTask(Base):
    text: str = Field(description="A concrete unfinished action.")
    # Exposed to callers as "list"; the Python attribute cannot be named `list`
    # because that shadows the builtin when sibling annotations are resolved.
    list_ref: str | None = Field(
        default=None,
        alias="list",
        description="Target list slug or name. Defaults to the project's task list.",
    )
    project: str | None = None
    due_date: date | None = None
    priority: int = 0


class CheckpointListUpdate(Base):
    list_ref: str = Field(
        alias="list",
        description="List slug or name. Search before inventing a new one.",
    )
    add: list[str] = Field(default_factory=list)
    complete: list[str] = Field(
        default_factory=list, description="Item ids, or exact item text, to mark done."
    )
    remove: list[str] = Field(default_factory=list)


class CheckpointProjectUpdate(Base):
    project: str = Field(description="Project slug or name.")
    status: str | None = None
    goals: str | None = None
    event: str | None = Field(default=None, description="One line describing what changed.")


class CheckpointArtifactRef(Base):
    artifact_id: UUID | None = None
    title: str | None = None
    relation: str = "related_to_conversation"


class CheckpointResult(Base):
    conversation_id: UUID
    session_summary_id: UUID | None
    created: dict[str, int]
    updated: dict[str, int]
    skipped: dict[str, int]
    classification: dict[str, Any] | None = None
    queued_vault_jobs: int = 0
    review_items: int = 0
    notes: list[str] = Field(default_factory=list)


Slug = Annotated[str, Field(min_length=1, max_length=120)]
