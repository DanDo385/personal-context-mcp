"""Supabase Storage backend (REST).

Uses the storage REST API directly through httpx rather than pulling in the
full supabase client: we need six operations, and the service-role key must be
handled carefully enough that an explicit implementation is worth having.

Buckets MUST be private. `ensure_buckets()` creates them private if missing
and refuses to continue if an existing bucket is public.
"""

from __future__ import annotations

from typing import Any

import httpx

from personal_context_mcp.errors import NotFoundError, StorageError
from personal_context_mcp.logging_setup import get_logger
from personal_context_mcp.storage.base import StoredObject, UploadTicket
from personal_context_mcp.util import sha256_bytes

logger = get_logger(__name__)


def auth_headers(service_key: str) -> dict[str, str]:
    """Supabase accepts two kinds of secret key. The legacy ``service_role``
    key is a JWT and goes in both headers. The newer ``sb_secret_...`` keys
    are not JWTs: the gateway reads them from ``apikey`` and mints the token
    itself, and a non-JWT bearer token would be rejected."""
    if service_key.startswith("sb_"):
        return {"apikey": service_key}
    return {"Authorization": f"Bearer {service_key}", "apikey": service_key}


class SupabaseObjectStore:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_key: str,
        bucket: str,
        archive_bucket: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = supabase_url.rstrip("/")
        self._key = service_key
        self.bucket = bucket
        self.archive_bucket = archive_bucket or bucket
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers=auth_headers(service_key),
        )

    def _url(self, *parts: str) -> str:
        return "/".join([f"{self._base}/storage/v1", *parts])

    @staticmethod
    def _raise(response: httpx.Response, action: str) -> None:
        if response.status_code == 404:
            raise NotFoundError(f"{action}: not found")
        # Never echo the response body verbatim: Supabase errors can include
        # the object key, and these messages reach the model.
        raise StorageError(f"{action} failed with HTTP {response.status_code}")

    async def ensure_buckets(self) -> None:
        """Create the configured buckets as private, or verify they are."""
        for name in {self.bucket, self.archive_bucket}:
            response = await self._client.get(self._url("bucket", name))
            if response.status_code == 200:
                if response.json().get("public"):
                    raise StorageError(
                        f"Supabase bucket {name!r} is PUBLIC. This service stores private "
                        "conversation artifacts; make the bucket private before starting."
                    )
                continue
            if response.status_code != 404:
                self._raise(response, f"inspect bucket {name}")

            created = await self._client.post(
                self._url("bucket"),
                json={"name": name, "id": name, "public": False},
            )
            if created.status_code not in (200, 201, 409):
                self._raise(created, f"create bucket {name}")
            logger.info("created private storage bucket", extra={"bucket": name})

    async def put(
        self, object_key: str, data: bytes, *, mime_type: str, bucket: str | None = None
    ) -> StoredObject:
        target = bucket or self.bucket
        response = await self._client.post(
            self._url("object", target, object_key),
            content=data,
            headers={
                "content-type": mime_type,
                "cache-control": "private, max-age=31536000",
                # Overwrite on retry: uploads are idempotent by object key.
                "x-upsert": "true",
            },
        )
        if response.status_code not in (200, 201):
            self._raise(response, "upload object")
        return StoredObject(
            object_key=object_key,
            byte_size=len(data),
            sha256=sha256_bytes(data),
            mime_type=mime_type,
        )

    async def get(self, object_key: str, *, bucket: str | None = None) -> bytes:
        response = await self._client.get(self._url("object", bucket or self.bucket, object_key))
        if response.status_code != 200:
            self._raise(response, "download object")
        return response.content

    async def delete(self, object_key: str, *, bucket: str | None = None) -> None:
        response = await self._client.delete(self._url("object", bucket or self.bucket, object_key))
        if response.status_code not in (200, 204, 404):
            self._raise(response, "delete object")

    async def exists(self, object_key: str, *, bucket: str | None = None) -> bool:
        response = await self._client.request(
            "HEAD", self._url("object", bucket or self.bucket, object_key)
        )
        return response.status_code == 200

    async def signed_download_url(
        self, object_key: str, *, expires_in: int, bucket: str | None = None
    ) -> str:
        target = bucket or self.bucket
        response = await self._client.post(
            self._url("object", "sign", target, object_key),
            json={"expiresIn": expires_in},
        )
        if response.status_code != 200:
            self._raise(response, "sign download url")
        payload: dict[str, Any] = response.json()
        signed = payload.get("signedURL") or payload.get("signedUrl")
        if not signed:
            raise StorageError("Supabase returned no signed URL")
        return f"{self._base}/storage/v1{signed}" if signed.startswith("/") else signed

    async def signed_upload_ticket(
        self, object_key: str, *, mime_type: str, expires_in: int, bucket: str | None = None
    ) -> UploadTicket:
        target = bucket or self.bucket
        response = await self._client.post(
            self._url("object", "upload", "sign", target, object_key),
            json={"expiresIn": expires_in},
        )
        if response.status_code != 200:
            self._raise(response, "sign upload url")
        payload = response.json()
        signed = payload.get("url") or payload.get("signedURL") or payload.get("signedUrl")
        if not signed:
            raise StorageError("Supabase returned no signed upload URL")
        url = f"{self._base}/storage/v1{signed}" if signed.startswith("/") else signed
        return UploadTicket(
            object_key=object_key,
            upload_url=url,
            method="PUT",
            headers={"content-type": mime_type, "x-upsert": "true"},
            expires_in=expires_in,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
