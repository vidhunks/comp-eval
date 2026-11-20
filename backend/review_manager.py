"""Manual architecture and production-readiness reviews.

Reviews with ``run_id = NULL`` are the team's current review. When scores are
computed, the review values used are copied into ``Team_Scores.details`` and
into run-specific Architecture_Reviews rows, so past results stay reproducible
even if the current review is edited later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.config import ARCHITECTURE_CHECKLIST
from backend.database import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_review(team_id: int, entries: dict[str, tuple[float, str]], run_id: int | None = None) -> None:
    """``entries`` maps checklist category -> (awarded_points, comments)."""
    with get_connection() as conn:
        for category, (points, comments) in entries.items():
            if category not in ARCHITECTURE_CHECKLIST:
                raise ValueError(f"Unknown review category '{category}'.")
            maximum = ARCHITECTURE_CHECKLIST[category]
            if not 0 <= float(points) <= maximum:
                raise ValueError(f"{category}: points must be between 0 and {maximum}.")
            conn.execute(
                "DELETE FROM Architecture_Reviews WHERE team_id=? AND IFNULL(run_id, -1)=IFNULL(?, -1)"
                " AND review_category=?",
                (team_id, run_id, category),
            )
            conn.execute(
                "INSERT INTO Architecture_Reviews (team_id, run_id, review_category, awarded_points, comments,"
                " updated_at) VALUES (?,?,?,?,?,?)",
                (team_id, run_id, category, float(points), comments or "", _now()),
            )


def get_review(team_id: int, run_id: int | None = None) -> dict[str, dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT review_category, awarded_points, comments FROM Architecture_Reviews"
            " WHERE team_id=? AND IFNULL(run_id, -1)=IFNULL(?, -1)",
            (team_id, run_id),
        ).fetchall()
    return {r["review_category"]: {"points": r["awarded_points"], "comments": r["comments"]} for r in rows}


def current_review_points(team_id: int) -> dict[str, float] | None:
    review = get_review(team_id)
    return {cat: v["points"] for cat, v in review.items()} if review else None


def snapshot_review(team_id: int, run_id: int) -> None:
    """Copy the team's current review into run-specific rows."""
    review = get_review(team_id)
    if review:
        save_review(team_id, {c: (v["points"], v["comments"]) for c, v in review.items()}, run_id=run_id)
