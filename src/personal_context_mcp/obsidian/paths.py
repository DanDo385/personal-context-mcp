"""Vault path construction and validation.

Models never supply paths. They supply logical objects - a note class, a
project slug, a title - and this module turns those into a path under one of
the configured roots. Every path that reaches the worker has been through
:func:`validate_vault_path`, and the worker validates again on arrival: the
cloud service and the laptop do not trust each other's validation.

Rejected: ``..`` traversal, absolute paths, drive letters, UNC paths, NUL and
control characters, reserved Windows device names, leading dots (so nothing
can write ``.obsidian/`` config or a ``.git`` directory), symlink-shaped
segments, and any path whose first segment is not a configured root.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath

from personal_context_mcp.errors import PathSecurityError
from personal_context_mcp.util import safe_filename, slugify

_ALLOWED_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
_ALLOWED_SUFFIXES = frozenset(
    {
        ".md",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".json",
        ".txt",
        ".csv",
        ".svg",
        ".yml",
        ".yaml",
        ".py",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".mmd",
    }
)
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
MAX_PATH_LENGTH = 240
MAX_DEPTH = 8


def validate_vault_path(path: str, allowed_roots: dict[str, str]) -> str:
    """Return a normalised vault-relative path, or raise PathSecurityError."""
    if not path or not path.strip():
        raise PathSecurityError("Empty vault path.")
    if "\x00" in path or any(ord(c) < 32 for c in path):
        raise PathSecurityError("Vault path contains control characters.")
    if len(path) > MAX_PATH_LENGTH:
        raise PathSecurityError(f"Vault path exceeds {MAX_PATH_LENGTH} characters.")

    normalized = path.replace("\\", "/").strip("/")
    if normalized.startswith("~") or re.match(r"^[A-Za-z]:", normalized):
        raise PathSecurityError("Absolute or home-relative vault paths are not allowed.")
    if PurePosixPath(normalized).is_absolute():
        raise PathSecurityError("Absolute vault paths are not allowed.")

    # Resolve the path textually and confirm it did not climb out.
    resolved = posixpath.normpath(normalized)
    if resolved.startswith("..") or resolved.startswith("/") or resolved == ".":
        raise PathSecurityError(f"Vault path escapes the vault root: {path!r}")

    segments = resolved.split("/")
    if len(segments) > MAX_DEPTH:
        raise PathSecurityError(f"Vault path is deeper than {MAX_DEPTH} levels.")

    for segment in segments:
        if segment in ("", ".", ".."):
            raise PathSecurityError(f"Invalid path segment in {path!r}")
        if segment.startswith("."):
            raise PathSecurityError("Vault paths may not contain dot-directories or dotfiles.")
        if not _ALLOWED_SEGMENT.match(segment):
            raise PathSecurityError(f"Disallowed characters in path segment {segment!r}")
        if segment.split(".")[0].lower() in _RESERVED:
            raise PathSecurityError(f"Reserved name in path: {segment!r}")

    if segments[0] not in set(allowed_roots.values()):
        raise PathSecurityError(
            f"{segments[0]!r} is not a configured vault root. Allowed roots: "
            + ", ".join(sorted(allowed_roots.values()))
        )

    suffix = PurePosixPath(resolved).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise PathSecurityError(
            f"Vault writes are not allowed for {suffix or 'extensionless'} files."
        )

    return resolved


def resolve_in_vault(vault_root: Path, relative_path: str, allowed_roots: dict[str, str]) -> Path:
    """Resolve a validated vault-relative path to a real filesystem path.

    Used by the worker. Re-validates, then resolves symlinks and confirms the
    result is still inside the vault - a symlink planted inside the vault must
    not become a write outside it.
    """
    validated = validate_vault_path(relative_path, allowed_roots)
    root = vault_root.resolve(strict=True)
    target = (root / validated).resolve()
    if not target.is_relative_to(root):
        raise PathSecurityError(f"Resolved path escapes the vault: {relative_path!r}")
    # Any existing symlink anywhere along the chain is refused outright.
    probe = root
    for segment in PurePosixPath(validated).parts:
        probe = probe / segment
        if probe.is_symlink():
            raise PathSecurityError(f"Refusing to follow a symlink at {segment!r}")
    return target


class VaultPaths:
    """Builds paths from logical objects. The only way a path is created."""

    def __init__(self, roots: dict[str, str]) -> None:
        self._roots = roots

    @property
    def roots(self) -> dict[str, str]:
        return dict(self._roots)

    def _root(self, key: str) -> str:
        return self._roots.get(key, self._roots.get("inbox", "00-Inbox"))

    def note(self, *, note_class: str, title: str, project_slug: str | None = None) -> str:
        if note_class == "session_index":
            return self.session_index(title)
        filename = f"{safe_filename(slugify(title) or 'note')}.md"
        if note_class == "project" and project_slug:
            path = f"{self._root('project')}/{slugify(project_slug)}/{filename}"
        elif note_class == "daily":
            path = f"{self._root('daily')}/{filename}"
        else:
            path = f"{self._root(note_class)}/{filename}"
        return validate_vault_path(path, self._roots)

    def home_note(self, home: str, title: str, *, is_list: bool = False) -> str:
        """Map logical homes onto the existing configured roots."""
        from personal_context_mcp.homes import home_metadata

        project = home.removeprefix("projects/") if home.startswith("projects/") else None
        home_metadata(home, project)
        kind, _, name = home.partition("/")
        base = {
            "projects": f"{self._root('project')}/{name}",
            "lesson": f"{self._root('research')}/Lessons/{name}",
            "setup": f"{self._root('resource')}/Setup/{name}",
            "other": self._root("inbox"),
        }[kind]
        middle = "/Lists" if is_list else ""
        return validate_vault_path(
            f"{base}{middle}/{safe_filename(slugify(title) or 'note')}.md", self._roots
        )

    def moc(self, title: str) -> str:
        """Maps of Content live together under the atlas root."""
        filename = f"{safe_filename(slugify(title) or 'map')}.md"
        return validate_vault_path(f"{self._root('atlas')}/{filename}", self._roots)

    def session_index(self, title: str) -> str:
        """One provider index, kept with the maps under the atlas root."""
        filename = f"{safe_filename(slugify(title) or 'index')}.md"
        return validate_vault_path(f"{self._root('atlas')}/{filename}", self._roots)

    def project_readme(self, project_slug: str) -> str:
        return validate_vault_path(
            f"{self._root('project')}/{slugify(project_slug)}/project.md", self._roots
        )

    def list_view(self, list_slug: str, *, project_slug: str | None = None) -> str:
        filename = f"{safe_filename(slugify(list_slug))}.md"
        if project_slug:
            path = f"{self._root('project')}/{slugify(project_slug)}/Lists/{filename}"
        else:
            path = f"{self._root('resource')}/Lists/{filename}"
        return validate_vault_path(path, self._roots)

    def artifact_note(self, artifact_slug: str, *, project_slug: str | None = None) -> str:
        slug = slugify(artifact_slug)
        if project_slug:
            path = f"{self._root('project')}/{slugify(project_slug)}/Artifacts/{slug}/README.md"
        else:
            path = f"{self._root('resource')}/Artifacts/{slug}/README.md"
        return validate_vault_path(path, self._roots)

    def artifact_file(
        self, artifact_slug: str, relative_path: str, *, project_slug: str | None = None
    ) -> str:
        """Mirror path for one file of an artifact.

        The artifact's own relative path is sanitised segment by segment; a
        bundle claiming ``../../.ssh/authorized_keys`` produces a harmless
        flattened name rather than an escape.
        """
        slug = slugify(artifact_slug)
        safe_parts = [
            safe_filename(part)
            for part in relative_path.replace("\\", "/").split("/")
            if part not in ("", ".", "..")
        ]
        if not safe_parts:
            raise PathSecurityError(
                f"Artifact file path is empty after sanitising: {relative_path!r}"
            )
        tail = "/".join(safe_parts)
        base = (
            f"{self._root('project')}/{slugify(project_slug)}/Artifacts/{slug}"
            if project_slug
            else f"{self._root('resource')}/Artifacts/{slug}"
        )
        return validate_vault_path(f"{base}/{tail}", self._roots)
