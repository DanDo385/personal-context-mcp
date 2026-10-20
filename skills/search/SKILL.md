---
name: search
description: Search shared memory when the user says "search my memory for X", "find what we said about X", or asks to recall a specific topic across chats.
---

Use the Personal Context MCP `context_search` tool with a short query you choose:
names, projects and topic terms. Never paste the user's prompt or transcript.
Supply a project filter only when known. Start with five results and summaries;
request raw message excerpts only when those do not answer the question.

Report what was found with provenance. Distinguish the user's statements from
assistant conclusions, and surface conflicts. A search request does not authorize
saving, emailing or writing to the vault. If nothing matches, say so.
