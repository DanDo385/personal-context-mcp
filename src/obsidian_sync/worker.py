"""The sync worker loop.

Poll, lease, write, acknowledge. Nothing in this process listens on a socket.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import httpx

from obsidian_sync.client import SyncClient
from obsidian_sync.config import WorkerSettings
from obsidian_sync.vault import Vault
from personal_context.logging_setup import configure_logging, get_logger
from personal_context.util import sha256_bytes

logger = get_logger(__name__)


class ObsidianSyncWorker:
    def __init__(self, settings: WorkerSettings) -> None:
        self._settings = settings
        self._client = SyncClient(settings)
        self._vault = Vault(
            settings.resolved_vault,
            settings.vault_roots,
            max_bytes=settings.max_file_bytes,
            dry_run=settings.dry_run,
        )
        self._stopping = asyncio.Event()
        #: vault path -> sha we last wrote, used to spot human edits.
        self._written: dict[str, str] = {}

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info(
            "obsidian-sync starting",
            extra={
                "vault": str(self._settings.resolved_vault),
                "api": self._settings.api_url,
                "dry_run": self._settings.dry_run,
            },
        )
        try:
            while not self._stopping.is_set():
                try:
                    processed = await self._tick()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401:
                        logger.error(
                            "worker rejected: check OBSIDIAN_API_KEY matches "
                            "PC_WORKER_API_KEY on the server"
                        )
                    else:
                        logger.warning(
                            "server error while polling",
                            extra={"status": exc.response.status_code},
                        )
                    processed = 0
                except httpx.HTTPError:
                    # Offline is the normal state for a laptop. Keep quiet and
                    # retry; nothing is lost because the queue is server-side.
                    logger.debug("could not reach the server; will retry")
                    processed = 0
                except Exception:
                    logger.exception("unexpected worker error")
                    processed = 0

                # Poll again immediately while there is a backlog.
                delay = 0.5 if processed else self._settings.poll_interval_seconds
                with_timeout = asyncio.wait_for(self._stopping.wait(), timeout=delay)
                try:
                    await with_timeout
                except TimeoutError:
                    continue
        finally:
            await self._client.aclose()
            logger.info("obsidian-sync stopped")

    async def _tick(self) -> int:
        if self._settings.watch_for_edits and self._written:
            edits = self._vault.scan_for_edits(self._written)
            if edits:
                result = await self._client.push_edits(edits)
                for edit in edits:
                    # Accept the new on-disk hash so the edit is pushed once.
                    self._written[str(edit["vault_path"])] = str(edit["sha256"])
                logger.info(
                    "pushed vault edits",
                    extra={"count": len(edits), "applied": len(result.get("applied", []))},
                )

        jobs = await self._client.lease()
        for job in jobs:
            await self._handle(job)
        return len(jobs)

    async def _handle(self, job: dict[str, Any]) -> None:
        job_id = str(job["id"])
        vault_path = str(job["vault_path"])

        try:
            if "download_url" in job:
                data = await self._client.download(job["download_url"])
                expected = job.get("expected_content_sha256")
                if expected and sha256_bytes(data) != expected:
                    await self._client.ack(
                        job_id,
                        status="failed",
                        error="downloaded bytes did not match the expected hash",
                    )
                    return
            else:
                content = job.get("content")
                if content is None:
                    await self._client.ack(job_id, status="failed", error="job carried no content")
                    return
                data = content.encode("utf-8")

            outcome = self._vault.write(
                vault_path, data, expected_sha256=job.get("expected_sha256")
            )
        except Exception as exc:
            logger.exception("failed to write vault file", extra={"vault_path": vault_path})
            await self._client.ack(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            return

        if outcome.conflict:
            logger.warning("vault write conflict", extra={"vault_path": vault_path})
            await self._client.ack(job_id, status="conflict", error=outcome.reason)
            return

        if not outcome.written and outcome.reason != "already up to date":
            await self._client.ack(job_id, status="failed", error=outcome.reason)
            return

        if outcome.sha256:
            self._written[vault_path] = outcome.sha256
        await self._client.ack(job_id, status="done", sha256=outcome.sha256)
        logger.info("wrote vault file", extra={"vault_path": vault_path})


async def run_worker(settings: WorkerSettings) -> None:
    configure_logging("INFO")
    worker = ObsidianSyncWorker(settings)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with __import__("contextlib").suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)

    await worker.run()
