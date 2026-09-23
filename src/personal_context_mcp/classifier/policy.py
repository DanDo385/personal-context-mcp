"""Confidence policy: turning typed answers into decisions.

The answer says *what*; confidence says *whether to act*. Three bands, with
the acting threshold scaled by how visible or reversible the action is:

    >= auto_route * multiplier   act automatically
    >= review_confidence         act, but flag for human review
    <  review_confidence         do not act; leave unassigned / archive-only

A Noul carries no confidence value - its probability *is* the signal - so
Nouls use a separate band (NOUL_YES / NOUL_NO) and anything in between is
treated as "unsure", never as "slightly yes".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from personal_context_mcp.classifier.base import ClassificationResult
from personal_context_mcp.classifier.questions import (
    ACTION_CONFIDENCE_MULTIPLIER,
    ACTION_REVERSIBLE,
    IMPORTANCE_LEVELS,
    LONG_TERM_VALUE_LEVELS,
    NOUL_NO,
    NOUL_YES,
    UNASSIGNED,
)
from personal_context_mcp.config import Settings


class Band(StrEnum):
    AUTO = "auto"
    REVIEW = "review"
    DEFER = "defer"


class Verdict(StrEnum):
    YES = "yes"
    NO = "no"
    UNSURE = "unsure"


@dataclass(frozen=True)
class Routing:
    """What the policy engine decided to do with one classified object."""

    project_slug: str | None
    project_band: Band
    destination: str | None
    destination_band: Band
    importance: float
    save_memory: Verdict
    promote_to_vault: Verdict
    updates_project: Verdict
    creates_task: Verdict
    sensitive: Verdict
    needs_review: bool
    review_reasons: list[str]
    action_taken: str
    confidence: float

    def as_json(self) -> dict[str, Any]:
        return {
            "project": self.project_slug,
            "project_band": self.project_band.value,
            "destination": self.destination,
            "destination_band": self.destination_band.value,
            "importance": round(self.importance, 3),
            "save_memory": self.save_memory.value,
            "promote_to_vault": self.promote_to_vault.value,
            "updates_project": self.updates_project.value,
            "creates_task": self.creates_task.value,
            "sensitive": self.sensitive.value,
            "needs_review": self.needs_review,
            "review_reasons": self.review_reasons,
            "action_taken": self.action_taken,
            "confidence": round(self.confidence, 3),
        }


class PolicyEngine:
    def __init__(self, settings: Settings, *, historical_import: bool = False) -> None:
        self._auto = (
            settings.import_auto_route_confidence
            if historical_import
            else settings.auto_route_confidence
        )
        self._review = settings.review_confidence
        self._historical = historical_import
        self._allow_vault_promotion = (
            settings.import_promote_to_vault if historical_import else True
        )

    # ---------------------------------------------------------------- bands

    def band(self, confidence: float, *, action: str = ACTION_REVERSIBLE) -> Band:
        threshold = min(1.0, self._auto * ACTION_CONFIDENCE_MULTIPLIER.get(action, 1.0))
        if confidence >= threshold:
            return Band.AUTO
        if confidence >= self._review:
            return Band.REVIEW
        return Band.DEFER

    @staticmethod
    def verdict(probability: float) -> Verdict:
        if probability >= NOUL_YES:
            return Verdict.YES
        if probability <= NOUL_NO:
            return Verdict.NO
        return Verdict.UNSURE

    # ----------------------------------------------------------- evaluation

    def route(self, result: ClassificationResult) -> Routing:
        review_reasons: list[str] = []

        project, project_conf = result.choice("project")
        project_band = self.band(project_conf, action=ACTION_REVERSIBLE)
        if project == UNASSIGNED:
            project = None
        if project and project_band is Band.DEFER:
            project = None
        elif project and project_band is Band.REVIEW:
            review_reasons.append("ambiguous_project")

        destination, destination_conf = result.choice("destination")
        destination_band = self.band(destination_conf, action="visible")
        if destination and destination_band is Band.DEFER:
            destination, review_reasons = "inbox", [*review_reasons, "ambiguous_destination"]
        elif destination and destination_band is Band.REVIEW:
            review_reasons.append("ambiguous_destination")

        importance, _ = result.score("importance", levels=len(IMPORTANCE_LEVELS))
        if "long_term_value" in result.answers:
            importance, _ = result.score("long_term_value", levels=len(LONG_TERM_VALUE_LEVELS))

        save_memory = self.verdict(result.noul("save_durable_memory", 0.5))
        promote = self.verdict(
            result.noul("promote_to_obsidian", result.noul("promote_to_vault", 0.0))
        )
        updates_project = self.verdict(result.noul("updates_project", 0.0))
        creates_task = self.verdict(result.noul("creates_task", 0.0))
        sensitive = self.verdict(result.noul("sensitive", 0.0))
        explicit_review = self.verdict(result.noul("needs_human_review", 0.0))

        if explicit_review is Verdict.YES:
            review_reasons.append("classifier_requested_review")
        if sensitive is not Verdict.NO and promote is not Verdict.NO:
            # Sensitive content never auto-promotes into the vault, whatever
            # the promotion answer said. This only ever *downgrades*: an
            # uncertain sensitivity reading must not make a "no" promotion
            # look more permissive than the classifier actually said.
            if promote is Verdict.YES:
                review_reasons.append("sensitive_promotion")
            promote = Verdict.UNSURE if sensitive is Verdict.UNSURE else Verdict.NO

        if not self._allow_vault_promotion and promote is Verdict.YES:
            # Historical bulk imports do not flood the vault. The note can
            # still be created later from the review queue.
            promote = Verdict.NO
            review_reasons.append("historical_import_promotion_withheld")

        if result.degraded:
            review_reasons.append("classifier_degraded")

        needs_review = bool(review_reasons)
        confidence = max(project_conf, destination_conf)

        if result.degraded:
            action = "fallback"
        elif needs_review:
            action = "review"
        elif project or (destination and destination != "archive_only"):
            action = "auto_routed"
        else:
            action = "none"

        return Routing(
            project_slug=project,
            project_band=project_band,
            destination=destination,
            destination_band=destination_band,
            importance=importance,
            save_memory=save_memory,
            promote_to_vault=promote,
            updates_project=updates_project,
            creates_task=creates_task,
            sensitive=sensitive,
            needs_review=needs_review,
            review_reasons=sorted(set(review_reasons)),
            action_taken=action,
            confidence=confidence,
        )

    def artifact_project(self, result: ClassificationResult) -> tuple[str | None, Band]:
        """Project assignment for an artifact, gated on belongs_to_project.

        ``project`` is asked speculatively in the same request; when
        ``belongs_to_project`` comes back no, that speculative answer is
        discarded rather than acted on.
        """
        belongs = self.verdict(result.noul("belongs_to_project", 0.0))
        if belongs is not Verdict.YES:
            return None, Band.DEFER
        project, confidence = result.choice("project")
        band = self.band(confidence, action=ACTION_REVERSIBLE)
        if not project or project == UNASSIGNED or band is Band.DEFER:
            return None, band
        return project, band
