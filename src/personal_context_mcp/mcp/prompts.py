"""MCP prompts and resources.

These carry the long-form workflow guidance that must NOT live in a permanent
custom instruction. Gemini in particular has no Skills mechanism for ordinary
chats, so the procedures are exposed here where a model can pull them on
demand instead of paying for them on every turn.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP

from personal_context_mcp.services import get_services


def _skills_dir() -> Path | None:
    for candidate in (
        Path(__file__).resolve().parents[3] / "skills",
        Path("/app/skills"),
        Path.cwd() / "skills",
    ):
        if candidate.is_dir():
            return candidate
    return None


def register(mcp: FastMCP) -> None:
    # The same short workflow is used by app Skills and slash-command clients.
    def action_guide(name: str) -> str:
        directory = _skills_dir()
        if directory is None:
            raise RuntimeError("Workflow skills are missing from this deployment.")
        return (directory / name / "SKILL.md").read_text(encoding="utf-8")

    @mcp.prompt(name="search")
    def search() -> str:
        """Search shared memory for a topic named by the user."""
        return action_guide("search")

    @mcp.prompt(name="save")
    def save() -> str:
        """Save this session with one home and its concept tags."""
        return action_guide("save")

    @mcp.prompt(name="email")
    def email() -> str:
        """Draft an email, show it, and send only after confirmation."""
        return action_guide("email")

    @mcp.prompt(name="vault")
    def vault() -> str:
        """Queue a note, summary or two-way list for Obsidian."""
        return action_guide("vault")

    @mcp.prompt(name="recall_context")
    def recall_context(topic: str = "") -> str:
        """How to retrieve shared context before answering."""
        return (
            "Before answering, decide whether the request could depend on earlier "
            "work: a previous conversation, a project, a decision, a preference, "
            "research, a list, a person, or an unfinished task.\n\n"
            "If it could:\n"
            "1. Call context_bootstrap once with a short query of key terms you "
            "choose (names, projects, topics"
            + (f", e.g. '{topic}'" if topic else "")
            + ") and the project if you know it. Never paste the user's message.\n"
            "2. Only if that did not cover it, call context_search with sharper "
            "terms.\n"
            "3. Prefer memories, summaries and notes over raw messages.\n"
            "4. Read each item's provenance. 'user_statement' is something the "
            "user told us; 'assistant_generated' is a model's own conclusion and "
            "should be treated as a hypothesis, not a fact. If the package lists "
            "conflicts, ask the user which one stands.\n"
            "5. Say what you found rather than silently assuming it.\n\n"
            "If the request is trivially self-contained, skip retrieval entirely."
        )

    @mcp.prompt(name="checkpoint_session")
    def checkpoint_session() -> str:
        """How and when to checkpoint durable state."""
        return (
            "Checkpoint when the conversation has established something that "
            "should outlive it. Call session_checkpoint ONCE with everything:\n\n"
            "- summary: a few sentences written for a future session that cannot "
            "see this conversation\n"
            "- project: the project this session was about, when there is one\n"
            "- decisions: things settled, one sentence each\n"
            "- facts: durable, self-contained statements, with accurate provenance\n"
            "- tasks: concrete unfinished actions\n"
            "- list_updates: additions and completions on existing lists - call "
            "list_search first to find their real names\n"
            "- project_updates: status or goal changes\n"
            "- open_questions: what is still unresolved\n"
            "- resolved_questions: earlier open questions this conversation answered\n\n"
            "- home: one of projects/{existing-project-slug}, lesson/{category}, setup/{tool}, other\n"
            "- concepts: covered concept names, stored as concept/{name} tags\n"
            "- queue_for_vault=false for shared-memory saving; note_write handles explicit vault requests\n\n"
            "Do not checkpoint small talk, a resolved lookup, or anything you "
            "already checkpointed in this conversation. Repeats are deduplicated, "
            "but the call still costs a round trip."
        )

    @mcp.prompt(name="capture_artifacts")
    def capture_artifacts() -> str:
        """How to capture files honestly."""
        return (
            "When a file worth keeping appears:\n\n"
            "1. Decide whether you actually have its contents.\n"
            "   - Text or code you generated: yes.\n"
            "   - A file the user uploaded that you can only see described: "
            "usually NO. Most native apps do not pass attachment bytes to tools.\n"
            "2. If you have the contents:\n"
            "   - one file -> artifact_save_text\n"
            "   - several files of one project -> artifact_save_bundle, ONE call, "
            "with the directory structure intact\n"
            "   - a change to something already saved -> artifact_create_revision\n"
            "   - small binary you truly hold -> artifact_save_binary\n"
            "   - anything large -> artifact_begin_upload, then "
            "artifact_complete_upload\n"
            "3. If you do NOT have the contents: artifact_register_reference. "
            "Then tell the user plainly that the details were recorded but the "
            "file itself was not captured.\n\n"
            "Never say 'saved' or 'archived' unless the tool result says "
            "capture_status is 'captured'. Reconstructing bytes you cannot read "
            "is worse than recording a reference."
        )

    @mcp.prompt(name="manage_lists")
    def manage_lists() -> str:
        """How to work with the user's lists."""
        return (
            "The user keeps many lists and they are canonical state, not prose.\n\n"
            "1. Call list_search FIRST. The name in conversation rarely matches "
            "the stored name - 'my reading list' may be 'Books to read'.\n"
            "2. Add with list_add_item to the list you found.\n"
            "3. Only call list_create when nothing existing is a reasonable home, "
            "and say that you are creating it.\n"
            "4. To complete or move an item, use the item id from list_get. "
            "Matching on text can hit the wrong item.\n"
            "5. Never answer a list question by writing out a Markdown list from "
            "memory - read the real one."
        )

    @mcp.resource("personal-context-mcp://guide/{name}", mime_type="text/markdown")
    def skill_guide(name: str) -> str:
        """Full text of a workflow guide, for clients without Skills support."""
        directory = _skills_dir()
        if directory is None:
            return "No guides are bundled with this deployment."
        candidate = directory / name / "SKILL.md"
        # `name` comes from the URI; resolve and confirm it stayed inside.
        try:
            resolved = candidate.resolve()
            if not resolved.is_relative_to(directory.resolve()) or not resolved.is_file():
                return f"No guide named {name!r}."
        except OSError:
            return f"No guide named {name!r}."
        return resolved.read_text(encoding="utf-8")

    @mcp.resource("personal-context-mcp://status", mime_type="application/json")
    async def status() -> dict:
        """Current shape of the user's context: counts, not content."""
        services = get_services()
        row = await services.db.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM memories WHERE status = 'active') AS memories,
              (SELECT count(*) FROM conversations) AS conversations,
              (SELECT count(*) FROM session_summaries) AS summaries,
              (SELECT count(*) FROM projects WHERE status = 'active') AS active_projects,
              (SELECT count(*) FROM lists WHERE status = 'active') AS lists,
              (SELECT count(*) FROM list_items WHERE status = 'open') AS open_items,
              (SELECT count(*) FROM notes) AS notes,
              (SELECT count(*) FROM artifacts) AS artifacts,
              (SELECT count(*) FROM artifacts WHERE storage_status = 'captured')
                  AS artifacts_captured,
              (SELECT count(*) FROM review_queue WHERE status = 'open') AS open_reviews,
              (SELECT count(*) FROM sync_jobs
                WHERE status IN ('queued','approved','leased')) AS pending_vault_writes
            """
        )
        return dict(row) if row else {}
