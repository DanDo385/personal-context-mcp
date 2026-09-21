"""Outbound HTTP client for the worker."""

from __future__ import annotations

from typing import Any

import httpx

from obsidian_sync.config import WorkerSettings


class SyncClient:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.api_url.rstrip("/"),
            timeout=httpx.Timeout(60.0, connect=10.0),
            headers={"x-worker-key": settings.api_key.get_secret_value()},
        )

    async def lease(self) -> list[dict[str, Any]]:
        response = await self._client.post(
            "/worker/jobs/lease",
            json={"worker_id": self._settings.worker_id, "limit": self._settings.batch_size},
        )
        response.raise_for_status()
        return response.json().get("jobs", [])

    async def ack(
        self,
        job_id: str,
        *,
        status: str = "done",
        sha256: str | None = None,
        error: str | None = None,
    ) -> None:
        response = await self._client.post(
            "/worker/jobs/ack",
            json={
                "worker_id": self._settings.worker_id,
                "job_id": job_id,
                "status": status,
                "sha256": sha256,
                "error": error,
            },
        )
        response.raise_for_status()

    async def push_edits(self, edits: list[dict[str, Any]]) -> dict[str, Any]:
        response = await self._client.post(
            "/worker/edits",
            json={"worker_id": self._settings.worker_id, "edits": edits},
        )
        response.raise_for_status()
        return response.json()

    async def download(self, url: str) -> bytes:
        # Signed URLs point at object storage, not at our API, so this uses a
        # bare client with no worker credential attached.
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    async def aclose(self) -> None:
        await self._client.aclose()
