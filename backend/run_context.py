"""Shared state for one evaluation run: result buffering, progress and cancellation.

Results are buffered in memory and written to SQLite only between phases, so
database I/O never blocks the event loop while requests are being timed.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

from backend.database import get_connection
from backend.request_executor import RequestOutcome


class RunCancelled(Exception):
    pass


class TeamUnreachable(Exception):
    """Raised after too many consecutive connection failures/timeouts for a team."""


TRANSPORT_FAILURES = {"connection_error", "timeout"}


class RunContext:
    def __init__(
        self,
        run_id: int,
        settings: dict[str, Any],
        cancel_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.settings = settings
        self.cancel_event = cancel_event or threading.Event()
        self.on_progress = on_progress or (lambda msg: None)
        self.errors: list[str] = []
        self._buffer: list[tuple] = []
        self._max_payload = int(settings["request"]["max_stored_payload_chars"])
        self._max_consecutive = int(settings["request"].get("max_consecutive_failures", 0))
        self._consecutive: dict[int, int] = {}

    # ----------------------------------------------------------- recording
    def record(
        self,
        team_id: int,
        phase: str,
        outcome: RequestOutcome | None,
        *,
        test_id: str | None = None,
        request_index: int | None = None,
        concurrency_level: int | None = None,
        success: bool | None = None,
        metrics: dict[str, float | None] | None = None,
        details: dict[str, Any] | None = None,
        track: bool = True,
    ) -> None:
        rec = outcome.to_record(self._max_payload) if outcome else {
            "outcome": "summary", "success": 1, "latency_ms": None, "status_code": None,
            "error_message": None, "started_at": None, "attempts": 0, "response_payload": None,
        }
        if success is not None:
            rec["success"] = int(success)
        metrics = metrics or {}
        self._buffer.append((
            self.run_id, team_id, test_id, phase, request_index, concurrency_level, rec["started_at"],
            rec["latency_ms"], rec["status_code"], rec["success"], rec["outcome"], rec["attempts"],
            rec["error_message"], rec["response_payload"], metrics.get("precision"), metrics.get("recall"),
            metrics.get("ndcg"), json.dumps(details, default=str) if details is not None else None,
        ))
        if outcome is not None and track:
            self._track(team_id, outcome)

    def _track(self, team_id: int, outcome: RequestOutcome) -> None:
        """Circuit breaker: stop hammering a team that is clearly down, so one dead
        endpoint cannot stall the whole run for (requests x timeout) seconds."""
        if outcome.outcome in TRANSPORT_FAILURES:
            self._consecutive[team_id] = self._consecutive.get(team_id, 0) + 1
        else:
            self._consecutive[team_id] = 0
        if self._max_consecutive and self._consecutive[team_id] >= self._max_consecutive:
            self._consecutive[team_id] = 0
            raise TeamUnreachable(
                f"{self._max_consecutive} consecutive connection failures/timeouts (last: {outcome.error_message})"
            )

    def flush(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        with get_connection() as conn:
            conn.executemany(
                "INSERT INTO Evaluation_Results (run_id, team_id, test_id, phase, request_index, concurrency_level,"
                " started_at, latency_ms, status_code, success, outcome, attempts, error_message, response_payload,"
                " precision_score, recall_score, ndcg_score, details)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

    # ------------------------------------------------------------- control
    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise RunCancelled()

    def progress(self, message: str) -> None:
        self.on_progress(message)

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.on_progress("ERROR: " + message)
