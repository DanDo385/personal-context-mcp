"""Object-key generation.

Keys are ALWAYS produced here, from structured inputs. No model, classifier or
provider export ever supplies a storage path. Filenames coming from outside
are sanitised segment by segment, and the original name is preserved in
metadata rather than in the key.

Layout:

    projects/<project>/artifacts/<artifact>-v<n>/<relative path>
    library/<category>/<yyyy>/<mm>/<artifact>-v<n>/<relative path>
    archives/<provider>/<yyyy>/<mm>/<sha12>-<filename>
"""

from __future__ import annotations

from datetime import datetime

from personal_context.errors import PathSecurityError
from personal_context.util import safe_filename, slugify, utcnow

#: Semantic category -> library prefix. The classifier chooses a category
#: name from a fixed set; this table turns it into a path. Jev never returns
#: a path, and an unknown category lands in `inbox` rather than anywhere else.
LIBRARY_PREFIX: dict[str, str] = {
    "research_asset": "research-assets",
    "reference": "reference",
    "generated_media": "images",
    "uploaded_media": "images",
    "code_project": "code",
    "document": "documents",
    "dataset": "data",
    "presentation": "documents",
    "spreadsheet": "data",
    "archive": "archives",
    "project_artifact": "inbox",
    "other": "inbox",
}

_MEDIA_BY_TYPE: dict[str, str] = {
    "image": "images",
    "video": "video",
    "audio": "audio",
    "document": "documents",
    "dataset": "data",
    "spreadsheet": "data",
    "presentation": "documents",
    "notebook": "code",
    "code_project": "code",
    "web_project": "code",
    "diagram": "research-assets",
    "archive": "archives",
}

MAX_RELATIVE_DEPTH = 10


def sanitize_relative_path(relative_path: str) -> str:
    """Reduce a supplied relative path to a safe one, preserving structure.

    ``src/main.js`` stays ``src/main.js``. ``../../etc/passwd`` becomes
    ``etc/passwd`` - the traversal is removed, not honoured. An empty result
    is an error rather than a silent default.
    """
    raw = (relative_path or "").replace("\\", "/")
    if "\x00" in raw:
        raise PathSecurityError("Null byte in artifact path.")

    parts = [p for p in raw.split("/") if p not in ("", ".", "..")]
    if not parts:
        raise PathSecurityError(f"Artifact path is empty after sanitising: {relative_path!r}")
    if len(parts) > MAX_RELATIVE_DEPTH:
        raise PathSecurityError(
            f"Artifact path is deeper than {MAX_RELATIVE_DEPTH} levels: {relative_path!r}"
        )

    safe_parts = [safe_filename(p) for p in parts]
    result = "/".join(safe_parts)
    if result.startswith("/") or ".." in safe_parts:
        raise PathSecurityError(f"Unsafe artifact path: {relative_path!r}")
    return result


def artifact_prefix(
    *,
    artifact_slug: str,
    version: int,
    project_slug: str | None = None,
    semantic_category: str | None = None,
    artifact_type: str | None = None,
    when: datetime | None = None,
) -> str:
    """Storage prefix for one version of one artifact."""
    slug = slugify(artifact_slug, fallback="artifact")
    folder = f"{slug}-v{max(1, int(version))}"

    if project_slug:
        return f"projects/{slugify(project_slug)}/artifacts/{folder}"

    category = LIBRARY_PREFIX.get(
        semantic_category or "", _MEDIA_BY_TYPE.get(artifact_type or "", "inbox")
    )
    stamp = when or utcnow()
    return f"library/{category}/{stamp:%Y/%m}/{folder}"


def object_key(prefix: str, relative_path: str) -> str:
    return f"{prefix.rstrip('/')}/{sanitize_relative_path(relative_path)}"


def archive_key(*, provider: str, filename: str, sha256: str, when: datetime | None = None) -> str:
    """Cold-storage key for an original provider export archive."""
    stamp = when or utcnow()
    return (
        f"archives/{slugify(provider, fallback='unknown')}/{stamp:%Y/%m}"
        f"/{sha256[:12]}-{safe_filename(filename)}"
    )
