"""Safe vault writes and edit detection.

Every write goes through :func:`resolve_in_vault`, which re-validates the path
the server sent. The server already validated it; doing it again here is the
point. The cloud service is the thing most likely to be compromised, and the
worker is the thing with filesystem access, so the worker does not trust it.

Concurrency: a write is refused when the file on disk does not match the hash
the job expected. A human edit is never silently overwritten - the job comes
back as a conflict and the edit is pushed upstream instead.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from personal_context_mcp.errors import PathSecurityError
from personal_context_mcp.obsidian.paths import resolve_in_vault
from personal_context_mcp.obsidian.render import PC_ID_PREFIX
from personal_context_mcp.util import sha256_bytes

_ID_MARKER = re.compile(rf"<!--\s*{re.escape(PC_ID_PREFIX)}([0-9a-fA-F-]{{36}})\s*-->")
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[([ xX])\]\s*(.*)$")
_PC_ID_FRONTMATTER = re.compile(r"^pc_id:\s*([0-9a-fA-F-]{36})\s*$", re.M)


@dataclass(frozen=True)
class WriteOutcome:
    written: bool
    sha256: str | None
    conflict: bool = False
    reason: str | None = None


class Vault:
    def __init__(self, root: Path, roots: dict[str, str], *, max_bytes: int, dry_run: bool) -> None:
        self._root = root
        self._roots = roots
        self._max_bytes = max_bytes
        self._dry_run = dry_run
        if not self._root.is_dir():
            raise FileNotFoundError(
                f"Vault directory does not exist: {self._root}. Set OBSIDIAN_SYNC_VAULT_PATH."
            )

    def resolve(self, relative_path: str) -> Path:
        return resolve_in_vault(self._root, relative_path, self._roots)

    def read(self, relative_path: str) -> bytes | None:
        try:
            target = self.resolve(relative_path)
        except (PathSecurityError, FileNotFoundError):
            return None
        return target.read_bytes() if target.is_file() else None

    def write(
        self, relative_path: str, data: bytes, *, expected_sha256: str | None
    ) -> WriteOutcome:
        if len(data) > self._max_bytes:
            return WriteOutcome(False, None, reason=f"file exceeds {self._max_bytes} bytes")

        try:
            target = self.resolve(relative_path)
        except PathSecurityError as exc:
            # The server sent something this worker will not write. That is a
            # bug or an attack; either way it is a hard refusal, not a retry.
            return WriteOutcome(False, None, reason=f"path refused: {exc}")

        current = target.read_bytes() if target.is_file() else None
        digest = sha256_bytes(data)

        if current is not None:
            current_hash = sha256_bytes(current)
            if current_hash == digest:
                return WriteOutcome(True, digest, reason="already up to date")
            if expected_sha256 and current_hash != expected_sha256:
                return WriteOutcome(
                    False,
                    current_hash,
                    conflict=True,
                    reason=(
                        "the file on disk has changed since this write was queued; "
                        "refusing to overwrite a human edit"
                    ),
                )

        if self._dry_run:
            return WriteOutcome(False, digest, reason="dry run")

        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory and rename, so a crash
        # mid-write cannot leave a half-written note in the vault.
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".pc-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return WriteOutcome(True, digest)

    # -------------------------------------------------------- reading back

    def scan_for_edits(self, known: dict[str, str]) -> list[dict[str, object]]:
        """Find managed files whose content differs from what we last wrote.

        ``known`` maps vault path -> the SHA-256 the server believes is on
        disk. Anything that differs is a human edit.
        """
        edits: list[dict[str, object]] = []
        for relative_path, expected in known.items():
            try:
                target = self.resolve(relative_path)
            except (PathSecurityError, FileNotFoundError):
                continue
            if not target.is_file():
                continue
            data = target.read_bytes()
            digest = sha256_bytes(data)
            if digest == expected:
                continue

            text = data.decode("utf-8", errors="replace")
            items = parse_list_items(text)
            edits.append(
                {
                    "kind": "list_items" if items else "note",
                    "vault_path": relative_path,
                    "sha256": digest,
                    "content": text,
                    "items": items,
                }
            )
        return edits


def parse_list_items(text: str) -> list[dict[str, object]]:
    """Extract checkbox state from a rendered list file.

    Identity comes only from the ``pc:id`` marker. A line without one is
    reported with a null id and never matched by text: ticking the wrong item
    because two lines read similarly would be worse than doing nothing.
    """
    items: list[dict[str, object]] = []
    for line in text.splitlines():
        match = _CHECKBOX.match(line)
        if not match:
            continue
        checked = match.group(1).lower() == "x"
        body = match.group(2)
        marker = _ID_MARKER.search(body)
        items.append(
            {
                "id": marker.group(1) if marker else None,
                "checked": checked,
                "text": _ID_MARKER.sub("", body).strip(),
            }
        )
    return items


def note_id_of(text: str) -> str | None:
    match = _PC_ID_FRONTMATTER.search(text)
    return match.group(1) if match else None
