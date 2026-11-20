"""Warm-up and sequential latency benchmark, plus latency statistics."""

from __future__ import annotations

import math
from typing import Any, Sequence

import httpx

from backend.request_executor import build_request, effective_request_config, execute_request
from backend.run_context import RunContext


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Percentile with linear interpolation between closest ranks (same as numpy's default)."""
    if not values:
        return None
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    rank = (pct / 100) * (len(data) - 1)
    lower, upper = math.floor(rank), math.ceil(rank)
    if lower == upper:
        return float(data[int(rank)])
    return float(data[lower] + (data[upper] - data[lower]) * (rank - lower))


def latency_stats(latencies: Sequence[float], p99_min_samples: int = 50) -> dict[str, Any]:
    """Statistics over successful-request latencies (ms). p99 is None when there are too few samples."""
    values = [float(v) for v in latencies if v is not None]
    if not values:
        return {"count": 0, "avg_ms": None, "min_ms": None, "max_ms": None,
                "p50_ms": None, "p95_ms": None, "p99_ms": None}
    return {
        "count": len(values),
        "avg_ms": sum(values) / len(values),
        "min_ms": min(values),
        "max_ms": max(values),
        "p50_ms": percentile(values, 50),
        "p95_ms": percentile(values, 95),
        "p99_ms": percentile(values, 99) if len(values) >= p99_min_samples else None,
    }


async def run_warmup(
    ctx: RunContext, team: dict[str, Any], client: httpx.AsyncClient, queries: list[tuple[str, str]]
) -> None:
    """Warm-up requests are recorded with phase='warmup' and never used for scoring."""
    settings = ctx.settings
    cfg = effective_request_config(team, settings)
    count = int(settings["request"]["warmup_requests"])
    for i in range(count):
        ctx.check_cancelled()
        test_id, query = queries[i % len(queries)]
        outcome = await execute_request(client, build_request(team, settings, query), settings, cfg)
        ctx.record(team["team_id"], "warmup", outcome, test_id=test_id, request_index=i)
    ctx.progress(f"{team['team_name']}: warm-up done ({count} requests)")


async def run_benchmark(
    ctx: RunContext, team: dict[str, Any], client: httpx.AsyncClient, queries: list[tuple[str, str]]
) -> dict[str, Any]:
    """Sequential latency benchmark. Queries are cycled in a fixed order so every
    team receives exactly the same payload sequence."""
    settings = ctx.settings
    cfg = effective_request_config(team, settings)
    count = int(settings["request"]["benchmark_requests"])
    latencies: list[float] = []
    failures = 0
    for i in range(count):
        ctx.check_cancelled()
        test_id, query = queries[i % len(queries)]
        outcome = await execute_request(client, build_request(team, settings, query), settings, cfg)
        ctx.record(team["team_id"], "benchmark", outcome, test_id=test_id, request_index=i)
        if outcome.success:
            latencies.append(outcome.latency_ms)
        else:
            failures += 1
    stats = latency_stats(latencies, int(settings["statistics"]["p99_min_samples"]))
    ctx.progress(
        f"{team['team_name']}: benchmark done - {len(latencies)}/{count} ok, "
        f"p50={_fmt(stats['p50_ms'])} p95={_fmt(stats['p95_ms'])}"
    )
    return {**stats, "total": count, "failures": failures}


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}ms"
