"""One logical home and concept tags, independent of vault folder names."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import Field

from personal_context_mcp.errors import ValidationError
from personal_context_mcp.util import slugify

HOME_PATTERN = r"^(other|(projects|lesson|setup)/[a-z0-9][a-z0-9-]{0,79})$"
Home = Annotated[str, Field(pattern=HOME_PATTERN)]


def home_metadata(
    home: str | None, project_slug: str | None, concepts: list[str] | None = None
) -> dict:
    """Validate caller-selected semantics; code never guesses a lesson or tool."""
    chosen = home or (f"projects/{project_slug}" if project_slug else "other")
    if not re.fullmatch(HOME_PATTERN, chosen):
        raise ValidationError(
            "Home must be projects/{slug}, lesson/{category}, setup/{tool}, or other."
        )
    if chosen.startswith("projects/") and chosen != f"projects/{project_slug}":
        raise ValidationError(
            "A project home must match an existing project. Use project_search first."
        )
    tags = []
    for concept in concepts or []:
        name = slugify(concept.removeprefix("concept/"))
        if not name or len(name) > 80:
            raise ValidationError(
                "Concept names must contain letters or numbers and fit in 80 characters."
            )
        tags.append(f"concept/{name}")
    return {"home": chosen, "tags": [chosen, *sorted(set(tags))]}
