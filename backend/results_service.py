"""Aggregation of stored measurements into metrics, score persistence,
leaderboard queries and CSV export. Every metric here is recomputed from the
individual request rows in Evaluation_Results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from backend import review_manager, scoring_engine, team_manager
from backend.config import EXPORT_DIR
from backend.database import get_connection
from backend.latency_evaluator import latency_stats

PERFORMANCE_PHASES = ("benchmark", "throughput", "concurrency")


def list_runs() -> list[dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT run_id, start_time, end_time, evaluation_status, phases FROM Evaluation_Runs ORDER BY run_id DESC"
        ).fetchall()
    runs = []
    for r in rows:
        run = dict(r)
        run["phases"] = json.loads(run["phases"] or "[]")
        runs.append(run)
    return runs


def get_run(run_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM Evaluation_Runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        return None
    run = dict(row)
    for key in ("configuration", "phases", "progress", "error_log"):
        run[key] = json.loads(run[key] or ("[]" if key in ("phases", "error_log") else "{}"))
    return run


def run_team_names(run_id: int) -> dict[int, str]:
    """Team names as recorded in the run snapshot (works for teams deleted since)."""
    run = get_run(run_id)
    names = {}
    if run:
        for team in run["configuration"].get("teams", []):
            names[int(team["team_id"])] = team["team_name"]
    for tid, name in team_manager.team_names().items():
        names.setdefault(tid, name)
    return names


def results_frame(run_id: int, phases: tuple[str, ...] | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM Evaluation_Results WHERE run_id=?"
    params: list[Any] = [run_id]
    if phases:
        sql += f" AND phase IN ({','.join('?' * len(phases))})"
        params.extend(phases)
    with get_connection() as conn:
        df = pd.read_sql_query(sql + " ORDER BY result_id", conn, params=params)
    return df


def aggregate_team_metrics(df: pd.DataFrame, team_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    """Compute all metrics for one team from that run's result rows."""
    p99_min = int(settings["statistics"]["p99_min_samples"])
    t = df[df["team_id"] == team_id]
    metrics: dict[str, Any] = {}

    bench = t[t["phase"] == "benchmark"]
    if len(bench):
        ok = bench[bench["success"] == 1]
        metrics["benchmark"] = {**latency_stats(ok["latency_ms"].tolist(), p99_min),
                                "total": len(bench), "failures": int((bench["success"] == 0).sum())}

    thr = t[t["phase"] == "throughput_summary"]
    if len(thr):
        metrics["throughput"] = json.loads(thr.iloc[-1]["details"])

    conc = t[t["phase"] == "concurrency_summary"]
    if len(conc):
        metrics["concurrency"] = sorted((json.loads(d) for d in conc["details"]), key=lambda x: x["level"])

    func = t[t["phase"] == "functional"]
    if len(func):
        metrics["functional"] = {
            "tests": len(func),
            "precision": float(func["precision_score"].fillna(0).mean()),
            "recall": float(func["recall_score"].fillna(0).mean()),
            "ndcg": float(func["ndcg_score"].fillna(0).mean()),
        }

    rel = t[t["phase"] == "reliability"]
    if len(rel):
        counted = rel[~rel["details"].fillna("{}").map(lambda d: bool(json.loads(d).get("skipped")))]
        metrics["reliability"] = {"total": len(counted), "passed": int(counted["success"].sum()),
                                  "skipped": len(rel) - len(counted)}

    perf = t[t["phase"].isin(PERFORMANCE_PHASES)]
    if len(perf):
        metrics["requests_total"] = len(perf)
        metrics["requests_failed"] = int((perf["success"] == 0).sum())
        metrics["success_rate"] = float((perf["success"] == 1).mean())
        metrics["error_rate"] = 1 - metrics["success_rate"]
        metrics["outcomes"] = perf["outcome"].value_counts().to_dict()
    else:
        metrics["success_rate"] = None
        metrics["error_rate"] = None
    return metrics


def compute_and_store_scores(run_id: int, scoring: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """(Re)compute scores for every team in a run.

    Measurements and statistics settings come from the run's configuration snapshot.
    Scoring settings default to the snapshot too; pass ``scoring`` to rescore with
    new weights (the scoring config actually used is stored with each score).
    Current architecture reviews and cost data are snapshotted at this point.
    """
    run = get_run(run_id)
    if run is None:
        raise ValueError(f"Run {run_id} not found.")
    settings = run["configuration"]["settings"]
    scoring = scoring or settings["scoring"]
    df = results_frame(run_id)
    costs = team_manager.get_costs()
    team_ids = [int(t["team_id"]) for t in run["configuration"].get("teams", [])]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stored = []

    with get_connection() as conn:
        conn.execute("DELETE FROM Team_Scores WHERE run_id=?", (run_id,))
    for team_id in team_ids:
        metrics = aggregate_team_metrics(df, team_id, settings)
        review_manager.snapshot_review(team_id, run_id)
        review = {c: v["points"] for c, v in review_manager.get_review(team_id, run_id).items()} or None
        cost = costs.get(team_id)
        result = scoring_engine.score_team(metrics, cost, review, scoring)
        cats = result["categories"]
        details = {"categories": cats, "metrics": metrics, "cost_input": cost, "review_input": review,
                   "scoring_config": scoring, "missing": result["missing"]}
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO Team_Scores (run_id, team_id, functional_score, performance_score, reliability_score,"
                " scalability_score, cost_score, architecture_score, final_score, details, computed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, team_id, cats["functional"]["points"], cats["performance"]["points"],
                 cats["reliability"]["points"], cats["scalability"]["points"], cats["cost"]["points"],
                 cats["architecture"]["points"], result["final_score"], json.dumps(details, default=str), now),
            )
        stored.append({"team_id": team_id, **result})
    return stored


def scores_frame(run_id: int) -> pd.DataFrame:
    with get_connection() as conn:
        df = pd.read_sql_query("SELECT * FROM Team_Scores WHERE run_id=?", conn, params=(run_id,))
    if df.empty:
        return df
    names = run_team_names(run_id)
    df.insert(2, "team_name", df["team_id"].map(lambda t: names.get(int(t), f"Team {t}")))
    df = df.sort_values(["final_score", "functional_score", "performance_score"], ascending=False)
    # Ties share a rank (standard competition ranking: 1, 2, 2, 4).
    df.insert(0, "rank", df["final_score"].rank(method="min", ascending=False).astype(int))
    return df.reset_index(drop=True)


def team_metrics_table(run_id: int) -> pd.DataFrame:
    """One row per team with the headline metrics shown on the results dashboard."""
    run = get_run(run_id)
    if run is None:
        return pd.DataFrame()
    settings = run["configuration"]["settings"]
    df = results_frame(run_id)
    names = run_team_names(run_id)
    scores = scores_frame(run_id)
    final = dict(zip(scores["team_id"], scores["final_score"])) if not scores.empty else {}
    rows = []
    for team in run["configuration"].get("teams", []):
        tid = int(team["team_id"])
        m = aggregate_team_metrics(df, tid, settings)
        b = m.get("benchmark", {})
        f = m.get("functional", {})
        rows.append({
            "team_id": tid,
            "team_name": names.get(tid),
            "avg_latency_ms": b.get("avg_ms"),
            "p50_latency_ms": b.get("p50_ms"),
            "p95_latency_ms": b.get("p95_ms"),
            "p99_latency_ms": b.get("p99_ms"),
            "min_latency_ms": b.get("min_ms"),
            "max_latency_ms": b.get("max_ms"),
            "benchmark_success": f"{b.get('count', 0)}/{b.get('total', 0)}" if b else None,
            "throughput_rps": m.get("throughput", {}).get("throughput_rps"),
            "error_rate_pct": None if m.get("error_rate") is None else m["error_rate"] * 100,
            "precision_at_k": f.get("precision"),
            "recall_at_k": f.get("recall"),
            "ndcg_at_k": f.get("ndcg"),
            "reliability": (f"{m['reliability']['passed']}/{m['reliability']['total']}"
                            if m.get("reliability") else None),
            "final_score": final.get(tid),
        })
    return pd.DataFrame(rows)


def concurrency_frame(run_id: int) -> pd.DataFrame:
    df = results_frame(run_id, ("concurrency_summary",))
    if df.empty:
        return df
    names = run_team_names(run_id)
    rows = [{"team_name": names.get(int(r.team_id)), **json.loads(r.details)} for r in df.itertuples()]
    return pd.DataFrame(rows)


def export_run_csv(run_id: int) -> dict[str, str]:
    """Write CSV files for a run into exports/ and return {label: path}."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    names = run_team_names(run_id)
    paths = {}

    raw = results_frame(run_id)
    if not raw.empty:
        raw.insert(3, "team_name", raw["team_id"].map(lambda t: names.get(int(t))))
    files = {
        "raw_results": raw,
        "team_metrics": team_metrics_table(run_id),
        "scores": scores_frame(run_id).drop(columns=["details"], errors="ignore"),
        "concurrency": concurrency_frame(run_id),
    }
    for label, frame in files.items():
        path = EXPORT_DIR / f"run{run_id}_{label}_{stamp}.csv"
        frame.to_csv(path, index=False)
        paths[label] = str(path)
    return paths


def latest_scored_run_ids() -> list[int]:
    with get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT run_id FROM Team_Scores ORDER BY run_id DESC").fetchall()
    return [int(r["run_id"]) for r in rows]


def latest_scored_run_id() -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT r.run_id FROM Evaluation_Runs r WHERE EXISTS (SELECT 1 FROM Team_Scores s WHERE s.run_id=r.run_id)"
            " ORDER BY r.run_id DESC LIMIT 1"
        ).fetchone()
    return int(row["run_id"]) if row else None
