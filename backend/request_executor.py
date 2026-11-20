"""HTTP request construction, execution, timing and outcome classification.

This module knows nothing about scoring. It sends one request, measures the
end-to-end HTTP round-trip time with ``time.perf_counter()`` (monotonic), and
classifies the outcome as one of:

    success           2xx, valid JSON, and (if required) a results list
    http_error        non-2xx status code (incl. unfollowed redirects)
    connection_error  could not connect / connection dropped
    timeout           no complete response within the configured timeout
    invalid_json      2xx but the body is not valid JSON
    invalid_response  valid JSON but not the expected response format
    too_large         body exceeded request.max_response_bytes
    blocked           endpoint URL failed the security allowlist check
    error             any other unexpected client-side failure

Measured latency is the time from just before the request is sent until the
complete response body has been received. JSON parsing is not included. It is
end-to-end round-trip time from the evaluator machine, not backend time.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.security import validate_endpoint_url

OUTCOMES = (
    "success",
    "http_error",
    "connection_error",
    "timeout",
    "invalid_json",
    "invalid_response",
    "too_large",
    "blocked",
    "error",
)
RETRYABLE = {"connection_error", "timeout"}


@dataclass
class RequestSpec:
    method: str
    url: str
    json_body: Any = None
    params: dict[str, Any] | None = None
    content: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class RequestOutcome:
    outcome: str
    success: bool
    latency_ms: float | None
    status_code: int | None
    error_message: str | None
    started_at: str
    attempts: int = 1
    body_text: str | None = None
    payload: Any = None
    results: list | None = None

    def to_record(self, max_payload_chars: int) -> dict[str, Any]:
        text = self.body_text
        if text is not None and len(text) > max_payload_chars:
            text = text[:max_payload_chars] + f"... [truncated, {len(self.body_text)} chars]"
        return {
            "outcome": self.outcome,
            "success": int(self.success),
            "latency_ms": self.latency_ms,
            "status_code": self.status_code,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "attempts": self.attempts,
            "response_payload": text,
        }


def effective_request_config(team: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    config = dict(settings["request_template"])
    config.update(team.get("request_config") or {})
    return config


def build_request(
    team: dict[str, Any],
    settings: dict[str, Any],
    query: str | None,
    *,
    payload_override: dict[str, Any] | None = None,
    raw_body: str | None = None,
    content_type: str = "application/json",
    path_suffix: str | None = None,
    include_limit: bool = True,
) -> RequestSpec:
    """Construct the standard request for a team.

    POST sends a JSON body ``{query_field: query, limit_field: limit, **extra_body}``.
    GET sends the same fields as query-string parameters.
    """
    cfg = effective_request_config(team, settings)
    method = (team.get("http_method") or settings["request"]["http_method"]).upper()
    url = team["endpoint_url"].strip()
    if path_suffix:
        url = url.rstrip("/") + path_suffix
    headers = {str(k): str(v) for k, v in (cfg.get("headers") or {}).items()}

    if payload_override is not None:
        body = dict(payload_override)
    else:
        body = dict(cfg.get("extra_body") or {})
        body[cfg["query_field"]] = query
        if include_limit:
            body[cfg["limit_field"]] = int(settings["request"]["result_limit"])

    if raw_body is not None:
        headers.setdefault("Content-Type", content_type)
        return RequestSpec("POST" if method == "POST" else method, url, content=raw_body.encode("utf-8"), headers=headers)
    if method == "GET":
        params = {k: v for k, v in body.items() if isinstance(v, (str, int, float, bool))}
        return RequestSpec("GET", url, params=params, headers=headers)
    return RequestSpec("POST", url, json_body=body, headers=headers)


def extract_results(payload: Any, results_path: str) -> list | None:
    """Follow a dotted path (e.g. ``results`` or ``data.items``); '' means the payload itself."""
    node = payload
    for part in [p for p in (results_path or "").split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, list) else None


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def validate_result_items(results: list, id_field: str, name_field: str) -> str | None:
    """Return an error message if result items do not have the expected shape."""
    for idx, item in enumerate(results):
        if not isinstance(item, dict):
            return f"Result item {idx} is not a JSON object."
        if item.get(id_field) in (None, "") and item.get(name_field) in (None, ""):
            return f"Result item {idx} has neither '{id_field}' nor '{name_field}'."
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def _send_once(
    client: httpx.AsyncClient,
    spec: RequestSpec,
    timeout_s: float,
    max_bytes: int,
) -> tuple[str, float | None, int | None, bytes | None, str | None]:
    """Return (outcome, latency_ms, status_code, body, error_message)."""
    connected = {"value": False}

    async def trace(event: str, info: dict) -> None:
        # Any HTTP-level event means a TCP connection exists (new or reused from the pool).
        if event == "connection.connect_tcp.complete" or event.startswith(("http11.", "http2.")):
            connected["value"] = True

    request = client.build_request(
        spec.method, spec.url, json=spec.json_body, params=spec.params, content=spec.content, headers=spec.headers,
        extensions={"trace": trace},
    )
    start = time.perf_counter()

    async def _do() -> tuple[int, bytes, bool]:
        response = await client.send(request, stream=True)
        try:
            chunks, size, too_large = [], 0, False
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    too_large = True
                    break
                chunks.append(chunk)
            return response.status_code, b"".join(chunks), too_large
        finally:
            await response.aclose()

    try:
        # wait_for enforces a total deadline; httpx timeouts are per network operation.
        status, body, too_large = await asyncio.wait_for(_do(), timeout=timeout_s)
        end = time.perf_counter()
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        elapsed = (time.perf_counter() - start) * 1000
        if isinstance(exc, httpx.ConnectTimeout) or not connected["value"]:
            return "connection_error", elapsed, None, None, f"Could not connect within {timeout_s:g}s."
        return "timeout", elapsed, None, None, f"No complete response within {timeout_s:g}s."
    except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError,
            httpx.NetworkError) as exc:
        return "connection_error", (time.perf_counter() - start) * 1000, None, None, f"{type(exc).__name__}: {exc}"
    except httpx.HTTPError as exc:
        return "error", (time.perf_counter() - start) * 1000, None, None, f"{type(exc).__name__}: {exc}"

    latency_ms = (end - start) * 1000
    if too_large:
        return "too_large", latency_ms, status, None, f"Response body exceeded {max_bytes} bytes."
    return "ok", latency_ms, status, body, None


async def execute_request(
    client: httpx.AsyncClient,
    spec: RequestSpec,
    settings: dict[str, Any],
    request_cfg: dict[str, Any],
    *,
    expect_results: bool = True,
    retries: int | None = None,
) -> RequestOutcome:
    """Send ``spec`` and classify the result. Retries (default 0) only apply to
    connection errors and timeouts; the latency of the final attempt is recorded."""
    req_settings = settings["request"]
    timeout_s = float(req_settings["timeout_seconds"])
    max_bytes = int(req_settings["max_response_bytes"])
    retries = int(req_settings["retry_attempts"] if retries is None else retries)

    attempts = 0
    while True:
        attempts += 1
        started_at = _utc_now()
        outcome, latency_ms, status, body, error = await _send_once(client, spec, timeout_s, max_bytes)
        if outcome in RETRYABLE and attempts <= retries:
            continue
        break

    if outcome != "ok":
        return RequestOutcome(outcome, False, latency_ms, status, error, started_at, attempts)

    text = body.decode("utf-8", errors="replace") if body is not None else ""
    if not 200 <= status < 300:
        reason = "Redirect not followed" if 300 <= status < 400 else f"HTTP {status}"
        return RequestOutcome("http_error", False, latency_ms, status, reason, started_at, attempts, body_text=text)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return RequestOutcome("invalid_json", False, latency_ms, status, f"Invalid JSON: {exc}", started_at,
                              attempts, body_text=text)

    if not expect_results:
        return RequestOutcome("success", True, latency_ms, status, None, started_at, attempts,
                              body_text=text, payload=payload)

    results = extract_results(payload, request_cfg.get("results_path", "results"))
    if results is None:
        return RequestOutcome(
            "invalid_response", False, latency_ms, status,
            f"Response has no list at '{request_cfg.get('results_path', 'results')}'.",
            started_at, attempts, body_text=text, payload=payload,
        )
    problem = validate_result_items(results, request_cfg["id_field"], request_cfg["name_field"])
    if problem:
        return RequestOutcome("invalid_response", False, latency_ms, status, problem, started_at, attempts,
                              body_text=text, payload=payload, results=results)
    return RequestOutcome("success", True, latency_ms, status, None, started_at, attempts,
                          body_text=text, payload=payload, results=results)


def blocked_outcome(reason: str) -> RequestOutcome:
    return RequestOutcome("blocked", False, None, None, reason, _utc_now(), 0)


def check_team_url(team: dict[str, Any], settings: dict[str, Any]) -> str | None:
    """Return None if the team endpoint is allowed, else the reason it is blocked."""
    check = validate_endpoint_url(team["endpoint_url"], settings["security"])
    return None if check.ok else check.reason


def make_client(settings: dict[str, Any], max_connections: int = 1) -> httpx.AsyncClient:
    timeout_s = float(settings["request"]["timeout_seconds"])
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=bool(settings["security"].get("follow_redirects", False)),
        limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
        trust_env=False,  # ignore proxy env vars: requests go directly to approved local hosts
        headers={"User-Agent": "competition-evaluator/1.0"},
    )


async def connectivity_check(team: dict[str, Any], settings: dict[str, Any], query: str) -> RequestOutcome:
    reason = check_team_url(team, settings)
    if reason:
        return blocked_outcome(reason)
    cfg = effective_request_config(team, settings)
    async with make_client(settings) as client:
        return await execute_request(client, build_request(team, settings, query), settings, cfg)


def run_connectivity_check(team: dict[str, Any], settings: dict[str, Any], query: str) -> RequestOutcome:
    """Synchronous wrapper for use from the dashboard."""
    return asyncio.run(connectivity_check(team, settings, query))
