"""Small deterministic helpers.

Everything here is code that must NOT be delegated to a model: hashing,
slugging, filename sanitising, MIME detection, token estimation.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import unicodedata
from datetime import UTC, datetime

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")

#: Extensions mimetypes gets wrong or does not know, and which matter to us.
_MIME_OVERRIDES: dict[str, str] = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
    ".jsx": "text/jsx",
    ".mjs": "text/javascript",
    ".cjs": "text/javascript",
    ".sol": "text/x-solidity",
    ".rs": "text/x-rust",
    ".go": "text/x-go",
    ".py": "text/x-python",
    ".toml": "application/toml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".ipynb": "application/x-ipynb+json",
    ".mmd": "text/vnd.mermaid",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".heic": "image/heic",
    ".zip": "application/zip",
    ".webm": "video/webm",
    ".jsonl": "application/x-ndjson",
}

#: MIME prefixes/types treated as text for mirroring and extraction purposes.
_TEXTUAL_TYPES = frozenset(
    {
        "application/json",
        "application/x-ndjson",
        "application/toml",
        "application/yaml",
        "application/xml",
        "application/x-ipynb+json",
        "image/svg+xml",
        "application/javascript",
    }
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalized_sha256(text: str) -> str:
    """Hash of text normalised for *deduplication*, not for integrity.

    Collapses whitespace, strips punctuation-only differences in case, so that
    "Add Morgan Stanley " and "add morgan stanley" are one list item.
    """
    collapsed = " ".join(text.lower().split())
    collapsed = unicodedata.normalize("NFKC", collapsed).strip(" .!,;:")
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def slugify(value: str, *, max_length: int = 80, fallback: str = "untitled") -> str:
    """ASCII slug safe to use in object keys, vault paths and URLs."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_only).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or fallback


def safe_filename(name: str, *, max_length: int = 120) -> str:
    """Reduce an arbitrary supplied filename to one safe segment.

    Strips directories, control characters, leading dots and anything outside
    [A-Za-z0-9._-]. The *original* name is preserved in metadata; this is only
    what goes into a storage key or onto a disk.
    """
    base = name.replace("\\", "/").split("/")[-1]
    base = base.replace("\x00", "")
    base = _SAFE_SEGMENT.sub("_", base).strip("._")
    if not base:
        base = "file"
    if len(base) > max_length:
        stem, _, ext = base.rpartition(".")
        if stem and len(ext) <= 10:
            base = stem[: max_length - len(ext) - 1] + "." + ext
        else:
            base = base[:max_length]
    return base


def guess_mime(filename: str, *, default: str = "application/octet-stream") -> str:
    """Deterministic MIME detection. Never ask a model to do this."""
    ext = extension_of(filename)
    if ext in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or default


def extension_of(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    if "." not in base[1:]:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


def is_textual(mime: str) -> bool:
    return mime.startswith("text/") or mime in _TEXTUAL_TYPES


def estimate_tokens(text: str) -> int:
    """Rough token estimate for context-budget arithmetic.

    Deliberately crude and slightly pessimistic: ~3.6 chars/token. Used only
    to decide how much to include, never to bill anything.
    """
    return max(1, int(len(text) / 3.6) + 1)


def truncate_for_budget(text: str, max_tokens: int) -> str:
    """Trim text to an approximate token budget on a whitespace boundary."""
    max_chars = int(max_tokens * 3.6)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    boundary = cut.rfind(" ")
    if boundary > max_chars * 0.6:
        cut = cut[:boundary]
    return cut.rstrip() + " […]"
