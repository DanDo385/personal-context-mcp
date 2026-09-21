"""Classifier: request construction, response handling, thresholds, outage."""

from __future__ import annotations

from typing import Any

import pytest
from typesafe_sdk import Choice, Noul, Score

from personal_context.classifier.base import Answer, ClassificationResult
from personal_context.classifier.jev import JevClassifier, _normalise_answers
from personal_context.classifier.policy import Band, PolicyEngine, Verdict
from personal_context.classifier.questions import (
    IMPORTANCE_LEVELS,
    QUESTIONS_VERSION,
    UNASSIGNED,
    artifact_questions,
    checkpoint_questions,
)
from personal_context.classifier.rules import RulesClassifier, detect_artifact_type
from personal_context.config import Settings

# ------------------------------------------------------- question building


def test_checkpoint_questions_are_batched_into_one_request() -> None:
    questions = checkpoint_questions({"personal-ai": "Personal AI infrastructure"})
    # Everything independent about the same state goes in one call.
    assert len(questions) >= 7
    assert isinstance(questions["save_durable_memory"], Noul)
    assert isinstance(questions["destination"], Choice)
    assert isinstance(questions["importance"], Score)


def test_project_choice_always_offers_an_escape_hatch() -> None:
    questions = checkpoint_questions({"alpha": "Alpha project"})
    assert UNASSIGNED in questions["project"].criteria
    # The model cannot pick an option we did not offer, so the no-match option
    # is not optional.
    assert len(questions["project"].criteria) == 2


def test_project_question_omitted_when_there_are_no_projects() -> None:
    assert "project" not in checkpoint_questions({})


def test_score_levels_stay_within_the_documented_maximum() -> None:
    questions = checkpoint_questions({})
    assert len(questions["importance"].criteria) <= 10


def test_artifact_choice_stays_within_the_documented_maximum() -> None:
    questions = artifact_questions({f"p{i}": f"Project {i}" for i in range(300)})
    # Choice supports up to 255 options; candidates are capped upstream, but
    # the question builder must not be the thing that blows the limit.
    assert len(questions["semantic_category"].criteria) <= 255


def test_questions_version_is_set() -> None:
    assert QUESTIONS_VERSION


# ------------------------------------------------------- response handling


class _FakeAnswer:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeResponse:
    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.model = "jev-1.13.0"
        self.usage = _FakeAnswer(input_tokens=400, output_tokens=12)
        self.request_id = "req_123"


def test_normalise_answers_maps_all_three_primitives() -> None:
    response = _FakeResponse(
        {
            "is_urgent": _FakeAnswer(type="noul", noul=0.97),
            "destination": _FakeAnswer(
                type="choice",
                choice="research",
                confidence=0.88,
                probabilities={"research": 0.9, "inbox": 0.1},
            ),
            "importance": _FakeAnswer(
                type="score",
                score=2.7,
                confidence=0.71,
                probabilities={"2": 0.4, "3": 0.6},
                legend={"0": "a"},
            ),
        }
    )
    answers = _normalise_answers(response)
    assert answers["is_urgent"].kind == "noul"
    assert answers["is_urgent"].probability == pytest.approx(0.97)
    assert answers["destination"].value == "research"
    assert answers["destination"].confidence == pytest.approx(0.88)
    assert answers["importance"].value == pytest.approx(2.7)


def test_unknown_answer_types_are_dropped_not_guessed() -> None:
    response = _FakeResponse({"weird": _FakeAnswer(type="mystery", value=1)})
    assert _normalise_answers(response) == {}


def test_score_normalisation_maps_levels_to_unit_interval() -> None:
    result = ClassificationResult(
        answers={"importance": Answer("score", 4.0, confidence=0.9)},
        classifier="jev",
    )
    value, confidence = result.score("importance", levels=len(IMPORTANCE_LEVELS))
    assert value == pytest.approx(1.0)
    assert confidence == pytest.approx(0.9)


# --------------------------------------------------------------- policy


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgres://unused",
        "auth_mode": "none",
        "classifier": "rules",
        "storage_backend": "local",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.95, Band.AUTO),
        (0.85, Band.AUTO),
        (0.7, Band.REVIEW),
        (0.55, Band.REVIEW),
        (0.3, Band.DEFER),
    ],
)
def test_confidence_bands(confidence: float, expected: Band) -> None:
    assert PolicyEngine(_settings()).band(confidence) == expected


def test_visible_actions_need_more_confidence_than_reversible_ones() -> None:
    engine = PolicyEngine(_settings())
    assert engine.band(0.86, action="reversible") is Band.AUTO
    assert engine.band(0.86, action="visible") is Band.REVIEW


@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.95, Verdict.YES),
        (0.8, Verdict.YES),
        (0.5, Verdict.UNSURE),
        (0.6, Verdict.UNSURE),
        (0.2, Verdict.NO),
        (0.02, Verdict.NO),
    ],
)
def test_noul_verdicts(probability: float, expected: Verdict) -> None:
    assert PolicyEngine(_settings()).verdict(probability) is expected


def test_low_confidence_project_is_dropped_not_guessed() -> None:
    result = ClassificationResult(
        answers={
            "project": Answer("choice", "alpha", confidence=0.31, probabilities={"alpha": 0.31}),
            "destination": Answer(
                "choice", "research", confidence=0.95, probabilities={"research": 0.95}
            ),
        },
        classifier="jev",
    )
    routing = PolicyEngine(_settings()).route(result)
    assert routing.project_slug is None


def test_medium_confidence_project_is_kept_but_flagged() -> None:
    result = ClassificationResult(
        answers={
            "project": Answer("choice", "alpha", confidence=0.7, probabilities={"alpha": 0.7}),
        },
        classifier="jev",
    )
    routing = PolicyEngine(_settings()).route(result)
    assert routing.project_slug == "alpha"
    assert "ambiguous_project" in routing.review_reasons
    assert routing.needs_review


def test_unassigned_choice_becomes_no_project() -> None:
    result = ClassificationResult(
        answers={
            "project": Answer(
                "choice", UNASSIGNED, confidence=0.99, probabilities={UNASSIGNED: 0.99}
            )
        },
        classifier="jev",
    )
    assert PolicyEngine(_settings()).route(result).project_slug is None


def test_sensitive_content_never_auto_promotes_to_the_vault() -> None:
    result = ClassificationResult(
        answers={
            "sensitive": Answer("noul", 0.93),
            "promote_to_obsidian": Answer("noul", 0.97),
            "destination": Answer(
                "choice", "research", confidence=0.99, probabilities={"research": 0.99}
            ),
        },
        classifier="jev",
    )
    routing = PolicyEngine(_settings()).route(result)
    assert routing.promote_to_vault is Verdict.NO
    assert "sensitive_promotion" in routing.review_reasons


def test_historical_import_withholds_vault_promotion() -> None:
    result = ClassificationResult(
        answers={"promote_to_obsidian": Answer("noul", 0.99)}, classifier="jev"
    )
    routing = PolicyEngine(_settings(), historical_import=True).route(result)
    assert routing.promote_to_vault is Verdict.NO
    assert "historical_import_promotion_withheld" in routing.review_reasons


def test_historical_import_uses_a_stricter_auto_threshold() -> None:
    live = PolicyEngine(_settings())
    historical = PolicyEngine(_settings(), historical_import=True)
    assert live.band(0.88) is Band.AUTO
    assert historical.band(0.88) is Band.REVIEW


def test_degraded_result_always_requests_review() -> None:
    result = ClassificationResult(answers={}, classifier="rules", degraded=True)
    routing = PolicyEngine(_settings()).route(result)
    assert routing.action_taken == "fallback"
    assert "classifier_degraded" in routing.review_reasons


def test_speculative_project_answer_ignored_when_not_a_project_artifact() -> None:
    result = ClassificationResult(
        answers={
            "belongs_to_project": Answer("noul", 0.05),
            "project": Answer("choice", "alpha", confidence=0.99, probabilities={"alpha": 0.99}),
        },
        classifier="jev",
    )
    project, band = PolicyEngine(_settings()).artifact_project(result)
    assert project is None
    assert band is Band.DEFER


def test_confident_project_artifact_is_assigned() -> None:
    result = ClassificationResult(
        answers={
            "belongs_to_project": Answer("noul", 0.96),
            "project": Answer("choice", "alpha", confidence=0.97, probabilities={"alpha": 0.97}),
        },
        classifier="jev",
    )
    project, band = PolicyEngine(_settings()).artifact_project(result)
    assert project == "alpha"
    assert band is Band.AUTO


# ------------------------------------------------- deterministic fallback


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["index.html", "styles.css", "app.js"], "web_project"),
        (["package.json", "src/main.ts", "index.html"], "web_project"),
        (["main.py", "util.py", "test_main.py"], "code_project"),
        (["photo.png"], "image"),
        (["clip.mp4"], "video"),
        (["report.pdf"], "document"),
        (["data.csv"], "dataset"),
        (["deck.pptx"], "presentation"),
        (["book.xlsx"], "spreadsheet"),
        (["bundle.zip"], "archive"),
        (["notes.ipynb"], "notebook"),
        ([], "other"),
    ],
)
def test_artifact_type_is_detected_without_a_model(paths: list[str], expected: str) -> None:
    assert detect_artifact_type(paths) == expected


async def test_rules_fallback_is_conservative_about_the_vault() -> None:
    result = await RulesClassifier(degraded=True).classify_checkpoint(
        {"facts": ["a"], "decisions": ["b"]}, {"alpha": "Alpha"}
    )
    routing = PolicyEngine(_settings()).route(result)
    # It will save memory, because the caller explicitly supplied facts...
    assert routing.save_memory is Verdict.YES
    # ...but it will not write to the user's filesystem on a guess.
    assert routing.promote_to_vault is Verdict.NO
    assert routing.project_slug is None


async def test_rules_fallback_trusts_an_explicit_project() -> None:
    result = await RulesClassifier().classify_checkpoint(
        {"stated_project": "alpha"}, {"alpha": "Alpha"}
    )
    routing = PolicyEngine(_settings()).route(result)
    assert routing.project_slug == "alpha"


# --------------------------------------------------------- Jev integration


async def test_jev_falls_back_when_the_api_fails(monkeypatch) -> None:
    from typesafe_sdk import TypeSafeAPIConnectionError

    classifier = JevClassifier(_settings(classifier="jev", typesafe_api_key="k"))

    async def boom(**_: Any) -> Any:
        raise TypeSafeAPIConnectionError("network down")

    monkeypatch.setattr(classifier._client, "system_one", boom)
    result = await classifier.classify_checkpoint({"summary": "x"}, {"alpha": "Alpha"})

    assert result.degraded is True
    assert result.classifier == "rules"
    assert result.error == "TypeSafeAPIConnectionError"
    assert result.input_hash  # the state we would have sent is still recorded
    await classifier.aclose()


async def test_jev_falls_back_on_an_empty_answer_set(monkeypatch) -> None:
    classifier = JevClassifier(_settings(classifier="jev", typesafe_api_key="k"))

    async def empty(**_: Any) -> Any:
        return _FakeResponse({})

    monkeypatch.setattr(classifier._client, "system_one", empty)
    result = await classifier.classify_checkpoint({"summary": "x"}, {})
    assert result.degraded is True
    assert result.error == "empty_answers"
    await classifier.aclose()


async def test_jev_records_model_and_request_id(monkeypatch) -> None:
    classifier = JevClassifier(_settings(classifier="jev", typesafe_api_key="k"))
    captured: dict[str, Any] = {}

    async def ok(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _FakeResponse({"save_durable_memory": _FakeAnswer(type="noul", noul=0.9)})

    monkeypatch.setattr(classifier._client, "system_one", ok)
    result = await classifier.classify_checkpoint({"summary": "hello"}, {"alpha": "Alpha"})

    assert result.model == "jev-1.13.0"
    assert result.request_id == "req_123"
    assert result.input_tokens == 400
    assert result.degraded is False
    # Every question went in one request.
    assert len(captured["questions"]) >= 7
    await classifier.aclose()


async def test_sensitive_state_never_reaches_the_api(monkeypatch) -> None:
    classifier = JevClassifier(
        _settings(
            classifier="jev",
            typesafe_api_key="k",
            no_external_sensitivity=["highly_sensitive"],
        )
    )
    called = False

    async def should_not_run(**_: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("highly sensitive state was sent to the classifier")

    monkeypatch.setattr(classifier._client, "system_one", should_not_run)
    result = await classifier.classify_checkpoint(
        {"summary": "medical records", "sensitivity": "highly_sensitive"}, {}
    )

    assert called is False
    assert result.error == "blocked_by_privacy_policy"
    # Blocked by policy is not the same as the classifier being broken.
    assert result.degraded is False
    await classifier.aclose()


async def test_state_is_redacted_and_bounded(monkeypatch) -> None:
    classifier = JevClassifier(
        _settings(classifier="jev", typesafe_api_key="k", jev_max_state_chars=500)
    )
    captured: dict[str, Any] = {}

    async def capture(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _FakeResponse({"save_durable_memory": _FakeAnswer(type="noul", noul=0.5)})

    monkeypatch.setattr(classifier._client, "system_one", capture)
    await classifier.classify_checkpoint(
        {
            "summary": "contact me at secret@example.com " + ("x" * 5000),
            "title": "T",
            "sensitivity": "normal",
        },
        {},
    )

    sent = captured["state"]["summary"]
    assert "secret@example.com" not in sent
    assert "[email]" in sent
    assert len(sent) < 5000
    await classifier.aclose()
