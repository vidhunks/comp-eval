"""Concurrency testing with asyncio + httpx.AsyncClient.

For each level L, ``L * rounds_per_level`` requests are issued with at most L
in flight at a time (bounded by a semaphore and the client connection pool).
Levels above ``concurrency.max_concurrency`` are refused so the evaluator never
runs unbounded concurrency on the laptop.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from backend.latency_evaluator import latency_stats
from backend.request_executor import build_request, effective_request_config, execute_request, make_client
from backend.run_context import RunContext


def concurrency_levels(settings: dict[str, Any]) -> list[int]:
    cap = int(settings["concurrency"]["max_concurrency"])
    levels = sorted({int(level) for level in settings["concurrency"]["levels"]})
    too_high = [level for level in levels if level > cap]
    if too_high:
        raise ValueError(f"Concurrency levels {too_high} exceed max_concurrency={cap}.")
    return levels


async def run_concurrency(
    ctx: RunContext, team: dict[str, Any], queries: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    settings = ctx.settings
    cfg = effective_request_config(team, settings)
    rounds = max(1, int(settings["concurrency"]["rounds_per_level"]))
    p99_min = int(settings["statistics"]["p99_min_samples"])
    summaries: list[dict[str, Any]] = []

    for level in concurrency_levels(settings):
        ctx.check_cancelled()
        total = level * rounds
        specs = [(queries[i % len(queries)][0], build_request(team, settings, queries[i % len(queries)][1]))
                 for i in range(total)]
        semaphore = asyncio.Semaphore(level)

        async with make_client(settings, max_connections=level) as client:
            async def one(index: int, test_id: str, spec):
                async with semaphore:
                    return index, test_id, await execute_request(client, spec, settings, cfg)

            start = time.perf_counter()
            results = await asyncio.gather(*(one(i, tid, spec) for i, (tid, spec) in enumerate(specs)))
            wall_s = time.perf_counter() - start

        latencies = []
        for index, test_id, outcome in results:
            ctx.record(team["team_id"], "concurrency", outcome, test_id=test_id,
                       request_index=index, concurrency_level=level, track=False)
            if outcome.success:
                latencies.append(outcome.latency_ms)

        stats = latency_stats(latencies, p99_min)
        successful = len(latencies)
        summary = {
            "level": level,
            "total_requests": total,
            "successful_requests": successful,
            "failed_requests": total - successful,
            "error_rate": (total - successful) / total,
            "wall_time_s": wall_s,
            "throughput_rps": successful / wall_s if wall_s > 0 else 0.0,
            **stats,
        }
        ctx.record(team["team_id"], "concurrency_summary", None, concurrency_level=level, details=summary)
        summaries.append(summary)
        p95 = "n/a" if stats["p95_ms"] is None else f"{stats['p95_ms']:.1f}ms"
        ctx.progress(f"{team['team_name']}: concurrency {level} - {successful}/{total} ok, p95={p95}")
    return summaries
