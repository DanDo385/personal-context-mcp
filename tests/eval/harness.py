"""Run retrieval_cases.yaml through a real MCP client and score the answers.

The adapter functions at the top are the only place that knows tool names and
result shapes. Everything below them works on a plain list of returned items
(dicts with at least an ``id``) plus the package-level fields.

Metrics, per run:

* recall@5     share of ``include`` keys found (search: top 5; bootstrap: package)
* MRR          mean reciprocal rank of the first expected key
* stale        a superseded or overruled decision returned as if it were live
* duplicates   the same id, or the same text, twice in one answer
* tokens       size of the returned JSON, at the server's 3.6 chars per token
* round trips  database statements per tool call
* latency      wall-clock per tool call, p50 and p95
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .fixture import Seeded

CASES_PATH = Path(__file__).with_name("retrieval_cases.yaml")


def load_cases() -> list[dict[str, Any]]:
    return list(yaml.safe_load(CASES_PATH.read_text())["cases"])


# --------------------------------------------------------------- adapters


@dataclass
class Answer:
    items: list[dict[str, Any]]
    package: dict[str, Any]
    raw_size: int


async def _schema_properties(client: Any, tool: str) -> set[str]:
    for t in await client.list_tools():
        if t.name == tool:
            return set((t.inputSchema or {}).get("properties", {}))
    raise KeyError(tool)


async def call(client: Any, case: dict[str, Any], seeded: Seeded) -> Answer:
    args = {k: _substitute(v, seeded) for k, v in case["args"].items()}
    if case["tool"] == "search":
        result = await client.call_tool("context_search", args)
        data = result.structured_content or {}
        items = data.get("result", data) if isinstance(data, dict) else data
        items = list(items or [])
        return Answer(items=items, package={}, raw_size=len(json.dumps(data, default=str)))

    props = await _schema_properties(client, "context_bootstrap")
    if "query" in args and "query" not in props:
        # Older servers only take the whole message.
        args["user_message"] = args.pop("query")
    result = await client.call_tool("context_bootstrap", args)
    package = result.structured_content or {}
    return Answer(
        items=list(package.get("items") or []),
        package=package,
        raw_size=len(json.dumps(package, default=str)),
    )


def _substitute(value: Any, seeded: Seeded) -> Any:
    if isinstance(value, str) and value.startswith("$id:"):
        return str(seeded.ids[value[4:]])
    return value


# ------------------------------------------------------------- inspection


def _keys(items: list[dict[str, Any]], seeded: Seeded) -> list[str | None]:
    return [seeded.key_for(i["id"]) if i.get("id") else None for i in items]


def _is_marked_superseded(item: dict[str, Any]) -> bool:
    return item.get("status") == "superseded" or bool(item.get("superseded_by"))


def _is_marked_conflict(item: dict[str, Any], package: dict[str, Any]) -> bool:
    if item.get("conflicts_with"):
        return True
    for conflict in package.get("conflicts") or []:
        if str(item.get("id")) in {str(x) for x in conflict.get("ids", [])}:
            return True
    return False


def _package_project(package: dict[str, Any]) -> str | None:
    project = package.get("project")
    if isinstance(project, dict):
        return project.get("slug")
    return project


def _candidates(package: dict[str, Any]) -> list[str]:
    project = package.get("project")
    if isinstance(project, dict):
        return [c.get("slug") for c in project.get("candidates") or []]
    return []


@dataclass
class CaseResult:
    id: str
    category: str
    tool: str
    passed: bool
    failures: list[str]
    recall: float | None
    reciprocal_rank: float | None
    stale_errors: int
    duplicates: int
    tokens: int
    round_trips: int
    latency_ms: float
    returned: list[str | None] = field(default_factory=list)


def score(
    case: dict[str, Any],
    answer: Answer,
    seeded: Seeded,
    *,
    round_trips: int,
    latency_ms: float,
) -> CaseResult:
    expect = case.get("expect") or {}
    keys = _keys(answer.items, seeded)
    by_key = {k: item for k, item in zip(keys, answer.items, strict=True) if k}
    window = keys[:5] if case["tool"] == "search" else keys
    failures: list[str] = []

    include = list(expect.get("include") or [])
    found = [k for k in include if k in window]
    recall = len(found) / len(include) if include else None
    for k in include:
        if k not in window:
            failures.append(f"missing {k}")

    any_of = list(expect.get("include_any") or [])
    if any_of and not any(k in window for k in any_of):
        failures.append(f"none of {any_of}")
        recall = 0.0 if recall is None else recall
    elif any_of:
        recall = 1.0 if recall is None else recall

    wanted = include or any_of or ([expect["first"]] if expect.get("first") else [])
    reciprocal_rank = None
    if wanted:
        ranks = [keys.index(k) + 1 for k in wanted if k in keys]
        reciprocal_rank = 1 / min(ranks) if ranks else 0.0

    if expect.get("first"):
        first = keys[0] if keys else None
        if first != expect["first"]:
            failures.append(f"first was {first}, wanted {expect['first']}")
        if recall is None:
            recall = 1.0 if expect["first"] in window else 0.0

    superseded = set(expect.get("superseded") or [])
    stale_errors = 0
    for k in superseded:
        if (
            k in by_key
            and not _is_marked_superseded(by_key[k])
            and not _is_marked_conflict(by_key[k], answer.package)
        ):
            stale_errors += 1
            failures.append(f"{k} returned as live")
    for k in expect.get("exclude") or []:
        if k in by_key and not (k in superseded and _is_marked_superseded(by_key[k])):
            failures.append(f"unexpected {k}")
    for k in expect.get("conflict") or []:
        if k not in by_key:
            failures.append(f"conflict item {k} missing")
            stale_errors += 1
        elif not _is_marked_conflict(by_key[k], answer.package) and not _is_marked_superseded(
            by_key[k]
        ):
            failures.append(f"{k} not flagged as conflicting")
            stale_errors += 1

    order = list(expect.get("order") or [])
    if order:
        positions = [keys.index(k) if k in keys else None for k in order]
        if None in positions or positions != sorted(positions):  # type: ignore[type-var]
            failures.append(f"order {order} not kept: {positions}")

    if expect.get("empty") and answer.items:
        failures.append(f"expected nothing, got {len(answer.items)}")
    if expect.get("empty") is True and recall is None:
        recall = 1.0 if not answer.items else 0.0

    if expect.get("project") and _package_project(answer.package) != expect["project"]:
        failures.append(f"project {_package_project(answer.package)!r}, wanted {expect['project']}")
    if expect.get("candidates"):
        got = set(_candidates(answer.package))
        if not set(expect["candidates"]) <= got:
            failures.append(f"candidates {sorted(got)}, wanted {expect['candidates']}")

    for k, kind in (expect.get("provenance") or {}).items():
        item = by_key.get(k)
        got_kind = ((item or {}).get("provenance") or {}).get("kind")
        if got_kind != kind:
            failures.append(f"{k} provenance {got_kind}, wanted {kind}")

    if expect.get("max_tokens") is not None:
        estimated = answer.package.get("estimated_tokens", 0)
        if estimated > expect["max_tokens"]:
            failures.append(f"estimated_tokens {estimated} > {expect['max_tokens']}")

    if expect.get("note"):
        notes = " ".join(answer.package.get("notes") or [])
        if expect["note"] not in notes:
            failures.append(f"note {expect['note']!r} missing")

    ids = [str(i.get("id")) for i in answer.items]
    texts = [" ".join(str(i.get("content", "")).lower().split()) for i in answer.items]
    duplicates = (len(ids) - len(set(ids))) + (len(texts) - len(set(texts)))

    return CaseResult(
        id=case["id"],
        category=case.get("category", ""),
        tool=case["tool"],
        passed=not failures,
        failures=failures,
        recall=recall,
        reciprocal_rank=reciprocal_rank,
        stale_errors=stale_errors,
        duplicates=duplicates,
        tokens=int(answer.raw_size / 3.6) + 1,
        round_trips=round_trips,
        latency_ms=latency_ms,
        returned=keys,
    )


class QueryCounter:
    """Counts database statements by wrapping the Database class methods."""

    METHODS = ("fetch", "fetchrow", "fetchval", "execute")

    def __init__(self, monkeypatch: Any) -> None:
        from personal_context_mcp.db.pool import Database

        self.count = 0
        for name in self.METHODS:
            original = getattr(Database, name)

            async def wrapped(self_db: Any, *args: Any, _o: Any = original, **kw: Any) -> Any:
                self.count += 1
                return await _o(self_db, *args, **kw)

            monkeypatch.setattr(Database, name, wrapped)


async def run(
    client: Any,
    seeded: Seeded,
    counter: QueryCounter,
    *,
    latency_samples: int = 1,
) -> dict[str, Any]:
    results: list[CaseResult] = []
    latencies: dict[str, list[float]] = {"search": [], "bootstrap": []}
    for case in load_cases():
        timings: list[float] = []
        answer = None
        trips = 0
        for _ in range(max(1, latency_samples)):
            before = counter.count
            started = time.perf_counter()
            answer = await call(client, case, seeded)
            timings.append((time.perf_counter() - started) * 1000)
            trips = counter.count - before
        assert answer is not None
        latencies[case["tool"]] += timings
        results.append(
            score(
                case,
                answer,
                seeded,
                round_trips=trips,
                latency_ms=statistics.median(timings),
            )
        )
    return summarize(results, latencies, latency_samples)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return round(ordered[index], 1)


def summarize(
    results: list[CaseResult], latencies: dict[str, list[float]], samples: int
) -> dict[str, Any]:
    def mean(values: list[float]) -> float:
        return round(sum(values) / len(values), 3) if values else 0.0

    recalls = [r.recall for r in results if r.recall is not None]
    rrs = [r.reciprocal_rank for r in results if r.reciprocal_rank is not None]
    by_tool: dict[str, Any] = {}
    for tool in ("search", "bootstrap"):
        rows = [r for r in results if r.tool == tool]
        by_tool[tool] = {
            "cases": len(rows),
            "passed": sum(r.passed for r in rows),
            "recall_at_5": mean([r.recall for r in rows if r.recall is not None]),
            "mrr": mean([r.reciprocal_rank for r in rows if r.reciprocal_rank is not None]),
            "mean_tokens": mean([float(r.tokens) for r in rows]),
            "mean_round_trips": mean([float(r.round_trips) for r in rows]),
            "latency_ms_p50": _pct(latencies[tool], 0.5),
            "latency_ms_p95": _pct(latencies[tool], 0.95),
            "latency_samples": len(latencies[tool]),
        }
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "recall_at_5": mean(recalls),  # type: ignore[arg-type]
        "mrr": mean(rrs),  # type: ignore[arg-type]
        "stale_decision_errors": sum(r.stale_errors for r in results),
        "duplicates": sum(r.duplicates for r in results),
        "samples_per_case": samples,
        "by_tool": by_tool,
        "failures": {r.id: r.failures for r in results if r.failures},
        "returned": {r.id: r.returned for r in results},
    }
