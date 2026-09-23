"""Safe archive handling.

Provider exports are ZIPs from outside our trust boundary. Every extraction
goes through :func:`iter_safe_entries`, which enforces, in order:

* **zip-slip** - no absolute paths, no ``..``, no drive letters, no symlinks
* **entry count** - refuse an archive with an implausible number of members
* **per-entry size** - refuse a single member that expands beyond the cap
* **total size** - refuse an archive whose total expansion exceeds the cap
* **compression ratio** - refuse a member compressed beyond a plausible ratio,
  which is what a zip bomb looks like before it has finished expanding

Members are streamed in bounded chunks; a declared size is never trusted, only
the bytes actually read.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from personal_context_mcp.errors import ArchiveSecurityError
from personal_context_mcp.util import sha256_bytes

_CHUNK = 1 << 20  # 1 MiB
_UNIX_SYMLINK_MODE = 0xA000


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 50_000
    max_total_bytes: int = 4 * 1024 * 1024 * 1024
    max_entry_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: int = 200


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    data: bytes
    sha256: str
    byte_size: int


def _reject_path(name: str) -> None:
    normalized = name.replace("\\", "/")
    if not normalized or normalized.endswith("/"):
        return
    if "\x00" in normalized:
        raise ArchiveSecurityError(f"Archive member has a null byte in its name: {name!r}")
    if normalized.startswith("/") or normalized.startswith("~"):
        raise ArchiveSecurityError(f"Archive member has an absolute path: {name!r}")
    if len(normalized) > 2 and normalized[1] == ":":
        raise ArchiveSecurityError(f"Archive member has a drive letter: {name!r}")
    parts = normalized.split("/")
    if ".." in parts:
        raise ArchiveSecurityError(f"Archive member escapes the extraction root: {name!r}")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # External attributes carry the unix mode in the high 16 bits.
    return bool((info.external_attr >> 16) & _UNIX_SYMLINK_MODE == _UNIX_SYMLINK_MODE)


def iter_safe_entries(
    archive_path: Path | str,
    *,
    limits: ArchiveLimits | None = None,
    name_filter: Callable[[str], bool] | None = None,
) -> Iterator[ArchiveEntry]:
    """Yield the archive's file members, or raise ArchiveSecurityError."""
    limits = limits or ArchiveLimits()
    total = 0

    with zipfile.ZipFile(archive_path) as zf:
        infos = zf.infolist()
        if len(infos) > limits.max_entries:
            raise ArchiveSecurityError(
                f"Archive declares {len(infos)} members, above the limit of {limits.max_entries}."
            )

        declared_total = sum(i.file_size for i in infos)
        if declared_total > limits.max_total_bytes:
            raise ArchiveSecurityError(
                f"Archive declares {declared_total} uncompressed bytes, above the "
                f"limit of {limits.max_total_bytes}."
            )

        for info in infos:
            if info.is_dir():
                continue
            _reject_path(info.filename)
            if _is_symlink(info):
                raise ArchiveSecurityError(
                    f"Archive contains a symlink, which is not extracted: {info.filename!r}"
                )
            if info.file_size > limits.max_entry_bytes:
                raise ArchiveSecurityError(
                    f"Archive member {info.filename!r} declares "
                    f"{info.file_size} bytes, above the per-file limit."
                )
            if info.compress_size > 0:
                ratio = info.file_size / info.compress_size
                if ratio > limits.max_compression_ratio:
                    raise ArchiveSecurityError(
                        f"Archive member {info.filename!r} has a compression ratio of "
                        f"{ratio:.0f}:1, above the limit of "
                        f"{limits.max_compression_ratio}:1. Refusing to expand it."
                    )
            if name_filter is not None and not name_filter(info.filename):
                continue

            # Read in bounded chunks and enforce against the *actual* bytes,
            # because the declared size in the header is attacker-controlled.
            chunks: list[bytes] = []
            read = 0
            with zf.open(info) as member:
                while chunk := member.read(_CHUNK):
                    read += len(chunk)
                    total += len(chunk)
                    if read > limits.max_entry_bytes:
                        raise ArchiveSecurityError(
                            f"Archive member {info.filename!r} expanded past the "
                            "per-file limit; refusing to continue."
                        )
                    if total > limits.max_total_bytes:
                        raise ArchiveSecurityError(
                            "Archive expanded past the total size limit; refusing to continue."
                        )
                    chunks.append(chunk)

            data = b"".join(chunks)
            yield ArchiveEntry(
                name=info.filename.replace("\\", "/"),
                data=data,
                sha256=sha256_bytes(data),
                byte_size=len(data),
            )


def read_member(
    archive_path: Path | str, name: str, *, limits: ArchiveLimits | None = None
) -> bytes:
    """Read one named member with the same guards applied."""
    for entry in iter_safe_entries(archive_path, limits=limits, name_filter=lambda n: n == name):
        return entry.data
    raise ArchiveSecurityError(f"Archive member not found: {name!r}")
