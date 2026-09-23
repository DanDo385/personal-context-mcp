from __future__ import annotations

from personal_context_mcp.config import Settings
from personal_context_mcp.storage.base import ObjectStore, StoredObject, UploadTicket
from personal_context_mcp.storage.local import LocalObjectStore
from personal_context_mcp.storage.supabase import SupabaseObjectStore

__all__ = [
    "LocalObjectStore",
    "ObjectStore",
    "StoredObject",
    "SupabaseObjectStore",
    "UploadTicket",
    "build_object_store",
]


def build_object_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "supabase":
        return SupabaseObjectStore(
            supabase_url=settings.supabase_url,
            service_key=settings.supabase_service_key.get_secret_value(),
            bucket=settings.supabase_bucket,
            archive_bucket=settings.supabase_archive_bucket,
        )
    return LocalObjectStore(
        settings.local_storage_root,
        base_url=str(settings.base_url),
        signing_key=(
            settings.oauth_signing_key.get_secret_value()
            or settings.worker_api_key.get_secret_value()
            or "local-dev-signing-key"
        ),
        bucket=settings.supabase_bucket,
    )
