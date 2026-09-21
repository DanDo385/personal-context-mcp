"""Every Jev question and every routing threshold, in one file.

TypeSafe's own guidance is that questions and thresholds are the part humans
must actually review, so they live here rather than being scattered through
the services that consume them. Nothing in this module calls an API.

Conventions followed from the TypeSafe docs:

* **Choice** for a fixed set of destinations, always including a no-match
  option, because the model cannot pick an option we did not offer.
* **Score** for an ordered rubric whose levels describe concrete situations
  that stand on their own.
* **Noul** for an independent yes/no; one per label, never a multi-label
  question. A Noul near 0.5 means "genuinely unsure", not "medium".
* Independent questions about the same state go in ONE request - they run in
  parallel and are far cheaper than separate calls.

Bump ``QUESTIONS_VERSION`` whenever a question's wording or a criteria set
changes. It is recorded on every classification_event so older results stay
interpretable after a change.
"""

from __future__ import annotations

from typing import Any

from typesafe_sdk import Choice, Noul, Score

QUESTIONS_VERSION = "2026-09-21.1"

#: Offered whenever a Choice ranges over projects, so "none of these" is
#: always reachable.
UNASSIGNED = "unassigned"

# --------------------------------------------------------------------------
# Shared rubrics
# --------------------------------------------------------------------------

#: Index 0..4. Normalise with `score / 4` to get a 0..1 importance.
IMPORTANCE_LEVELS: list[str] = [
    "Ephemeral. Small talk, a one-off lookup, or a question fully answered in "
    "the moment with nothing that would matter later.",
    "Minor reusable context. A passing detail that might save a little time if "
    "it came up again, but nothing would go wrong if it were forgotten.",
    "Useful durable context. A stable fact, preference or piece of research "
    "that a future session would benefit from knowing without being told again.",
    "Important. A decision, commitment, or finding that later work will be "
    "built on; forgetting it would cause rework or a contradiction.",
    "Critical long-term context. Foundational to an ongoing project or to how "
    "the person wants to be worked with; forgetting it would break continuity.",
]

#: Index 0..3.
LONG_TERM_VALUE_LEVELS: list[str] = [
    "Disposable. A scratch file, a throwaway test, or a duplicate of something already kept.",
    "Situational. Worth keeping while the current piece of work is live, but "
    "unlikely to be opened again after it ships.",
    "Durable. A finished output, dataset or document that is likely to be "
    "referred back to or reused.",
    "Reference. A definitive artifact that anchors a project or is expected to "
    "be returned to repeatedly over a long period.",
]

#: Where a piece of knowledge belongs in the vault. Keys map to the roots in
#: PC_OBSIDIAN_VAULT_ROOTS; Python resolves the key to a real path, never Jev.
DESTINATION_CRITERIA: dict[str, str] = {
    "project": "Work on a specific, named, ongoing project: its state, plan, "
    "decisions, or outputs.",
    "research": "Investigation of a topic with findings and sources, gathered "
    "to answer a question rather than to run a project.",
    "wiki": "A durable explanation of how something works, written to be read "
    "again later independently of why it was first looked up.",
    "concept": "A single idea, term or mental model being defined or refined.",
    "resource": "A pointer to something external worth keeping: a tool, "
    "library, article, product, or person's work.",
    "daily": "A log of what happened on a particular day, only meaningful in "
    "the context of that date.",
    "inbox": "Clearly worth keeping, but which of the above it belongs in is "
    "genuinely ambiguous from what is here.",
    "archive_only": "Not worth surfacing as knowledge. Keep the conversation "
    "record, but do not create a human-readable note.",
}

ARTIFACT_CATEGORY_CRITERIA: dict[str, str] = {
    "project_artifact": "An output belonging to a specific named project: its "
    "deliverable, its source, or a figure produced for it.",
    "research_asset": "Material gathered or produced while investigating a "
    "topic: extracted data, charts, annotated sources.",
    "reference": "Something kept for lookup rather than as work product: a "
    "spec, a manual, a downloaded standard.",
    "generated_media": "An image, video or audio file produced by a model.",
    "uploaded_media": "An image, video or audio file the person supplied.",
    "code_project": "Source code intended to run, spanning one or more files, "
    "not tied to a single named project.",
    "document": "Prose meant to be read: a report, letter, spec, or article.",
    "dataset": "Structured records meant to be analysed rather than read.",
    "presentation": "Slides.",
    "spreadsheet": "A workbook of tabular data with formulas or multiple sheets.",
    "archive": "A compressed bundle whose contents have not been separated out.",
    "other": "None of the above describes it.",
}


# --------------------------------------------------------------------------
# Question sets
# --------------------------------------------------------------------------


def checkpoint_questions(project_candidates: dict[str, str]) -> dict[str, Any]:
    """Questions asked once per substantive checkpoint or imported session.

    ``project_candidates`` maps project slug -> one-line description, already
    narrowed by retrieval (see PC_JEV_MAX_PROJECT_CANDIDATES). Passing an
    unbounded universe of projects makes the Choice worse, not better.
    """
    questions: dict[str, Any] = {
        "save_durable_memory": Noul(
            instructions=(
                "The conversation state contains at least one fact, preference, "
                "commitment or finding that a future session would need to be told "
                "again if it were not recorded now. Judge the content, not its "
                "length: a single sentence can qualify and a long transcript "
                "of a resolved lookup can fail."
            )
        ),
        "promote_to_obsidian": Noul(
            instructions=(
                "This would be worth having as a written note a person would "
                "open and read on its own, months from now, away from this "
                "conversation. It is not enough that it was useful in the "
                "moment or that it is already captured as structured state."
            )
        ),
        "updates_project": Noul(
            instructions=(
                "This materially changes the state of one of the listed active "
                "projects: its status, plan, scope, or a decision it rests on. "
                "Merely mentioning a project, or working on it without changing "
                "where it stands, does not count."
            )
        ),
        "creates_task": Noul(
            instructions=(
                "This establishes a concrete action that is intended to be done "
                "and is not yet done. A hypothetical, a suggestion that was not "
                "taken up, or something already completed in the conversation "
                "does not count."
            )
        ),
        "sensitive": Noul(
            instructions=(
                "The content includes personal, financial, medical, legal or "
                "security-sensitive material that the person would not want "
                "copied into a synced notes vault or sent to a third-party API "
                "without being asked first."
            )
        ),
        "destination": Choice(
            instructions=(
                "If this were written up as a single note for a personal "
                "knowledge vault, which section would it belong in? Choose by "
                "what the content is, not by which section is emptiest."
            ),
            criteria=DESTINATION_CRITERIA,
        ),
        "importance": Score(
            instructions=(
                "How much would future work suffer if this conversation were "
                "not retained at all beyond its raw transcript?"
            ),
            criteria=IMPORTANCE_LEVELS,
        ),
    }
    if project_candidates:
        questions["project"] = Choice(
            instructions=(
                "Which of these existing projects is this conversation about? "
                "Choose a project only if the conversation is substantively "
                "about that work, not merely adjacent to it."
            ),
            criteria={**project_candidates, UNASSIGNED: _unassigned_text("conversation")},
        )
    return questions


def artifact_questions(
    project_candidates: dict[str, str], *, ask_project: bool = True
) -> dict[str, Any]:
    """Questions asked once per artifact, after deterministic inspection.

    MIME type, extension, size, hash and file tree are established in code
    before this runs and are supplied as state. Jev is asked only the parts
    that need semantic judgement.
    """
    questions: dict[str, Any] = {
        "belongs_to_project": Noul(
            instructions=(
                "This artifact was produced for, or is part of, one specific "
                "named project rather than being general-purpose material."
            )
        ),
        "semantic_category": Choice(
            instructions=(
                "What kind of thing is this artifact, judged by its purpose and "
                "contents rather than only by its file extension?"
            ),
            criteria=ARTIFACT_CATEGORY_CRITERIA,
        ),
        "promote_to_vault": Noul(
            instructions=(
                "A person browsing their notes vault would want to find this "
                "artifact represented there, rather than it living only in cloud "
                "storage reachable through a search."
            )
        ),
        "needs_human_review": Noul(
            instructions=(
                "Filing this automatically would be a mistake: its purpose is "
                "unclear from what is given, it looks sensitive, or it could "
                "plausibly belong to more than one project."
            )
        ),
        "long_term_value": Score(
            instructions="How likely is this artifact to be wanted again later?",
            criteria=LONG_TERM_VALUE_LEVELS,
        ),
    }
    if ask_project and project_candidates:
        questions["project"] = Choice(
            instructions=(
                "Which existing project does this artifact belong to? This is "
                "asked speculatively alongside belongs_to_project; if that "
                "answer is no, this answer is not used."
            ),
            criteria={**project_candidates, UNASSIGNED: _unassigned_text("artifact")},
        )
    return questions


def note_questions(project_candidates: dict[str, str]) -> dict[str, Any]:
    questions: dict[str, Any] = {
        "destination": Choice(
            instructions=(
                "Which section of a personal knowledge vault should this note "
                "live in, judged by what the note is about?"
            ),
            criteria=DESTINATION_CRITERIA,
        ),
        "long_term_value": Score(
            instructions="How likely is this note to be read again later?",
            criteria=LONG_TERM_VALUE_LEVELS,
        ),
        "needs_human_review": Noul(
            instructions=(
                "Filing this note automatically would be a mistake: its subject "
                "is ambiguous, or it appears to duplicate or contradict an "
                "existing note listed in the state."
            )
        ),
        "sensitive": Noul(
            instructions=(
                "This note contains personal, financial, medical, legal or "
                "security-sensitive material that should not be written into a "
                "synced vault without being asked first."
            )
        ),
    }
    if project_candidates:
        questions["project"] = Choice(
            instructions="Which existing project is this note about?",
            criteria={**project_candidates, UNASSIGNED: _unassigned_text("note")},
        )
    return questions


def list_candidate_questions(list_candidates: dict[str, str]) -> dict[str, Any]:
    """Which existing list does a new item belong on?

    Deliberately never asked to *create* a list: creating lists from model
    judgement is how you end up with four spellings of the same list.
    """
    return {
        "is_actionable_item": Noul(
            instructions=(
                "The candidate text is a discrete entry a person would keep on "
                "a list: a task, a name to follow up, a book to read, a thing to "
                "buy or an idea to revisit. Narrative prose or a question is not."
            )
        ),
        "list": Choice(
            instructions=(
                "Which of the person's existing lists does this entry belong on? "
                "Choose 'none' if none of them is a natural home for it."
            ),
            criteria={**list_candidates, "none": "No existing list is a natural home."},
        ),
    }


def _unassigned_text(noun: str) -> str:
    return (
        f"None of the listed projects. Choose this when the {noun} is general, "
        f"spans several of them, or concerns a project not listed here."
    )


# --------------------------------------------------------------------------
# Confidence policy
# --------------------------------------------------------------------------
#
# Thresholds scale with the cost of being wrong. These are engineering
# defaults to be tuned against real results, not universal truths - every one
# is overridable from configuration.

#: Reversible, low-cost actions: associating a memory with a project, filing
#: an artifact under a prefix, tagging. Default PC_AUTO_ROUTE_CONFIDENCE.
ACTION_REVERSIBLE = "reversible"

#: Actions that write to the human's vault or change project state. These are
#: not destructive but they are *visible*, so they need more certainty.
ACTION_VISIBLE = "visible"

#: Actions that supersede or retract existing content.
ACTION_SUPERSEDING = "superseding"

#: Multipliers applied to PC_AUTO_ROUTE_CONFIDENCE per action class.
ACTION_CONFIDENCE_MULTIPLIER: dict[str, float] = {
    ACTION_REVERSIBLE: 1.00,
    ACTION_VISIBLE: 1.06,
    ACTION_SUPERSEDING: 1.12,
}

#: Noul probability above which a yes/no answer is treated as a yes. Nouls
#: carry no confidence value, so the probability itself is the signal and
#: anything in the middle band is deferred rather than guessed.
NOUL_YES = 0.80
NOUL_NO = 0.20
