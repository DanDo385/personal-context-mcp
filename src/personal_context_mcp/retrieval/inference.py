"""Which project a request is about, decided by code and stated with a reason.

Matching works on a handful of project rows, so it runs in Python: exact
slug, name or alias first; then a repository or worktree path; then the name
written loosely ("personal context mcp"); then a close misspelling; then a
shared distinctive word. A tie is never broken silently: the top two are
returned as candidates instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

_WORD_RE = re.compile(r"[a-z0-9]+")
_PATH_RES = (
    re.compile(r"(?:^|[\s(])~?/?(?:[\w.-]+/)*code/worktrees/([\w.-]+)(?:/[\w.-]+)?", re.I),
    re.compile(r"(?:^|[\s(])~?/?(?:[\w.-]+/)*code/([\w.-]+)", re.I),
    re.compile(r"github\.com[/:]([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/\s]|$)", re.I),
)


def _wordset(text: str) -> frozenset[str]:
    return frozenset(text.split())


#: Words too common in project names to identify one on their own.
_GENERIC = _wordset(
    """
    project projects app apps api site web website data server service tool tools notes
    note personal context mcp the and for new old main repo code dev test v1 v2 v3 my
    """
)

#: Score needed to act on an inference. Below it, candidates are offered instead.
INFER_THRESHOLD = 0.7
#: Two candidates this close are a tie.
TIE_MARGIN = 0.05


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


@dataclass(frozen=True)
class ProjectRow:
    id: UUID
    slug: str
    name: str
    status: str = "active"
    aliases: tuple[str, ...] = ()

    @classmethod
    def from_record(cls, row: Any) -> ProjectRow:
        metadata = row["metadata"] or {}
        aliases = metadata.get("aliases") if isinstance(metadata, dict) else None
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            status=row["status"],
            aliases=tuple(str(a) for a in aliases or () if str(a).strip()),
        )

    def spellings(self) -> list[tuple[str, ...]]:
        """Every way of writing this project, as word sequences."""
        seen: dict[tuple[str, ...], None] = {}
        for text in (self.slug, self.name, *self.aliases):
            words = tuple(_words(text))
            if words:
                seen[words] = None
        return list(seen)


@dataclass
class Match:
    project: ProjectRow
    score: float
    reason: str


@dataclass
class Inference:
    project: ProjectRow | None
    reason: str
    candidates: list[Match] = field(default_factory=list)
    #: How sure the match is, 0 to 1. Only set when a project was chosen.
    score: float = 0.0
    #: True when two candidates scored the same: the query really could mean
    #: either. False when the only candidate is a weak hint.
    tied: bool = False


def resolve_reference(reference: str, projects: list[ProjectRow]) -> Match | None:
    """Resolve what a caller passed as ``project``: slug, name, alias, id or near miss."""
    ref = reference.strip()
    if not ref:
        return None
    lowered = ref.lower()
    words = tuple(_words(ref))
    for p in projects:
        if lowered == str(p.id) or lowered == p.slug or lowered == p.name.lower():
            return Match(p, 1.0, f"matches the project {p.slug!r}")
    for p in projects:
        if words and words in p.spellings():
            return Match(p, 0.97, f"{ref!r} is a name or alias of {p.slug!r}")
    best: Match | None = None
    for p in projects:
        for spelling in p.spellings():
            ratio = SequenceMatcher(None, " ".join(words), " ".join(spelling)).ratio()
            if ratio >= 0.8 and (best is None or ratio > best.score):
                best = Match(p, ratio, f"{ref!r} looks like a misspelling of {p.slug!r}")
    return best


def _contains(haystack: list[str], needle: tuple[str, ...]) -> bool:
    n = len(needle)
    return any(tuple(haystack[i : i + n]) == needle for i in range(len(haystack) - n + 1))


def _fuzzy(haystack: list[str], needle: tuple[str, ...]) -> tuple[float, str] | None:
    """Best close spelling of ``needle`` among same-length word windows."""
    target = " ".join(needle)
    if len(target) < 5:
        return None
    n = len(needle)
    matcher = SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(target)
    best: tuple[float, str] | None = None
    first = needle[0][:1]
    seen: set[str] = set()
    for i in range(len(haystack) - n + 1):
        # A typo almost never changes the first letter; checking it first
        # keeps a long message cheap.
        if haystack[i][:1] != first:
            continue
        window = " ".join(haystack[i : i + n])
        if window in seen:
            continue
        seen.add(window)
        # A ratio of 0.85 needs lengths within about 30% of each other.
        if window == target or abs(len(window) - len(target)) > len(target) * 0.3:
            continue
        matcher.set_seq1(window)
        if matcher.real_quick_ratio() < 0.85 or matcher.quick_ratio() < 0.85:
            continue
        ratio = matcher.ratio()
        if ratio >= 0.85 and (best is None or ratio > best[0]):
            best = (ratio, window)
    return best


def infer(text: str, projects: list[ProjectRow]) -> Inference:
    """Read which project ``text`` is about. Never guesses between close candidates."""
    if not text.strip() or not projects:
        return Inference(None, "no project was passed and the query names none")

    words = _words(text)
    scores: dict[UUID, Match] = {}

    def offer(project: ProjectRow, score: float, reason: str) -> None:
        if project.status != "active":
            score *= 0.8
        current = scores.get(project.id)
        if current is None or score > current.score:
            scores[project.id] = Match(project, score, reason)

    by_slug = {p.slug: p for p in projects}
    for pattern in _PATH_RES:
        for found in pattern.finditer(text):
            repo = found.group(found.lastindex or 1).lower()
            if repo in by_slug:
                offer(by_slug[repo], 1.0, f"the path or repository {found.group(0).strip()!r}")

    for p in projects:
        for spelling in p.spellings():
            if _contains(words, spelling):
                whole = len(spelling) > 1 or spelling[0] not in _GENERIC
                score = 0.95 if len(spelling) > 1 else (0.85 if whole else 0.0)
                if score:
                    offer(p, score, f"the query names it: {' '.join(spelling)!r}")
            elif (close := _fuzzy(words, spelling)) is not None:
                ratio, seen = close
                offer(p, 0.9 * ratio, f"{seen!r} looks like {' '.join(spelling)!r}")

        distinctive = {w for s in p.spellings() for w in s if w not in _GENERIC and len(w) >= 4}
        shared = sorted(distinctive & set(words))
        if shared:
            offer(p, 0.6, f"the query shares the word {shared[0]!r}")

    ranked = sorted(scores.values(), key=lambda m: m.score, reverse=True)
    if not ranked:
        return Inference(None, "no project was passed and the query names none")
    top = ranked[0]
    tied = [m for m in ranked[1:] if top.score - m.score <= TIE_MARGIN]
    if tied:
        candidates = [top, *tied]
        names = " and ".join(repr(m.project.slug) for m in candidates)
        return Inference(None, f"the query could mean {names}", candidates=candidates, tied=True)
    if top.score < INFER_THRESHOLD:
        return Inference(
            None,
            f"not sure enough to pick a project: closest is {top.project.slug!r} ({top.reason})",
            candidates=[top],
        )
    return Inference(top.project, top.reason, candidates=[], score=round(top.score, 3))
