"""Retrieval eval: tests/eval/retrieval_cases.yaml against the synthetic fixture.

Prints recall@5, MRR, stale-decision errors, duplicates, tokens, round trips and
latency. Set PC_EVAL_REPORT to a file path to also write the full report as
JSON, and PC_EVAL_LATENCY_SAMPLES to time each case more than once.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from conftest import requires_db
from eval.fixture import seed
from eval.harness import QueryCounter, run

pytestmark = requires_db

#: Floors. Raise them when retrieval improves; never lower them to pass.
#: Baseline at 2ca2095 was recall@5 0.522, MRR 0.44, 4 stale-decision errors.
MIN_RECALL_AT_5 = 0.95
MIN_MRR = 0.85
MAX_STALE_DECISION_ERRORS = 0
MAX_DUPLICATES = 0


@pytest.fixture
def unthrottled(settings):
    """The eval makes hundreds of calls; the per-client rate limit is not under test."""
    settings.rate_limit_per_minute = 1_000_000
    settings.rate_limit_burst = 1_000_000
    return settings


async def test_retrieval_eval(
    unthrottled, db, live_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await seed(db)
    counter = QueryCounter(monkeypatch)
    samples = int(os.environ.get("PC_EVAL_LATENCY_SAMPLES", "1"))
    async with Client(
        StreamableHttpTransport(
            url=f"{live_server.base_url}{live_server.mcp_path}", auth="test-token"
        )
    ) as client:
        report = await run(client, seeded, counter, latency_samples=samples)

    print("\n" + json.dumps({k: v for k, v in report.items() if k != "returned"}, indent=2))
    if target := os.environ.get("PC_EVAL_REPORT"):
        Path(target).write_text(json.dumps(report, indent=2))

    assert not report["failures"], report["failures"]
    assert report["recall_at_5"] >= MIN_RECALL_AT_5
    assert report["mrr"] >= MIN_MRR
    assert report["stale_decision_errors"] <= MAX_STALE_DECISION_ERRORS
    assert report["duplicates"] <= MAX_DUPLICATES
