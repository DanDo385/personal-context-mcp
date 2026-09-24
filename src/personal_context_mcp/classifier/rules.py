"""Deterministic classification.

Two jobs:

1. Answer everything ordinary code can answer, *before* Jev is asked anything.
   File type, size, whether a tree looks like a web project, whether a hash is
   already known - none of that is a judgement call.
2. Be the fallback when Jev is unavailable. The system must stay usable when
   the classifier is down; it just becomes more conservative and queues the
   object for reclassification.
"""

from __future__ import annotations

from typing import Any

from personal_context_mcp.classifier.base import Answer, ClassificationResult
from personal_context_mcp.classifier.questions import (
    IMPORTANCE_LEVELS,
    LONG_TERM_VALUE_LEVELS,
    QUESTIONS_VERSION,
    UNASSIGNED,
)
from personal_context_mcp.util import extension_of, guess_mime, is_textual

#: Extension -> artifact_type. Purely mechanical; never ask a model for this.
_TYPE_BY_EXTENSION: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".avif": "image",
    ".heic": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".svg": "diagram",
    ".mmd": "diagram",
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".avi": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".aac": "audio",
    ".pdf": "document",
    ".docx": "document",
    ".doc": "document",
    ".odt": "document",
    ".rtf": "document",
    ".txt": "document",
    ".md": "document",
    ".pptx": "presentation",
    ".ppt": "presentation",
    ".odp": "presentation",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".ods": "spreadsheet",
    ".csv": "dataset",
    ".tsv": "dataset",
    ".parquet": "dataset",
    ".jsonl": "dataset",
    ".ndjson": "dataset",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".tgz": "archive",
    ".7z": "archive",
    ".rar": "archive",
    ".ipynb": "notebook",
}

_CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".rs",
        ".go",
        ".sol",
        ".java",
        ".rb",
        ".php",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".swift",
        ".kt",
        ".sh",
        ".sql",
    }
)

_WEB_MARKERS = frozenset({"index.html", "vite.config.js", "vite.config.ts", "package.json"})


def detect_artifact_type(paths: list[str]) -> str:
    """Artifact type from the file tree alone. Deterministic and cheap."""
    if not paths:
        return "other"

    basenames = {p.rsplit("/", 1)[-1].lower() for p in paths}
    extensions = [extension_of(p) for p in paths]

    if basenames & _WEB_MARKERS and any(e in (".html", ".css", ".js", ".ts") for e in extensions):
        return "web_project"

    code_count = sum(1 for e in extensions if e in _CODE_EXTENSIONS)
    if len(paths) > 1 and code_count >= max(2, len(paths) // 2):
        return "code_project"

    if len(paths) == 1:
        single = _TYPE_BY_EXTENSION.get(extensions[0])
        if single:
            return single
        if extensions[0] in _CODE_EXTENSIONS:
            return "code_project"
        return "document" if is_textual(guess_mime(paths[0])) else "other"

    # Mixed tree: take the most common resolvable type, else "other".
    counts: dict[str, int] = {}
    for ext in extensions:
        kind = _TYPE_BY_EXTENSION.get(ext) or ("code_project" if ext in _CODE_EXTENSIONS else None)
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else "other"


def looks_multi_file_project(paths: list[str]) -> bool:
    return len(paths) > 1 and any("/" in p for p in paths)


class RulesClassifier:
    """Conservative deterministic classifier.

    Its answers are deliberately unconfident: it reports low confidence and
    routes to review or inbox rather than guessing, so that a Jev outage
    degrades into "nothing was filed wrongly" rather than "everything landed
    in the first project alphabetically".
    """

    name = "rules"

    def __init__(self, *, degraded: bool = False, error: str | None = None) -> None:
        self._degraded = degraded
        self._error = error

    def _result(self, answers: dict[str, Answer], input_hash: str) -> ClassificationResult:
        return ClassificationResult(
            answers=answers,
            classifier=self.name,
            model=None,
            questions_version=QUESTIONS_VERSION,
            input_hash=input_hash,
            degraded=self._degraded,
            error=self._error,
        )

    async def classify_conversation(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        return await self.classify_checkpoint(state, project_candidates)

    async def classify_checkpoint(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        # Signals we can read without judgement: did the caller actually
        # supply decisions, facts or tasks?
        has_decisions = bool(state.get("decisions"))
        has_facts = bool(state.get("facts"))
        has_tasks = bool(state.get("tasks"))
        has_project_update = bool(state.get("project_updates"))
        stated_project = state.get("stated_project")

        answers = {
            "save_durable_memory": Answer("noul", 0.9 if (has_facts or has_decisions) else 0.3),
            "creates_task": Answer("noul", 0.9 if has_tasks else 0.1),
            "updates_project": Answer("noul", 0.9 if has_project_update else 0.2),
            # Without semantic judgement we do not promote to the vault. A
            # false negative costs a note that can be created later; a false
            # positive writes into the user's filesystem.
            "promote_to_obsidian": Answer("noul", 0.15),
            "sensitive": Answer("noul", 0.5),
            "destination": Answer(
                "choice",
                "inbox",
                confidence=0.3,
                probabilities={"inbox": 0.3, "archive_only": 0.3},
            ),
            "importance": Answer(
                "score",
                2.0 if (has_decisions or has_facts) else 1.0,
                confidence=0.3,
                legend={str(i): level for i, level in enumerate(IMPORTANCE_LEVELS)},
            ),
        }
        if project_candidates:
            # Only an exact caller-supplied project is trusted here.
            if stated_project and stated_project in project_candidates:
                answers["project"] = Answer(
                    "choice",
                    stated_project,
                    confidence=1.0,
                    probabilities={stated_project: 1.0},
                )
            else:
                answers["project"] = Answer(
                    "choice",
                    UNASSIGNED,
                    confidence=0.25,
                    probabilities={UNASSIGNED: 0.25},
                )
        return self._result(answers, str(state.get("input_hash", "")))

    async def classify_artifact(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        paths = list(state.get("file_paths") or [])
        artifact_type = state.get("artifact_type") or detect_artifact_type(paths)
        category = {
            "web_project": "code_project",
            "code_project": "code_project",
            "image": "uploaded_media",
            "video": "uploaded_media",
            "audio": "uploaded_media",
            "document": "document",
            "dataset": "dataset",
            "presentation": "presentation",
            "spreadsheet": "spreadsheet",
            "archive": "archive",
            "notebook": "code_project",
            "diagram": "research_asset",
        }.get(artifact_type, "other")
        if state.get("source_type") == "generated_by_model" and category == "uploaded_media":
            category = "generated_media"

        stated_project = state.get("stated_project")
        answers = {
            "belongs_to_project": Answer("noul", 0.9 if stated_project else 0.2),
            "semantic_category": Answer(
                "choice", category, confidence=0.6, probabilities={category: 0.6}
            ),
            "promote_to_vault": Answer("noul", 0.15),
            "needs_human_review": Answer("noul", 0.5 if self._degraded else 0.3),
            "long_term_value": Answer(
                "score",
                1.0,
                confidence=0.3,
                legend={str(i): lvl for i, lvl in enumerate(LONG_TERM_VALUE_LEVELS)},
            ),
        }
        if project_candidates:
            chosen = stated_project if stated_project in project_candidates else UNASSIGNED
            answers["project"] = Answer(
                "choice",
                chosen,
                confidence=1.0 if chosen != UNASSIGNED else 0.25,
                probabilities={chosen: 1.0 if chosen != UNASSIGNED else 0.25},
            )
        return self._result(answers, str(state.get("input_hash", "")))

    async def classify_note(
        self, state: dict[str, Any], project_candidates: dict[str, str]
    ) -> ClassificationResult:
        stated = state.get("stated_class")
        destination = (
            stated
            if stated in ("project", "research", "wiki", "concept", "resource", "daily")
            else "inbox"
        )
        answers = {
            "destination": Answer(
                "choice",
                destination,
                confidence=0.9 if stated else 0.3,
                probabilities={destination: 0.9 if stated else 0.3},
            ),
            "long_term_value": Answer(
                "score",
                2.0,
                confidence=0.3,
                legend={str(i): lvl for i, lvl in enumerate(LONG_TERM_VALUE_LEVELS)},
            ),
            "needs_human_review": Answer("noul", 0.5 if self._degraded else 0.2),
            "sensitive": Answer("noul", 0.5),
        }
        if project_candidates:
            stated_project = state.get("stated_project")
            chosen = stated_project if stated_project in project_candidates else UNASSIGNED
            answers["project"] = Answer(
                "choice",
                chosen,
                confidence=1.0 if chosen != UNASSIGNED else 0.25,
                probabilities={chosen: 1.0},
            )
        return self._result(answers, str(state.get("input_hash", "")))

    async def classify_list_candidate(
        self, state: dict[str, Any], list_candidates: dict[str, str]
    ) -> ClassificationResult:
        stated = state.get("stated_list")
        chosen = stated if stated in list_candidates else "none"
        return self._result(
            {
                "is_actionable_item": Answer("noul", 0.9 if stated else 0.5),
                "list": Answer(
                    "choice",
                    chosen,
                    confidence=1.0 if chosen != "none" else 0.2,
                    probabilities={chosen: 1.0 if chosen != "none" else 0.2},
                ),
            },
            str(state.get("input_hash", "")),
        )

    async def classify_note_link(self, state: dict[str, Any]) -> ClassificationResult:
        """No semantic judgement without Jev: stay undecided, so nothing is
        linked. Deterministic title-mention links are made before this is
        ever asked (wiki/service.py)."""
        return self._result(
            {"should_link": Answer("noul", 0.5), "navigation_value": Answer("noul", 0.5)},
            str(state.get("input_hash", "")),
        )

    async def classify_moc(
        self, state: dict[str, Any], moc_candidates: dict[str, str]
    ) -> ClassificationResult:
        return self._result({}, str(state.get("input_hash", "")))

    async def aclose(self) -> None:
        return None
