"""MCP tool, prompt and resource registration."""

from __future__ import annotations

from fastmcp import FastMCP

from personal_context.mcp import (
    prompts,
    tools_artifacts,
    tools_context,
    tools_lists,
    tools_memory,
    tools_projects,
    tools_review,
)


def register_all(mcp: FastMCP) -> None:
    tools_context.register(mcp)
    tools_memory.register(mcp)
    tools_projects.register(mcp)
    tools_lists.register(mcp)
    tools_artifacts.register(mcp)
    tools_review.register(mcp)
    prompts.register(mcp)


__all__ = ["register_all"]
