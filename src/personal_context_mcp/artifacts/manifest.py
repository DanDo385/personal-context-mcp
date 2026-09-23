"""Artifact manifests.

Generated in Python from the stored rows. A manifest is the record of what
was actually captured, so it never contains a field we did not verify: every
listed file has a hash we computed from bytes we held.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from personal_context_mcp.util import utcnow

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"


def build_manifest(
    *,
    artifact_id: UUID,
    slug: str,
    title: str,
    artifact_type: str,
    source_provider: str | None,
    source_conversation_id: UUID | None,
    project_slug: str | None,
    version: int,
    storage_status: str,
    storage_prefix: str | None,
    files: list[dict[str, Any]],
    description: str | None = None,
) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "artifact_id": str(artifact_id),
        "slug": slug,
        "title": title,
        "description": description,
        "project": project_slug,
        "source_provider": source_provider,
        "source_conversation": str(source_conversation_id) if source_conversation_id else None,
        "type": artifact_type,
        "version": version,
        "storage_status": storage_status,
        "storage_prefix": storage_prefix,
        "generated_at": utcnow().isoformat(),
        "file_count": len(files),
        "total_bytes": sum(int(f.get("byte_size") or 0) for f in files),
        "files": [
            {
                "path": f["relative_path"],
                "sha256": f["sha256"],
                "mime": f["mime_type"],
                "bytes": int(f.get("byte_size") or 0),
                "storage_status": f.get("storage_status", "captured"),
                "original_filename": f.get("original_filename"),
            }
            for f in sorted(files, key=lambda f: f["relative_path"])
        ],
    }
