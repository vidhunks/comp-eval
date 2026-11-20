"""Functional correctness: Precision@K, Recall@K and NDCG@K against graded references.

Matching rules
* Results are matched on stable IDs (``id_field``), compared as trimmed strings.
* Only when a returned item has no ID is its name normalised (case, accents,
  punctuation, whitespace) and matched against reference names.
* Only the first K returned items count. A repeated item scores zero gain
  after its first occurrence (duplicates cannot inflate the score).
* Empty or missing results score 0 on every metric.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from backend.request_executor import (
    build_request,
    effective_request_config,
    execute_request,
    normalize_text,
)
from backend.run_context import RunContext


def _gain(grade: float, mode: str) -> float:
    return (2 ** grade - 1) if mode == "exponential" else float(grade)


def compute_quality_metrics(
    returned: list[dict[str, Any]] | None,
    expected: list[dict[str, Any]],
    *,
    k: int,
    id_field: str = "song_id",
    name_field: str = "song_name",
    relevance_threshold: int = 1,
    precision_denominator: str = "k",
    gain: str = "exponential",
) -> dict[str, Any]:
    by_id: dict[str, int] = {}
    by_name: dict[str, tuple[str, int]] = {}
    for item in expected:
        grade = int(item.get("relevance", 0))
        key = str(item["song_id"]).strip() if item.get("song_id") not in (None, "") else None
        name = normalize_text(item["song_name"]) if item.get("song_name") else None
        if key is not None:
            by_id[key] = grade
        if name:
            by_name.setdefault(name, (key or f"name:{name}", grade))
            if key is None:
                by_id[f"name:{name}"] = grade

    total_relevant = sum(1 for g in by_id.values() if g >= relevance_threshold)
    ideal = sorted((g for g in by_id.values() if g > 0), reverse=True)[:k]
    idcg = sum(_gain(g, gain) / math.log2(i + 2) for i, g in enumerate(ideal))

    top_k = list(returned or [])[:k]
    seen: set[str] = set()
    hits = duplicates = matched_by_name = 0
    dcg = 0.0
    ranked: list[dict[str, Any]] = []
    for pos, item in enumerate(top_k):
        item = item if isinstance(item, dict) else {}
        raw_id = item.get(id_field)
        grade, key = 0, None
        if raw_id not in (None, ""):
            key = str(raw_id).strip()
            grade = by_id.get(key, 0)
        elif item.get(name_field):
            match = by_name.get(normalize_text(item[name_field]))
            if match:
                key, grade = match
                matched_by_name += 1
            else:
                key = "name:" + normalize_text(item[name_field])
        duplicate = key is not None and key in seen
        if duplicate:
            duplicates += 1
            grade = 0
        elif key is not None:
            seen.add(key)
        if grade >= relevance_threshold:
            hits += 1
        dcg += _gain(grade, gain) / math.log2(pos + 2)
        ranked.append({"rank": pos + 1, "key": key, "grade": grade, "duplicate": duplicate})

    if total_relevant == 0:
        precision = recall = None
    else:
        denom = k if precision_denominator == "k" else min(k, total_relevant)
        precision = hits / denom
        recall = hits / total_relevant
    ndcg = dcg / idcg if idcg > 0 else None
    return {
        "precision": precision,
        "recall": recall,
        "ndcg": ndcg,
        "k": k,
        "returned_count": len(returned or []),
        "relevant_hits": hits,
        "total_relevant": total_relevant,
        "duplicates": duplicates,
        "matched_by_name": matched_by_name,
        "ranking": ranked,
    }


def scorable_test_cases(test_cases: list[dict[str, Any]], relevance_threshold: int) -> list[dict[str, Any]]:
    """Test cases with at least one relevant reference result."""
    return [
        tc for tc in test_cases
        if any(int(e.get("relevance", 0)) >= relevance_threshold for e in tc["expected_results"])
    ]


async def run_functional(
    ctx: RunContext, team: dict[str, Any], client: httpx.AsyncClient, test_cases: list[dict[str, Any]]
) -> dict[str, Any]:
    settings = ctx.settings
    q = settings["quality"]
    cfg = effective_request_config(team, settings)
    cases = scorable_test_cases(test_cases, int(q["relevance_threshold"]))
    totals = {"precision": [], "recall": [], "ndcg": []}

    for i, tc in enumerate(cases):
        ctx.check_cancelled()
        outcome = await execute_request(client, build_request(team, settings, tc["query"]), settings, cfg)
        # Failed or malformed responses are evaluated as an empty result list (all metrics 0).
        returned = outcome.results if outcome.success else []
        metrics = compute_quality_metrics(
            returned, tc["expected_results"], k=int(q["k"]), id_field=cfg["id_field"],
            name_field=cfg["name_field"], relevance_threshold=int(q["relevance_threshold"]),
            precision_denominator=q["precision_denominator"], gain=q["ndcg_gain"],
        )
        for key in totals:
            totals[key].append(metrics[key] or 0.0)
        ctx.record(
            team["team_id"], "functional", outcome, test_id=tc["test_id"], request_index=i,
            metrics={key: metrics[key] or 0.0 for key in totals},
            details={k: v for k, v in metrics.items() if k not in ("precision", "recall", "ndcg")},
        )

    n = len(cases)
    summary = {key: (sum(vals) / n if n else None) for key, vals in totals.items()}
    ctx.progress(
        f"{team['team_name']}: functional done - {n} tests, "
        f"P@{q['k']}={_fmt(summary['precision'])}, NDCG@{q['k']}={_fmt(summary['ndcg'])}"
    )
    return {**summary, "tests": n}


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
