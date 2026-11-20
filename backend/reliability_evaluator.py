"""Reliability tests: how a participant handles invalid input and edge cases.

The suite is defined in ``config/reliability_tests.json``. Every test has a
predefined list of acceptable status codes. A test passes when the response
status is acceptable and, for 2xx responses, the body is valid JSON (and, for
``format_check``/``duplicate``, the results list has the expected shape).
Timeouts, connection failures and 5xx responses always fail.

For rows in phase ``reliability``, the ``success`` column means *test passed*;
``outcome`` holds the HTTP-level classification of the request.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.request_executor import (
    RequestOutcome,
    build_request,
    effective_request_config,
    execute_request,
)
from backend.run_context import RunContext

LONG_QUERY_TOKEN = "__long_query_10000__"
FIRST_CASE_TOKEN = "__first_test_case__"


def _resolve_query(value: Any, default_query: str) -> Any:
    if value == FIRST_CASE_TOKEN:
        return default_query
    if value == LONG_QUERY_TOKEN:
        return ("very long query " * 700)[:10_000]
    return value


def _translate_payload(payload: dict[str, Any], cfg: dict[str, Any], default_query: str) -> dict[str, Any]:
    """Map the generic 'query'/'limit' keys onto the team's configured field names."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        target = {"query": cfg["query_field"], "limit": cfg["limit_field"]}.get(key, key)
        out[target] = _resolve_query(value, default_query)
    return out


def judge(outcome: RequestOutcome, accepted: list[int], require_results: bool) -> tuple[bool, str]:
    if outcome.outcome in ("timeout", "connection_error", "blocked", "too_large", "error"):
        return False, f"{outcome.outcome}: {outcome.error_message}"
    status = outcome.status_code
    if status not in accepted:
        return False, f"HTTP {status} not in accepted {accepted}"
    if 200 <= status < 300:
        if require_results and outcome.outcome != "success":
            return False, f"{outcome.outcome}: {outcome.error_message}"
        if outcome.outcome == "invalid_json":
            return False, "2xx response with invalid JSON"
    return True, f"HTTP {status} accepted"


def _ids(outcome: RequestOutcome, cfg: dict[str, Any]) -> list[str]:
    return [str(item.get(cfg["id_field"], item.get(cfg["name_field"]))) for item in (outcome.results or [])]


async def run_reliability(
    ctx: RunContext,
    team: dict[str, Any],
    client: httpx.AsyncClient,
    tests: list[dict[str, Any]],
    default_query: str,
) -> dict[str, Any]:
    settings = ctx.settings
    cfg = effective_request_config(team, settings)
    is_get = (team.get("http_method") or "POST").upper() == "GET"
    passed = total = skipped = 0

    for idx, test in enumerate(tests):
        ctx.check_cancelled()
        accepted = [int(s) for s in test["accepted_status"]]
        kind = test["type"]
        details: dict[str, Any] = {"name": test.get("name"), "type": kind, "accepted_status": accepted}

        if kind == "raw_body" and is_get:
            skipped += 1
            ctx.record(team["team_id"], "reliability", None, test_id=test["id"], request_index=idx,
                       success=False, details={**details, "skipped": True,
                                               "reason": "Raw-body test does not apply to GET endpoints"})
            continue

        if kind == "duplicate":
            query = _resolve_query(test.get("query", FIRST_CASE_TOKEN), default_query)
            outcomes = [
                await execute_request(client, build_request(team, settings, query), settings, cfg, expect_results=True)
                for _ in range(max(2, int(test.get("repeat", 3))))
            ]
            ok, reason = True, "Consistent results across repeats"
            for o in outcomes:
                good, why = judge(o, accepted, require_results=True)
                if not good:
                    ok, reason = False, why
                    break
            if ok and len({tuple(_ids(o, cfg)) for o in outcomes}) != 1:
                ok, reason = False, "Result IDs differ between identical requests"
            outcome = outcomes[-1]
            details["repeats"] = len(outcomes)
            details["latencies_ms"] = [o.latency_ms for o in outcomes]
        else:
            if kind == "format_check":
                query = _resolve_query(test.get("query", FIRST_CASE_TOKEN), default_query)
                spec = build_request(team, settings, query)
                require_results = True
            elif kind == "payload":
                payload = _translate_payload(test.get("payload", {}), cfg, default_query)
                spec = build_request(team, settings, None, payload_override=payload)
                require_results = False
            elif kind == "raw_body":
                spec = build_request(team, settings, None, raw_body=test.get("raw_body", "{"),
                                     content_type=test.get("content_type", "application/json"))
                require_results = False
            elif kind == "path_suffix":
                spec = build_request(team, settings, default_query, path_suffix=test.get("path_suffix", "/__missing__"))
                require_results = False
            else:
                ctx.error(f"{team['team_name']}: unknown reliability test type '{kind}' ({test['id']})")
                continue
            outcome = await execute_request(client, spec, settings, cfg, expect_results=require_results)
            ok, reason = judge(outcome, accepted, require_results)

        total += 1
        passed += int(ok)
        details["reason"] = reason
        ctx.record(team["team_id"], "reliability", outcome, test_id=test["id"], request_index=idx,
                   success=ok, details=details)

    pct = (passed / total * 100) if total else None
    ctx.progress(f"{team['team_name']}: reliability {passed}/{total} passed" + (f", {skipped} skipped" if skipped else ""))
    return {"passed": passed, "total": total, "skipped": skipped, "pass_percentage": pct}
