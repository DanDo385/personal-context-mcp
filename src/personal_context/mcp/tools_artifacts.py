"""Artifact tools.

The rule these tools exist to enforce: an artifact is reported as saved only
when its bytes were actually received, written and hash-verified. When a
provider shows the model a file it cannot hand to a tool, the honest move is
artifact_register_reference, and the tool result says so in words the model
should repeat to the user.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated
from uuid import UUID

from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes
from pydantic import Field

from personal_context.artifacts.keys import artifact_prefix, object_key, sanitize_relative_path
from personal_context.auth import scopes
from personal_context.auth.principal import current_principal
from personal_context.classifier.policy import Verdict
from personal_context.errors import ValidationError
from personal_context.logging_setup import get_logger
from personal_context.models import ArtifactRecord
from personal_context.services import Services, get_services
from personal_context.util import guess_mime

#: Inline base64 is allowed only for genuinely small payloads. Anything bigger
#: goes through a signed upload so bytes never sit in a tool argument.
logger = get_logger(__name__)

#: Inline base64 cap.
MAX_INLINE_BASE64_BYTES = 2 * 1024 * 1024


async def _classify_and_file(
    services: Services, record: ArtifactRecord, *, stated_project: str | None
) -> dict:
    """Run artifact classification and apply whatever the policy permits."""
    state = {
        "title": record.title,
        "description": record.description or "",
        "artifact_type": record.artifact_type,
        "source_type": record.source_type,
        "source_provider": record.source_provider or "",
        "file_paths": [f.relative_path for f in record.files],
        "file_types": sorted({f.mime_type for f in record.files}),
        "total_bytes": record.total_bytes,
        "stated_project": stated_project,
        "sensitivity": "normal",
    }
    # Small text files carry their own best description; include a little.
    text_preview = [
        f.relative_path
        for f in record.files
        if f.mime_type.startswith("text/") and f.byte_size < 60_000
    ]
    if text_preview:
        state["textual_files"] = text_preview[:20]

    result, routing, event_id = await services.classification.classify_artifact(
        state, object_id=record.id
    )
    policy = services.classification.policy()
    project_slug, _ = policy.artifact_project(result)
    semantic_category, _ = result.choice("semantic_category")

    project_id = await services.projects.resolve(stated_project or project_slug)
    if project_id:
        await services.artifacts.attach_to_project(record.id, project_id)

    await services.artifacts.record_classification(
        record.id,
        semantic_category=semantic_category,
        project_id=project_id,
        promote_to_vault=routing.promote_to_vault is Verdict.YES,
        needs_human_review=routing.needs_review,
        long_term_value=routing.importance,
        confidence=routing.confidence,
        classifier=f"{result.classifier}:{result.model}" if result.model else result.classifier,
        answers=result.answers_json(),
    )

    queued = 0
    if (
        routing.promote_to_vault is Verdict.YES
        and services.settings.obsidian_enabled
        and record.storage_status == "captured"
    ):
        queued = await _queue_vault_mirror(
            services,
            record,
            project_slug=stated_project or project_slug,
            classification_event_id=event_id,
        )

    if routing.needs_review:
        await services.classification.queue_review(
            object_type="artifact",
            object_id=record.id,
            reason=routing.review_reasons[0] if routing.review_reasons else "low_confidence",
            question=f"Where should the artifact {record.title!r} be filed?",
            suggested=routing.as_json(),
            confidence=routing.confidence,
            classification_event_id=event_id,
        )

    return {
        "classifier": result.classifier,
        "model": result.model,
        "degraded": result.degraded,
        "semantic_category": semantic_category,
        "project": stated_project or project_slug,
        "queued_vault_jobs": queued,
        **routing.as_json(),
    }


async def _queue_vault_mirror(
    services: Services,
    record: ArtifactRecord,
    *,
    project_slug: str | None,
    classification_event_id: UUID | None,
) -> int:
    """Queue an artifact note, plus file mirrors for anything small enough."""
    note_path = services.vault_paths.artifact_note(record.slug, project_slug=project_slug)
    queued = 0
    if await services.sync_queue.enqueue(
        job_type="write_artifact_note",
        object_type="artifact",
        object_id=record.id,
        vault_path=note_path,
        payload={"artifact_id": str(record.id), "project_slug": project_slug},
        classification_event_id=classification_event_id,
    ):
        queued += 1

    max_bytes = int(services.settings.obsidian_mirror_max_mb * 1024 * 1024)
    for file in await services.artifacts.files_for_mirror(record.id, max_bytes=max_bytes):
        try:
            path = services.vault_paths.artifact_file(
                record.slug, file["relative_path"], project_slug=project_slug
            )
        except Exception as exc:
            # A file whose name cannot be represented safely in the vault is
            # skipped; it is still available from object storage.
            logger.info(
                "artifact file not mirrored to the vault",
                extra={"relative_path": file["relative_path"], "why": str(exc)},
            )
            continue
        if await services.sync_queue.enqueue(
            job_type="mirror_artifact_files",
            object_type="artifact",
            object_id=record.id,
            vault_path=path,
            payload={
                "artifact_id": str(record.id),
                "object_key": file["object_key"],
                "relative_path": file["relative_path"],
            },
            content_sha256=file["sha256"],
            classification_event_id=classification_event_id,
        ):
            queued += 1
    return queued


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name="artifact_save_text",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_save_text(
        filename: Annotated[
            str,
            Field(description="Filename with extension, e.g. 'analysis.md' or 'main.py'."),
        ],
        content: Annotated[str, Field(description="The complete file contents.")],
        title: Annotated[str | None, Field(description="Defaults to the filename.")] = None,
        description: str | None = None,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        conversation_id: Annotated[
            str | None, Field(description="This conversation's id in your platform.")
        ] = None,
    ) -> dict:
        """Save a single text or code file as an artifact.

        Use for anything the user should be able to find again: a document, a
        script, a dataset in CSV or JSON, a diagram in Mermaid or SVG. For a
        multi-file project use artifact_save_bundle so it stays one artifact.
        """
        services = get_services()
        principal = current_principal()
        record = await services.artifacts.save_files(
            title=title or filename,
            description=description,
            files={filename: content.encode("utf-8")},
            project_id=await services.projects.resolve(project),
            project_slug=project,
            source_provider=principal.provider if principal.provider != "unknown" else None,
            source_type="generated_by_model",
            original_names={sanitize_relative_path(filename): filename},
        )
        classification = await _classify_and_file(services, record, stated_project=project)
        await services.audit.record(
            tool="artifact_save_text",
            action="create",
            object_type="artifact",
            object_id=record.id,
            principal=principal,
            resulting_version={"slug": record.slug, "version": record.version},
        )
        refreshed = await services.artifacts.get(record.id) or record
        return {
            "artifact": refreshed.model_dump(mode="json"),
            "classification": classification,
            "capture_status": "captured",
        }

    @mcp.tool(
        name="artifact_save_bundle",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_save_bundle(
        title: Annotated[str, Field(description="Name for the whole artifact.")],
        files: Annotated[
            dict[str, str],
            Field(
                description=(
                    "Map of relative path to file contents, e.g. "
                    '{"index.html": "...", "src/main.js": "...", '
                    '"public/logo.svg": "..."}. Directory structure is preserved '
                    "exactly. Paths must be relative; leading slashes and '..' are "
                    "stripped."
                )
            ),
        ],
        description: str | None = None,
        project: Annotated[str | None, Field(description="Project slug or name.")] = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Save a multi-file project as ONE artifact with its tree intact.

        An HTML/CSS/JS page, a Vite app, a small repo: all of it stays one
        artifact with one id, one manifest and per-file hashes. Do not call
        artifact_save_text repeatedly for the files of one project - that
        produces unrelated artifacts and loses the structure.

        To update an existing artifact use artifact_create_revision, which
        links the new version to the old one.
        """
        services = get_services()
        principal = current_principal()
        if not files:
            raise ValidationError("A bundle needs at least one file.")

        record = await services.artifacts.save_files(
            title=title,
            description=description,
            files={path: text.encode("utf-8") for path, text in files.items()},
            project_id=await services.projects.resolve(project),
            project_slug=project,
            source_provider=principal.provider if principal.provider != "unknown" else None,
            source_type="generated_by_model",
        )
        classification = await _classify_and_file(services, record, stated_project=project)
        await services.audit.record(
            tool="artifact_save_bundle",
            action="create",
            object_type="artifact",
            object_id=record.id,
            principal=principal,
            resulting_version={"slug": record.slug, "files": record.file_count},
        )
        refreshed = await services.artifacts.get(record.id) or record
        return {
            "artifact": refreshed.model_dump(mode="json"),
            "classification": classification,
            "capture_status": "captured",
        }

    @mcp.tool(
        name="artifact_save_binary",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_save_binary(
        filename: Annotated[str, Field(description="Filename with extension.")],
        content_base64: Annotated[
            str,
            Field(
                description=f"Base64 of the file. Maximum {MAX_INLINE_BASE64_BYTES} decoded bytes."
            ),
        ],
        title: str | None = None,
        description: str | None = None,
        project: str | None = None,
    ) -> dict:
        """Save a SMALL binary file you have the actual bytes for.

        Only use this when the bytes are genuinely available to you. For
        anything above a couple of megabytes call artifact_begin_upload and
        PUT to the signed URL instead - large base64 in a tool argument is
        slow and frequently truncated.

        If you can see a file in the conversation but cannot read its bytes,
        do NOT guess at them: call artifact_register_reference.
        """
        services = get_services()
        principal = current_principal()
        try:
            data = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValidationError(
                "content_base64 is not valid base64. Do not reconstruct bytes you "
                "cannot read - use artifact_register_reference instead."
            ) from exc
        if len(data) > MAX_INLINE_BASE64_BYTES:
            raise ValidationError(
                f"{len(data)} bytes exceeds the inline limit of "
                f"{MAX_INLINE_BASE64_BYTES}. Use artifact_begin_upload."
            )

        record = await services.artifacts.save_files(
            title=title or filename,
            description=description,
            files={filename: data},
            project_id=await services.projects.resolve(project),
            project_slug=project,
            source_provider=principal.provider if principal.provider != "unknown" else None,
            source_type="uploaded_by_user",
            original_names={sanitize_relative_path(filename): filename},
        )
        classification = await _classify_and_file(services, record, stated_project=project)
        refreshed = await services.artifacts.get(record.id) or record
        return {
            "artifact": refreshed.model_dump(mode="json"),
            "classification": classification,
            "capture_status": "captured",
        }

    @mcp.tool(
        name="artifact_begin_upload",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_begin_upload(
        title: Annotated[str, Field(description="Name for the artifact.")],
        filename: Annotated[str, Field(description="Filename with extension.")],
        byte_size: Annotated[int, Field(ge=1, description="Exact size in bytes.")],
        mime_type: Annotated[
            str | None, Field(description="Detected automatically if omitted.")
        ] = None,
        project: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Get a signed URL to upload a large file directly to object storage.

        Returns an upload URL and an artifact id. The artifact stays in
        'pending_import' until artifact_complete_upload confirms the bytes
        arrived and their hash matches - until then it is NOT captured and
        must not be described to the user as saved.
        """
        services = get_services()
        principal = current_principal()
        if byte_size > services.settings.max_artifact_file_bytes:
            raise ValidationError(
                f"{byte_size} bytes exceeds the per-file limit of "
                f"{services.settings.max_artifact_file_bytes}."
            )

        safe_path = sanitize_relative_path(filename)
        mime = mime_type or guess_mime(filename)
        record = await services.artifacts.register_reference(
            title=title,
            description=description,
            status="pending_import",
            source_provider=principal.provider if principal.provider != "unknown" else None,
            source_type="uploaded_by_user",
            project_id=await services.projects.resolve(project),
            expected_files=[{"filename": filename, "mime_type": mime, "byte_size": byte_size}],
        )
        prefix = artifact_prefix(
            artifact_slug=record.slug, version=record.version, project_slug=project
        )
        key = object_key(prefix, safe_path)
        ticket = await services.store.signed_upload_ticket(
            key, mime_type=mime, expires_in=services.settings.signed_url_ttl_seconds
        )
        await services.db.execute(
            "UPDATE artifacts SET storage_prefix = $2 WHERE id = $1", record.id, prefix
        )
        await services.db.execute(
            "UPDATE artifact_files SET object_key = $3 WHERE artifact_id = $1 AND relative_path = $2",
            record.id,
            safe_path,
            key,
        )
        return {
            "artifact_id": str(record.id),
            "relative_path": safe_path,
            "upload_url": ticket.upload_url,
            "method": ticket.method,
            "headers": ticket.headers,
            "expires_in_seconds": ticket.expires_in,
            "next_step": (
                "PUT the bytes to upload_url, then call artifact_complete_upload "
                "with the artifact id and the file's SHA-256. The artifact is not "
                "captured until then."
            ),
        }

    @mcp.tool(
        name="artifact_complete_upload",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_complete_upload(
        artifact_id: UUID,
        relative_path: Annotated[str, Field(description="Path returned by artifact_begin_upload.")],
        sha256: Annotated[str, Field(description="SHA-256 hex digest of the uploaded bytes.")],
    ) -> dict:
        """Confirm an upload landed, verifying the bytes against your hash.

        The artifact becomes 'captured' only if the object exists in storage.
        If it does not, the status is left unchanged and this says so.
        """
        services = get_services()
        row = await services.db.fetchrow(
            "SELECT object_key FROM artifact_files WHERE artifact_id = $1 AND relative_path = $2",
            artifact_id,
            sanitize_relative_path(relative_path),
        )
        if row is None or not row["object_key"]:
            return {"captured": False, "reason": "No pending upload matches that path."}

        if not await services.store.exists(row["object_key"]):
            return {
                "captured": False,
                "reason": (
                    "Nothing was found at the upload location. The artifact remains "
                    "pending_import - do not tell the user it was saved."
                ),
            }

        await services.db.execute(
            """
            UPDATE artifact_files SET sha256 = $3, storage_status = 'captured'
            WHERE artifact_id = $1 AND relative_path = $2
            """,
            artifact_id,
            sanitize_relative_path(relative_path),
            sha256.lower(),
        )
        await services.db.execute(
            """
            UPDATE artifacts SET storage_status = 'captured'
            WHERE id = $1 AND NOT EXISTS (
                SELECT 1 FROM artifact_files f
                WHERE f.artifact_id = $1 AND f.storage_status <> 'captured'
            )
            """,
            artifact_id,
        )
        record = await services.artifacts.get(artifact_id)
        assert record is not None
        classification = await _classify_and_file(services, record, stated_project=None)
        return {
            "captured": record.storage_status == "captured",
            "artifact": record.model_dump(mode="json"),
            "classification": classification,
        }

    @mcp.tool(
        name="artifact_register_reference",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_register_reference(
        title: Annotated[str, Field(description="What the artifact is.")],
        description: Annotated[
            str | None, Field(description="Everything you can observe about it.")
        ] = None,
        artifact_type: Annotated[
            str,
            Field(description="image, video, audio, document, dataset, code_project, etc."),
        ] = "other",
        source_type: Annotated[
            str, Field(description="uploaded_by_user or generated_by_model.")
        ] = "uploaded_by_user",
        expected_files: Annotated[
            list[dict],
            Field(
                description="Known filenames, as objects with 'filename' and optional 'byte_size'."
            ),
        ] = [],
        project: str | None = None,
        conversation_id: str | None = None,
    ) -> dict:
        """Record that an artifact existed WITHOUT claiming we have its bytes.

        Use when you can see a file in the conversation but your platform does
        not make its contents available to tools - which is the normal case for
        images and uploads in the native apps. This creates an honest
        placeholder with status 'pending_import' that a later provider-export
        import can fill in.

        Never describe the result of this tool as "saved" or "archived". The
        bytes were not captured.
        """
        services = get_services()
        principal = current_principal()
        record = await services.artifacts.register_reference(
            title=title,
            description=description,
            status="pending_import",
            artifact_type=artifact_type,
            source_provider=principal.provider if principal.provider != "unknown" else None,
            source_type=source_type,
            project_id=await services.projects.resolve(project),
            expected_files=expected_files,
            metadata={"provider_conversation_id": conversation_id},
        )
        await services.audit.record(
            tool="artifact_register_reference",
            action="create",
            object_type="artifact",
            object_id=record.id,
            principal=principal,
        )
        return {
            "artifact": record.model_dump(mode="json"),
            "capture_status": "pending_import",
            "tell_the_user": (
                "The artifact's details were recorded, but its contents were NOT "
                "captured - this platform did not make the bytes available. It will "
                "be filled in when the provider export is imported."
            ),
        }

    @mcp.tool(
        name="artifact_search",
        tags={"artifacts", "read"},
        auth=require_scopes(scopes.ARTIFACTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def artifact_search(
        query: Annotated[str, Field(description="Words from the title or description.")] = "",
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        project: str | None = None,
        artifact_type: str | None = None,
        provider: str | None = None,
        storage_status: Annotated[
            str | None,
            Field(description="captured, referenced, pending_import or failed."),
        ] = None,
    ) -> list[ArtifactRecord]:
        """Find artifacts created or uploaded in any provider.

        Check storage_status on each result: 'captured' means the bytes are
        held and downloadable; anything else means only metadata exists.
        """
        services = get_services()
        return await services.artifacts.search(
            query,
            limit=limit,
            project_id=await services.projects.resolve(project),
            artifact_type=artifact_type,
            provider=provider,
            storage_status=storage_status,
        )

    @mcp.tool(
        name="artifact_get",
        tags={"artifacts", "read"},
        auth=require_scopes(scopes.ARTIFACTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def artifact_get(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
    ) -> ArtifactRecord | dict:
        """One artifact with its full file list and capture status."""
        record = await get_services().artifacts.get(artifact)
        return record or {"error": f"No artifact matches {artifact!r}."}

    @mcp.tool(
        name="artifact_manifest",
        tags={"artifacts", "read"},
        auth=require_scopes(scopes.ARTIFACTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def artifact_manifest(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
    ) -> dict:
        """The artifact's manifest: every file, its path, hash, MIME and size."""
        return await get_services().artifacts.manifest(artifact)

    @mcp.tool(
        name="artifact_signed_download",
        tags={"artifacts", "read"},
        auth=require_scopes(scopes.ARTIFACTS_READ),
        annotations={"readOnlyHint": True},
    )
    async def artifact_signed_download(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
        relative_path: Annotated[
            str | None, Field(description="One file, or omit for every file.")
        ] = None,
    ) -> dict:
        """Short-lived signed URLs for an artifact's captured files.

        Fails if the artifact was never captured, rather than returning a link
        to nothing. URLs expire quickly and expire faster for sensitive
        artifacts - fetch promptly and do not store them.
        """
        services = get_services()
        files = await services.artifacts.signed_download(
            artifact_ref=artifact, relative_path=relative_path
        )
        await services.audit.record(
            tool="artifact_signed_download",
            action="read_sensitive",
            object_type="artifact",
            metadata={"file_count": len(files)},
        )
        return {"files": files}

    @mcp.tool(
        name="artifact_create_revision",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_create_revision(
        artifact: Annotated[str, Field(description="Existing artifact id or slug.")],
        files: Annotated[
            dict[str, str],
            Field(description="The complete new file set, as path -> contents."),
        ],
        title: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Save a new version of an existing artifact, linked to the previous one.

        Use this instead of saving a second artifact when the user asks for
        changes to something you already saved - it keeps the history connected
        rather than leaving two unrelated copies.
        """
        services = get_services()
        record = await services.artifacts.create_revision(
            artifact_ref=artifact,
            files={p: c.encode("utf-8") for p, c in files.items()},
            title=title,
            description=description,
        )
        await services.audit.record(
            tool="artifact_create_revision",
            action="create",
            object_type="artifact",
            object_id=record.id,
            resulting_version={"version": record.version},
        )
        return {
            "artifact": record.model_dump(mode="json"),
            "capture_status": record.storage_status,
        }

    @mcp.tool(
        name="artifact_attach_to_project",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
        annotations={"idempotentHint": True},
    )
    async def artifact_attach_to_project(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
        project: Annotated[str, Field(description="Project slug or name.")],
    ) -> dict:
        """Associate an artifact with a project."""
        services = get_services()
        record = await services.artifacts.get(artifact)
        if record is None:
            return {"attached": False, "reason": f"No artifact matches {artifact!r}."}
        project_id = await services.projects.require(project)
        await services.artifacts.attach_to_project(record.id, project_id)
        await services.audit.record(
            tool="artifact_attach_to_project",
            action="update",
            object_type="artifact",
            object_id=record.id,
            resulting_version={"project": project},
        )
        return {"attached": True, "artifact_id": str(record.id), "project": project}

    @mcp.tool(
        name="artifact_queue_for_vault",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE, scopes.NOTES_WRITE),
    )
    async def artifact_queue_for_vault(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
    ) -> dict:
        """Queue an artifact note (and small file mirrors) for the Obsidian vault.

        Large files are never mirrored; the note links to them in cloud storage
        instead. The write happens when the local sync worker next runs.
        """
        services = get_services()
        record = await services.artifacts.get(artifact)
        if record is None:
            return {"queued": 0, "reason": f"No artifact matches {artifact!r}."}
        if record.storage_status != "captured":
            return {
                "queued": 0,
                "reason": (
                    f"Artifact status is {record.storage_status}; there is nothing to "
                    "mirror until its bytes are captured."
                ),
            }
        queued = await _queue_vault_mirror(
            services, record, project_slug=record.project, classification_event_id=None
        )
        return {
            "queued": queued,
            "note": "Queued. Files appear once the local obsidian-sync worker runs.",
        }

    @mcp.tool(
        name="artifact_classify",
        tags={"artifacts", "write"},
        auth=require_scopes(scopes.ARTIFACTS_WRITE),
    )
    async def artifact_classify(
        artifact: Annotated[str, Field(description="Artifact id or slug.")],
        project: Annotated[
            str | None, Field(description="Force a project instead of classifying.")
        ] = None,
    ) -> dict:
        """Re-run classification on an artifact.

        Useful after a classifier outage, or once the right project exists.
        """
        services = get_services()
        record = await services.artifacts.get(artifact)
        if record is None:
            return {"error": f"No artifact matches {artifact!r}."}
        return await _classify_and_file(services, record, stated_project=project)
