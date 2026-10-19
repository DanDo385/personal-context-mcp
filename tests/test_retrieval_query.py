"""Query parsing and project inference: pure code, no database."""

from __future__ import annotations

from uuid import UUID, uuid4

from personal_context_mcp.retrieval.inference import ProjectRow, infer, resolve_reference
from personal_context_mcp.retrieval.query import parse


def test_ordinary_question_becomes_or_of_content_words() -> None:
    q = parse("what did I decide about open-sourcing harbor notes")
    assert set(q.terms) == {"open", "sourcing", "harbor", "notes"}
    match = q.match_query()
    assert " | " in match
    assert " & " not in match


def test_pasted_message_is_reduced_to_key_terms() -> None:
    q = parse(
        "Hi! I hope you're well. I've been thinking a lot lately and wanted to ask you "
        "something about the Harbor Notes app: where are we deploying the server these days?"
    )
    assert q.terms[:2] == ("harbor", "notes")
    assert "thinking" not in q.terms and "hope" not in q.terms
    assert len(q.terms) <= 12


def test_term_cap_keeps_names_first() -> None:
    filler = " ".join(f"word{chr(97 + i)}" for i in range(26))
    q = parse(filler + " Zanzibar", max_terms=5)
    assert "zanzibar" in q.terms
    assert len(q.terms) == 5


def test_quoted_phrase_is_required_and_negation_excludes() -> None:
    q = parse('"raised beds" -basil')
    assert q.phrases == (("raised", "beds"),)
    assert q.excluded == ("basil",)
    assert q.match_query() == "('raised' <-> 'beds') & !'basil'"


def test_identifiers_stay_whole_and_split() -> None:
    q = parse("LH-142")
    assert "lh" in q.terms and "142" in q.terms
    assert "-142" in q.tokens
    q = parse("deploy on fly.io")
    assert "fly.io" in q.tokens


def test_uuid_is_an_exact_lookup() -> None:
    wanted = uuid4()
    q = parse(f"show {wanted}")
    assert q.ids == (wanted,)
    assert not q.is_empty


def test_long_words_tolerate_a_typo() -> None:
    assert "'postgr':*" in parse("postgress").match_query()


def test_filler_only_query_is_empty() -> None:
    assert parse("where did we leave off").is_empty
    assert parse("").is_empty


def test_query_text_cannot_inject_tsquery_operators() -> None:
    q = parse("a' | b ) & ! ( <-> :* 'x")
    for text in (q.match_query(), q.rank_query(), *q.coverage_queries()):
        stripped = text
        for allowed in ("'", "(", ")", "|", "&", "!", "<->", ":*", " "):
            stripped = stripped.replace(allowed, "")
        assert all(c.isalnum() or c in "._-" for c in stripped), text


# ----------------------------------------------------------- projects


def _p(slug: str, name: str, *aliases: str, status: str = "active") -> ProjectRow:
    return ProjectRow(id=uuid4(), slug=slug, name=name, status=status, aliases=aliases)


PROJECTS = [
    _p("harbor-notes", "Harbor Notes", "harbor"),
    _p("lighthouse-api", "Lighthouse API", "lh api"),
    _p("atlas-ledger", "Atlas Ledger"),
    _p("atlas-mobile", "Atlas Mobile"),
]


def test_infer_from_loose_name() -> None:
    found = infer("what is next for harbor notes", PROJECTS)
    assert found.project and found.project.slug == "harbor-notes"
    assert "harbor notes" in found.reason


def test_infer_from_worktree_path_and_repo() -> None:
    for text in (
        "tests fail in ~/Code/worktrees/harbor-notes/fix-search",
        "see github.com/someone/harbor-notes for the code",
        "cd ~/Code/harbor-notes",
    ):
        found = infer(text, PROJECTS)
        assert found.project and found.project.slug == "harbor-notes", text


def test_infer_from_misspelling_and_alias() -> None:
    assert infer("harbr notes status", PROJECTS).project.slug == "harbor-notes"  # type: ignore[union-attr]
    assert infer("lh api rate limit", PROJECTS).project.slug == "lighthouse-api"  # type: ignore[union-attr]


def test_ambiguous_query_offers_both_candidates() -> None:
    found = infer("atlas status", PROJECTS)
    assert found.project is None
    assert {m.project.slug for m in found.candidates} == {"atlas-ledger", "atlas-mobile"}


def test_unrelated_query_infers_nothing() -> None:
    found = infer("a good name for a cat", PROJECTS)
    assert found.project is None and not found.candidates


def test_resolve_reference_accepts_alias_misspelling_and_id() -> None:
    harbor = PROJECTS[0]
    assert resolve_reference("harbor", PROJECTS).project is harbor  # type: ignore[union-attr]
    assert resolve_reference("Harbr Notes", PROJECTS).project is harbor  # type: ignore[union-attr]
    assert resolve_reference(str(harbor.id), PROJECTS).project is harbor  # type: ignore[union-attr]
    assert resolve_reference("nonexistent thing", PROJECTS) is None
    assert isinstance(harbor.id, UUID)


def test_a_single_weak_hint_is_not_a_project() -> None:
    found = infer("notes on the ledger format", PROJECTS)
    assert found.project is None
    assert not found.tied
    assert [m.project.slug for m in found.candidates] == ["atlas-ledger"]
