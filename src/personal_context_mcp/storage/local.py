"""Filesystem object store.

Used by the test suite and by local development without Supabase. Signed URLs
are served by the app's own ``/artifacts/download`` route, with an HMAC token
carrying the key and an expiry - the same contract as a real signed URL, just
issued by us.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from personal_context_mcp.errors import NotFoundError, PathSecurityError
from personal_context_mcp.storage.base import StoredObject, UploadTicket
from personal_context_mcp.util import sha256_bytes


class LocalObjectStore:
    def __init__(self, root: str | Path, *, base_url: str, signing_key: str, bucket: str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")
        self._signing_key = signing_key.encode("utf-8")
        self.bucket = bucket

    def _path(self, object_key: str, bucket: str | None) -> Path:
        # Defence in depth: keys are generated, but if one ever came from
        # elsewhere it must still not be able to escape the root.
        target = (self._root / (bucket or self.bucket) / object_key).resolve()
        if not target.is_relative_to(self._root):
            raise PathSecurityError(f"Object key escapes the storage root: {object_key!r}")
        return target

    async def put(
        self, object_key: str, data: bytes, *, mime_type: str, bucket: str | None = None
    ) -> StoredObject:
        path = self._path(object_key, bucket)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return StoredObject(
            object_key=object_key,
            byte_size=len(data),
            sha256=sha256_bytes(data),
            mime_type=mime_type,
        )

    async def get(self, object_key: str, *, bucket: str | None = None) -> bytes:
        path = self._path(object_key, bucket)
        if not path.is_file():
            raise NotFoundError(f"No stored object at {object_key!r}")
        return path.read_bytes()

    async def delete(self, object_key: str, *, bucket: str | None = None) -> None:
        self._path(object_key, bucket).unlink(missing_ok=True)

    async def exists(self, object_key: str, *, bucket: str | None = None) -> bool:
        return self._path(object_key, bucket).is_file()

    def _sign(self, object_key: str, bucket: str, expires_at: int) -> str:
        payload = f"{bucket}:{object_key}:{expires_at}".encode()
        digest = hmac.new(self._signing_key, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def verify(self, object_key: str, bucket: str, expires_at: int, signature: str) -> bool:
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(self._sign(object_key, bucket, expires_at), signature)

    async def signed_download_url(
        self, object_key: str, *, expires_in: int, bucket: str | None = None
    ) -> str:
        bucket = bucket or self.bucket
        expires_at = int(time.time()) + expires_in
        signature = self._sign(object_key, bucket, expires_at)
        return (
            f"{self._base_url}/artifacts/download"
            f"?bucket={quote(bucket)}&key={quote(object_key)}"
            f"&expires={expires_at}&sig={signature}"
        )

    async def signed_upload_ticket(
        self, object_key: str, *, mime_type: str, expires_in: int, bucket: str | None = None
    ) -> UploadTicket:
        bucket = bucket or self.bucket
        expires_at = int(time.time()) + expires_in
        signature = self._sign(object_key, bucket, expires_at)
        return UploadTicket(
            object_key=object_key,
            upload_url=(
                f"{self._base_url}/artifacts/upload"
                f"?bucket={quote(bucket)}&key={quote(object_key)}"
                f"&expires={expires_at}&sig={signature}"
            ),
            method="PUT",
            headers={"content-type": mime_type},
            expires_in=expires_in,
        )

    async def aclose(self) -> None:
        return None
