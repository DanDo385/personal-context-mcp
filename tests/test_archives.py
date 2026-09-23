"""Archive guards: zip-slip, bombs, symlinks, limits."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from personal_context_mcp.artifacts.archives import ArchiveLimits, iter_safe_entries
from personal_context_mcp.errors import ArchiveSecurityError


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_reads_normal_archive(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path / "ok.zip",
        {"conversations.json": b"[]", "files/photo.png": b"\x89PNG data"},
    )
    entries = {e.name: e for e in iter_safe_entries(archive)}
    assert set(entries) == {"conversations.json", "files/photo.png"}
    assert entries["conversations.json"].byte_size == 2
    assert entries["files/photo.png"].sha256


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "../../etc/passwd", "/absolute.txt", "a/../../b.txt"],
)
def test_rejects_zip_slip(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "slip.zip"
    # writestr normalises some paths, so write the header name directly.
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo(member)
        zf.writestr(info, b"payload")

    with pytest.raises(ArchiveSecurityError, match=r"escapes|absolute"):
        list(iter_safe_entries(archive))


def test_rejects_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0xA1FF << 16  # lrwxrwxrwx
        zf.writestr(info, b"/etc/passwd")

    with pytest.raises(ArchiveSecurityError, match="symlink"):
        list(iter_safe_entries(archive))


def test_rejects_too_many_entries(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "many.zip", {f"f{i}.txt": b"x" for i in range(20)})
    with pytest.raises(ArchiveSecurityError, match="members"):
        list(iter_safe_entries(archive, limits=ArchiveLimits(max_entries=5)))


def test_rejects_oversized_total(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "big.zip", {"a.bin": b"x" * 5000})
    with pytest.raises(ArchiveSecurityError, match="uncompressed"):
        list(iter_safe_entries(archive, limits=ArchiveLimits(max_total_bytes=1000)))


def test_rejects_oversized_single_entry(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "one-big.zip", {"a.bin": b"x" * 5000})
    with pytest.raises(ArchiveSecurityError, match="per-file limit"):
        list(
            iter_safe_entries(
                archive,
                limits=ArchiveLimits(max_entry_bytes=1000, max_total_bytes=10_000_000),
            )
        )


def test_rejects_compression_bomb(tmp_path: Path) -> None:
    """A highly compressible member is refused before it is expanded."""
    archive = _zip(tmp_path / "bomb.zip", {"bomb.bin": b"\0" * 2_000_000})
    with pytest.raises(ArchiveSecurityError, match="compression ratio"):
        list(
            iter_safe_entries(
                archive,
                limits=ArchiveLimits(
                    max_compression_ratio=10,
                    max_entry_bytes=50_000_000,
                    max_total_bytes=50_000_000,
                ),
            )
        )


def test_name_filter_limits_what_is_read(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "mixed.zip", {"a.json": b"{}", "b.png": b"img"})
    names = [e.name for e in iter_safe_entries(archive, name_filter=lambda n: n.endswith(".json"))]
    assert names == ["a.json"]


def test_directories_are_skipped(tmp_path: Path) -> None:
    archive = tmp_path / "dirs.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("folder/", b"")
        zf.writestr("folder/file.txt", b"hello")
    assert [e.name for e in iter_safe_entries(archive)] == ["folder/file.txt"]


@pytest.mark.parametrize(
    "name",
    ["bad\x00name.txt", "../escape.txt", "/abs.txt", "C:/win.txt", "~/home.txt"],
)
def test_reject_path_guard(name: str) -> None:
    """The name guard is tested directly.

    CPython's zipfile truncates a member name at a NUL when reading the central
    directory, so a NUL cannot be delivered through a real archive. The guard
    stays because we do not want to depend on that, but the only honest way to
    exercise it is to call it.
    """
    from personal_context_mcp.artifacts.archives import _reject_path

    with pytest.raises(ArchiveSecurityError):
        _reject_path(name)


def test_stream_guard_catches_lying_header(tmp_path: Path) -> None:
    """The declared size is attacker-controlled; the read is what counts."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.bin", b"y" * 300_000)
    archive = tmp_path / "lie.zip"
    archive.write_bytes(buffer.getvalue())

    # Generous declared-size limits, tight per-entry byte cap: the guard must
    # still fire while streaming.
    with pytest.raises(ArchiveSecurityError):
        list(
            iter_safe_entries(
                archive,
                limits=ArchiveLimits(
                    max_entry_bytes=1000,
                    max_total_bytes=10_000_000,
                    max_compression_ratio=100_000,
                ),
            )
        )
