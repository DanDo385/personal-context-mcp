"""Turn what a caller typed into a Postgres text-search query, deterministically.

``websearch_to_tsquery`` ANDs every word, so an ordinary question ("what did I
decide about open-sourcing harbor notes") matches nothing unless one row holds
every word. Here the content words are ORed instead, and ranking rewards rows
that match more of them. The syntax people already use keeps its meaning:

* ``"quoted phrase"``  the phrase must appear
* ``-word`` / ``-"a phrase"``  rows containing it are dropped
* a UUID  an exact id lookup
* ``LH-142``, ``fly.io``, ``0007_wiki``  identifiers stay whole as well as split

No model is involved. Every lexeme placed into the query text is reduced to
``[a-z0-9._-]`` and single-quoted, so the text handed to ``to_tsquery`` cannot
carry operators the caller did not intend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from uuid import UUID


def _wordset(text: str) -> frozenset[str]:
    return frozenset(text.split())


#: Most content words kept. Long pasted prompts reduce to their key terms.
MAX_TERMS = 12

#: Postgres's English stop words, so the term count matches what the index sees.
_PG_STOPWORDS = _wordset(
    """
    i me my myself we our ours ourselves you your yours yourself yourselves he him his
    himself she her hers herself it its itself they them their theirs themselves what
    which who whom this that these those am is are was were be been being have has had
    having do does did doing a an the and but if or because as until while of at by for
    with about against between into through during before after above below to from up
    down in out on off over under again further then once here there when where why how
    all any both each few more most other some such no nor not only own same so than too
    very s t can will just don should now
    """
)

#: Words that say how someone is asking, not what about. Dropping them keeps a
#: chatty message from matching every summary that happens to say "decided".
_CHAT_WORDS = _wordset(
    """
    hi hello hey thanks thank please pls ok okay yes yeah sure hope well
    remind reminder remember recall tell know knew think thinking thought want wanted
    wanting need needs like would could might may must also really actually just
    ask asking asked question questions wonder wondering lately recently today
    something anything everything nothing thing things stuff lot lots much many
    decide decided decision decisions discuss discussed talk talked said say says
    last time leave left still again us let lets get got go going make made
    did does doing done anymore these days way ways kind sort bit
    """
)

STOPWORDS = _PG_STOPWORDS | _CHAT_WORDS

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_PHRASE_RE = re.compile(r'(-?)"([^"]+)"')
_WORD_RE = re.compile(r"[a-z0-9]+")
_CONTRACTION_RE = re.compile(r"['\u2019](s|ve|re|ll|d|m|t)\b")
_LEXEME_SAFE = re.compile(r"[^a-z0-9._-]")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _quote(lexeme: str) -> str | None:
    cleaned = _LEXEME_SAFE.sub("", lexeme.lower()).strip("._")
    return f"'{cleaned}'" if cleaned and cleaned != "-" else None


@dataclass(frozen=True)
class ParsedQuery:
    """A caller's text, broken into the parts the search statement needs."""

    text: str
    terms: tuple[str, ...] = ()
    phrases: tuple[tuple[str, ...], ...] = ()
    #: Hyphenated words and identifiers: boosted when they match whole.
    compounds: tuple[tuple[str, ...], ...] = ()
    #: Tokens Postgres keeps as one lexeme (hosts, signed numbers).
    tokens: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    excluded_phrases: tuple[tuple[str, ...], ...] = ()
    ids: tuple[UUID, ...] = ()
    dropped: tuple[str, ...] = field(default=(), compare=False)

    @property
    def is_empty(self) -> bool:
        return not (self.terms or self.phrases or self.ids or self.tokens)

    @property
    def keywords(self) -> list[str]:
        """What the search actually used, for reporting back to the caller."""
        return [
            *(" ".join(p) for p in self.phrases),
            *self.terms,
            *(f"-{w}" for w in self.excluded),
        ]

    # -------------------------------------------------------- tsquery text

    @staticmethod
    def _term(term: str) -> str | None:
        """One term, plus a shortened prefix for long words so a typo still matches.

        "postgress" becomes ``('postgress' | 'postgr':*)``, which reaches the
        lexeme Postgres stores for "postgres".
        """
        quoted = _quote(term)
        if quoted is None:
            return None
        if len(term) >= 7 and term.isalpha():
            prefix = _quote(term[: max(5, len(term) - 3)])
            if prefix:
                return f"({quoted} | {prefix}:*)"
        return quoted

    def _alternatives(self) -> list[str]:
        alts = [q for term in self.terms if (q := self._term(term))]
        alts += [q for token in self.tokens if (q := _quote(token))]
        return alts

    @staticmethod
    def _phrase(words: tuple[str, ...]) -> str | None:
        parts = [q for w in words if (q := _quote(w))]
        if not parts:
            return None
        return "(" + " <-> ".join(parts) + ")" if len(parts) > 1 else parts[0]

    def _negations(self) -> list[str]:
        out = [f"!{q}" for w in self.excluded if (q := _quote(w))]
        out += [f"!{p}" for words in self.excluded_phrases if (p := self._phrase(words))]
        return out

    def match_query(self) -> str:
        """Rows must satisfy this: every phrase, any term, no excluded word."""
        required = [p for words in self.phrases if (p := self._phrase(words))]
        alts = self._alternatives()
        clauses: list[str] = []
        if required:
            clauses += required
        elif alts:
            clauses.append("(" + " | ".join(alts) + ")")
        if not clauses:
            return ""
        return " & ".join(clauses + self._negations())

    def rank_query(self) -> str:
        """What ``ts_rank_cd`` scores against: every term, phrase and compound, ORed."""
        return " | ".join([*self._alternatives(), *self._phrases_and_compounds()])

    def _phrases_and_compounds(self) -> list[str]:
        return [p for words in (*self.phrases, *self.compounds) if (p := self._phrase(words))]

    def phrase_query(self) -> str:
        """Rows matching a phrase or a whole compound ("last-writer-wins") get a boost."""
        return " | ".join(self._phrases_and_compounds())

    def coverage_queries(self) -> list[str]:
        """One tsquery text per thing asked for; a row's coverage is how many it matches."""
        out = self._alternatives()
        out += [p for words in self.phrases if (p := self._phrase(words))]
        return out


def parse(text: str | None, *, max_terms: int = MAX_TERMS) -> ParsedQuery:
    """Parse a short query or a pasted message into search parts."""
    raw = (text or "").strip()
    if not raw:
        return ParsedQuery(text="")

    ids = tuple(dict.fromkeys(UUID(m) for m in _UUID_RE.findall(raw)))
    rest = _UUID_RE.sub(" ", raw)

    phrases: list[tuple[str, ...]] = []
    excluded_phrases: list[tuple[str, ...]] = []
    for negated, body in _PHRASE_RE.findall(rest):
        words = tuple(_words(body))
        if not words:
            continue
        (excluded_phrases if negated else phrases).append(words)
    rest = _PHRASE_RE.sub(" ", rest)

    candidates: list[tuple[str, bool]] = []  # (word, looks like a name or id)
    compounds: list[tuple[str, ...]] = []
    tokens: list[str] = []
    excluded: list[str] = []
    dropped: list[str] = []

    for position, chunk in enumerate(rest.split()):
        if chunk.upper() == "OR":
            continue
        if chunk.startswith("-") and len(chunk) > 1 and chunk[1].isalnum():
            excluded += _words(chunk[1:])
            continue
        lowered = _CONTRACTION_RE.sub("", chunk.lower()).strip(".,;:!?()[]{}'\"")
        parts = _words(lowered)
        if not parts:
            continue
        named = (chunk[:1].isupper() and position > 0) or any(c.isdigit() for c in chunk)
        if len(parts) > 1:
            compounds.append(tuple(parts))
            # Postgres keeps hosts ("fly.io") whole and reads "LH-142" as "lh" and "-142".
            if "." in lowered and "/" not in lowered:
                tokens.append(lowered)
            for part in parts:
                if part.isdigit() and f"-{part}" in lowered:
                    tokens.append(f"-{part}")
            for part in parts:
                if part not in STOPWORDS:
                    candidates.append((part, True))
            continue
        word = parts[0]
        if word in STOPWORDS or (len(word) == 1 and not word.isdigit()):
            dropped.append(word)
            continue
        candidates.append((word, named))

    # Names and identifiers first when the cap bites, then the order typed.
    unique: dict[str, bool] = {}
    for word, named in candidates:
        unique[word] = unique.get(word, False) or named
    ordered = sorted(unique, key=lambda w: (not unique[w], list(unique).index(w)))
    terms = ordered[:max_terms]
    dropped += ordered[max_terms:]

    return ParsedQuery(
        text=raw,
        terms=tuple(terms),
        phrases=tuple(phrases),
        compounds=tuple(dict.fromkeys(compounds)),
        tokens=tuple(dict.fromkeys(tokens)),
        excluded=tuple(dict.fromkeys(excluded)),
        excluded_phrases=tuple(excluded_phrases),
        ids=ids,
        dropped=tuple(dropped),
    )
