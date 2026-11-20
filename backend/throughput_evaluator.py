"""Sequential throughput: successful requests / total wall-clock seconds."""

from __future__ import annotations

import time
from typing import Any

import httpx

from backend.request_executor import build_request, effective_request_config, execute_request
from backend.run_context import RunContext


def compute_throughput(successful: int, duration_s: float) -> float:
    return successful / duration_s if duration_s > 0 else 0.0


async def run_throughput(
    ctx: RunContext, team: dict[str, Any], client: httpx.AsyncClient, queries: list[tuple[str, str]]
) -> dict[str, Any]:
    settings = ctx.settings
    cfg = effective_request_config(team, settings)
    total = int(settings["throughput"]["requests"])
    successful = 0
    # Requests are built up front so construction time is not part of the measurement.
    specs = [(queries[i % len(queries)], build_request(team, settings, queries[i % len(queries)][1]))
             for i in range(total)]

    start = time.perf_counter()
    for i, ((test_id, _), spec) in enumerate(specs):
        ctx.check_cancelled()
        outcome = await execute_request(client, spec, settings, cfg)
        ctx.record(team["team_id"], "throughput", outcome, test_id=test_id, request_index=i)
        successful += int(outcome.success)
    duration_s = time.perf_counter() - start

    summary = {
        "total_requests": total,
        "successful_requests": successful,
        "failed_requests": total - successful,
        "duration_s": duration_s,
        "throughput_rps": compute_throughput(successful, duration_s),
    }
    ctx.record(team["team_id"], "throughput_summary", None, details=summary)
    ctx.progress(f"{team['team_name']}: throughput {summary['throughput_rps']:.2f} req/s ({successful}/{total} ok)")
    return summary
