"""Evaluation run orchestration.

* Only one run may execute at a time (process lock + database check).
* A run executes in a background thread so the dashboard stays responsive.
* Teams are evaluated one after another (never in parallel) so that one
  team's traffic cannot distort another team's latency measurements.
* A failure for one team is logged and the run continues with the next team.
* Every run stores a snapshot of the settings, teams and test cases it used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import traceback
from datetime import datetime, timezone
from typing import Any

from backend import results_service, team_manager, test_manager
from backend.concurrency_evaluator import concurrency_levels, run_concurrency
from backend.config import load_reliability_tests, load_settings, validate_settings
from backend.database import get_connection
from backend.latency_evaluator import run_benchmark, run_warmup
from backend.quality_evaluator import run_functional, scorable_test_cases
from backend.reliability_evaluator import run_reliability
from backend.request_executor import (
    build_request,
    check_team_url,
    effective_request_config,
    execute_request,
    make_client,
)
from backend.run_context import TRANSPORT_FAILURES, RunCancelled, RunContext, TeamUnreachable
from backend.security import mask_request_config
from backend.throughput_evaluator import run_throughput

# Phases in execution order. Reliability runs last because malformed requests
# are the most likely to destabilise a participant's server.
PHASES = ("connectivity", "warmup", "benchmark", "throughput", "concurrency", "functional", "reliability")
FULL_EVALUATION = PHASES
SCORING_PHASES = {"benchmark", "throughput", "concurrency", "functional", "reliability"}

_run_lock = threading.Lock()
_state_lock = threading.Lock()
_state: dict[str, Any] = {"run_id": None, "thread": None, "cancel": None, "progress": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def recover_interrupted_runs() -> int:
    """Mark runs left 'running' by a previous process (e.g. app restart) as aborted."""
    if is_running():
        return 0
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE Evaluation_Runs SET evaluation_status='aborted', end_time=? WHERE evaluation_status='running'",
            (_now(),),
        )
        return cur.rowcount


def is_running() -> bool:
    thread = _state.get("thread")
    return bool(thread and thread.is_alive())


def current_progress() -> dict[str, Any] | None:
    with _state_lock:
        progress = _state.get("progress")
        return json.loads(json.dumps(progress)) if progress else None


def request_cancel() -> bool:
    cancel = _state.get("cancel")
    if cancel and is_running():
        cancel.set()
        with get_connection() as conn:
            conn.execute("UPDATE Evaluation_Runs SET cancel_requested=1 WHERE run_id=?", (_state["run_id"],))
        return True
    return False


def _test_case_fingerprint(cases: list[dict[str, Any]]) -> str:
    blob = json.dumps([(c["test_id"], c["query"], c["expected_results"]) for c in cases], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def preflight(phases: list[str], team_ids: list[int] | None = None) -> tuple[list[str], list[dict], list[dict]]:
    """Return (problems, teams, test_cases). Problems block the run."""
    settings = load_settings()
    problems = list(validate_settings(settings))
    teams = team_manager.list_teams(active_only=True)
    if team_ids:
        teams = [t for t in teams if t["team_id"] in set(team_ids)]
    cases = test_manager.list_test_cases()
    if not teams:
        problems.append("No active teams selected.")
    unknown = [p for p in phases if p not in PHASES]
    if unknown:
        problems.append(f"Unknown phases: {unknown}")
    if not cases and set(phases) - {"reliability"}:
        problems.append("No test cases defined. Add test cases before running an evaluation.")
    if "functional" in phases and not scorable_test_cases(cases, int(settings["quality"]["relevance_threshold"])):
        problems.append("Functional evaluation needs test cases with relevant expected results.")
    if "concurrency" in phases:
        try:
            concurrency_levels(settings)
        except ValueError as exc:
            problems.append(str(exc))
    if "reliability" in phases and not load_reliability_tests():
        problems.append("No reliability tests defined in config/reliability_tests.json.")
    return problems, teams, cases


def start_run(phases: list[str], team_ids: list[int] | None = None) -> int:
    """Create a run and start it in a background thread. Raises RuntimeError if
    a run is already in progress or ValueError if the preflight check fails."""
    if not _run_lock.acquire(blocking=False):
        raise RuntimeError("An evaluation run is already in progress.")
    try:
        with get_connection() as conn:
            busy = conn.execute("SELECT run_id FROM Evaluation_Runs WHERE evaluation_status='running'").fetchone()
        if busy:
            raise RuntimeError(f"Run {busy['run_id']} is still marked as running.")
        ordered = [p for p in PHASES if p in phases]
        problems, teams, cases = preflight(ordered, team_ids)
        if problems:
            raise ValueError(" ".join(problems))

        settings = load_settings()
        reliability_tests = load_reliability_tests()
        snapshot = {
            "settings": settings,
            "reliability_tests": reliability_tests,
            "teams": [
                {"team_id": t["team_id"], "team_name": t["team_name"], "endpoint_url": t["endpoint_url"],
                 "http_method": t["http_method"], "request_config": mask_request_config(t["request_config"])}
                for t in teams
            ],
            "test_cases": {"count": len(cases), "ids": [c["test_id"] for c in cases],
                           "fingerprint": _test_case_fingerprint(cases)},
            "phases": ordered,
        }
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO Evaluation_Runs (start_time, evaluation_status, configuration, phases, progress)"
                " VALUES (?, 'running', ?, ?, '{}')",
                (_now(), json.dumps(snapshot), json.dumps(ordered)),
            )
            run_id = int(cur.lastrowid)

        cancel = threading.Event()
        steps = len(teams) * len(ordered)
        with _state_lock:
            _state.update(run_id=run_id, cancel=cancel, progress={
                "run_id": run_id, "status": "running", "done_steps": 0, "total_steps": steps,
                "current": "Starting", "log": [], "errors": []})
        thread = threading.Thread(
            target=_thread_main, args=(run_id, ordered, teams, cases, settings, reliability_tests, cancel),
            name=f"evaluation-run-{run_id}", daemon=True,
        )
        _state["thread"] = thread
        thread.start()
        return run_id
    except Exception:
        _run_lock.release()
        raise


def _on_progress(message: str) -> None:
    with _state_lock:
        progress = _state["progress"]
        progress["current"] = message
        progress["log"].append(f"{datetime.now().strftime('%H:%M:%S')}  {message}")
        progress["log"] = progress["log"][-300:]
        if message.startswith("ERROR:"):
            progress["errors"].append(message[6:].strip())


def _step_done() -> None:
    with _state_lock:
        _state["progress"]["done_steps"] += 1


def _persist_progress(run_id: int) -> None:
    progress = current_progress() or {}
    with get_connection() as conn:
        conn.execute("UPDATE Evaluation_Runs SET progress=?, error_log=? WHERE run_id=?",
                     (json.dumps(progress), json.dumps(progress.get("errors", [])), run_id))


def _thread_main(run_id, phases, teams, cases, settings, reliability_tests, cancel) -> None:
    ctx = RunContext(run_id, settings, cancel, _on_progress)
    status = "completed"
    try:
        asyncio.run(_execute(ctx, phases, teams, cases, reliability_tests))
        if ctx.errors:
            status = "completed_with_errors"
    except RunCancelled:
        status = "cancelled"
        _on_progress("Run cancelled by administrator.")
    except Exception as exc:  # noqa: BLE001 - never let the thread die silently
        status = "failed"
        _on_progress(f"ERROR: Run failed: {exc}")
        _on_progress(traceback.format_exc(limit=5))
    finally:
        try:
            ctx.flush()
            if status in ("completed", "completed_with_errors") and SCORING_PHASES & set(phases):
                results_service.compute_and_store_scores(run_id)
                _on_progress("Scores computed.")
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            _on_progress(f"ERROR: Could not store results/scores: {exc}")
        with _state_lock:
            _state["progress"]["status"] = status
        _persist_progress(run_id)
        with get_connection() as conn:
            conn.execute("UPDATE Evaluation_Runs SET evaluation_status=?, end_time=? WHERE run_id=?",
                         (status, _now(), run_id))
        _run_lock.release()


async def _execute(ctx: RunContext, phases, teams, cases, reliability_tests) -> None:
    settings = ctx.settings
    queries = [(c["test_id"], c["query"]) for c in cases]
    default_query = cases[0]["query"] if cases else "test"

    for team in teams:
        name = team["team_name"]
        blocked = check_team_url(team, settings)
        if blocked:
            ctx.error(f"{name}: endpoint blocked by security policy - {blocked}. Team skipped.")
            for _ in phases:
                _step_done()
            continue

        unreachable = False
        async with make_client(settings) as client:
            for phase in phases:
                ctx.check_cancelled()
                if unreachable:
                    _step_done()
                    continue
                ctx.progress(f"{name}: {phase} started")
                try:
                    if phase == "connectivity":
                        cfg = effective_request_config(team, settings)
                        outcome = await execute_request(client, build_request(team, settings, default_query),
                                                        settings, cfg, retries=1)
                        ctx.record(team["team_id"], "connectivity", outcome, track=False,
                                   test_id=queries[0][0] if queries else None)
                        msg = (f"{name}: connectivity {outcome.outcome}"
                               + (f" ({outcome.latency_ms:.1f}ms)" if outcome.latency_ms else ""))
                        if outcome.outcome in TRANSPORT_FAILURES:
                            raise TeamUnreachable(f"connectivity check failed - {outcome.error_message}")
                        if outcome.success:
                            ctx.progress(msg)
                        else:
                            ctx.error(f"{msg} - {outcome.error_message}")
                    elif phase == "warmup":
                        await run_warmup(ctx, team, client, queries)
                    elif phase == "benchmark":
                        # Warm-up always precedes the benchmark on the same connection pool;
                        # warm-up rows are stored with phase='warmup' and excluded from scoring.
                        if "warmup" not in phases:
                            await run_warmup(ctx, team, client, queries)
                        await run_benchmark(ctx, team, client, queries)
                    elif phase == "throughput":
                        await run_throughput(ctx, team, client, queries)
                    elif phase == "concurrency":
                        await run_concurrency(ctx, team, queries)
                    elif phase == "functional":
                        await run_functional(ctx, team, client, cases)
                    elif phase == "reliability":
                        await run_reliability(ctx, team, client, reliability_tests, default_query)
                except RunCancelled:
                    raise
                except TeamUnreachable as exc:
                    unreachable = True
                    ctx.error(f"{name}: unreachable during {phase} ({exc}). Remaining phases skipped for this team.")
                except Exception as exc:  # noqa: BLE001 - continue with the next phase/team
                    ctx.error(f"{name}: {phase} failed - {type(exc).__name__}: {exc}")
                finally:
                    ctx.flush()
                    _step_done()
                    _persist_progress(ctx.run_id)
