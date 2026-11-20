"""Scoring engine. Pure functions: metrics in, points out.

Every category returns ``{"points", "max", "evaluated", "formula"}`` where
``formula`` is a human-readable trace of the calculation, so a score can be
reproduced by hand from the stored measurements.
"""

from __future__ import annotations

from typing import Any

from backend.config import ARCHITECTURE_CHECKLIST

CATEGORIES = ("functional", "performance", "reliability", "scalability", "cost", "architecture")


def _cat(points: float, maximum: float, evaluated: bool, formula: str, **extra: Any) -> dict[str, Any]:
    return {"points": round(max(0.0, min(points, maximum)), 4), "max": maximum,
            "evaluated": evaluated, "formula": formula, **extra}


def _ratio(target: float, actual: float | None) -> float:
    """min(1, target / actual) with 0 for missing data and 1 when actual is 0."""
    if actual is None:
        return 0.0
    if actual <= 0:
        return 1.0
    return min(1.0, target / actual)


def score_functional(metrics: dict[str, Any] | None, weight: float, cfg: dict[str, Any]) -> dict[str, Any]:
    if not metrics or not metrics.get("tests"):
        return _cat(0, weight, False, "Not evaluated (no functional results in this run).")
    pw, nw = float(cfg["precision_weight"]), float(cfg["ndcg_weight"])
    total_w = pw + nw or 1.0
    p, n = metrics["precision"] or 0.0, metrics["ndcg"] or 0.0
    combined = (pw * p + nw * n) / total_w
    return _cat(weight * combined, weight, True,
                f"{weight:g} x ({pw:g} x P@K {p:.4f} + {nw:g} x NDCG@K {n:.4f}) / {total_w:g} "
                f"= {weight * combined:.2f}  [{metrics['tests']} tests]")


def score_performance(bench: dict[str, Any] | None, weight: float, cfg: dict[str, Any]) -> dict[str, Any]:
    if not bench or not bench.get("total"):
        return _cat(0, weight, False, "Not evaluated (no benchmark results in this run).")
    target = float(cfg["target_p95_ms"])
    p95 = bench.get("p95_ms")
    if p95 is None:
        return _cat(0, weight, True, "0 (no successful benchmark requests).")
    ratio = _ratio(target, p95)
    success_rate = bench["count"] / bench["total"]
    if cfg.get("apply_success_rate", True):
        points = weight * ratio * success_rate
        formula = (f"{weight:g} x min(1, {target:g} / p95 {p95:.1f}) x success rate {success_rate:.3f} "
                   f"= {points:.2f}")
    else:
        points = weight * ratio
        formula = f"{weight:g} x min(1, {target:g} / p95 {p95:.1f}) = {points:.2f}"
    return _cat(points, weight, True, formula)


def score_reliability(rel: dict[str, Any] | None, weight: float) -> dict[str, Any]:
    if not rel or not rel.get("total"):
        return _cat(0, weight, False, "Not evaluated (no reliability results in this run).")
    points = weight * rel["passed"] / rel["total"]
    return _cat(points, weight, True, f"{weight:g} x {rel['passed']} / {rel['total']} passed = {points:.2f}")


def score_scalability(levels: list[dict[str, Any]] | None, weight: float, cfg: dict[str, Any]) -> dict[str, Any]:
    """Average over concurrency levels of a weighted mix of
    latency (min(1, target_p95 / p95)), throughput (min(1, rps / target_rps)) and (1 - error rate)."""
    if not levels:
        return _cat(0, weight, False, "Not evaluated (no concurrency results in this run).")
    lw, tw, ew = float(cfg["latency_weight"]), float(cfg["throughput_weight"]), float(cfg["error_weight"])
    total_w = lw + tw + ew or 1.0
    target_p95, target_rps = float(cfg["target_p95_ms"]), float(cfg["target_throughput_rps"])
    parts, per_level = [], []
    for lvl in levels:
        lat = _ratio(target_p95, lvl.get("p95_ms"))
        thr = min(1.0, (lvl.get("throughput_rps") or 0.0) / target_rps)
        err = 1.0 - float(lvl.get("error_rate", 1.0))
        level_score = (lw * lat + tw * thr + ew * err) / total_w
        parts.append(level_score)
        per_level.append(f"L{lvl['level']}: lat {lat:.3f}, thr {thr:.3f}, ok {err:.3f} -> {level_score:.3f}")
    avg = sum(parts) / len(parts)
    return _cat(weight * avg, weight, True,
                f"{weight:g} x mean[{'; '.join(per_level)}] = {weight * avg:.2f} "
                f"(weights lat {lw:g}/thr {tw:g}/err {ew:g})")


def score_cost(cost: dict[str, Any] | None, success_rate: float | None, weight: float,
               cfg: dict[str, Any]) -> dict[str, Any]:
    missing = min(float(cfg.get("missing_data_points", 0)), weight)
    if not cost or (cost.get("api_cost_per_1k_usd") is None and cost.get("infra_cost_per_1k_usd") is None):
        return _cat(missing, weight, False, f"No cost data entered -> {missing:g} points.")
    per_1k = float(cost.get("api_cost_per_1k_usd") or 0) + float(cost.get("infra_cost_per_1k_usd") or 0)
    if success_rate is None:
        success_rate = 1.0
        rate_note = "success rate unknown, assumed 1.0"
    else:
        rate_note = f"success rate {success_rate:.3f}"
    if success_rate <= 0:
        return _cat(0, weight, True, f"0 (no successful requests; cost per successful 1k undefined).")
    per_success_1k = per_1k / success_rate
    target = float(cfg["target_cost_per_1k_usd"])
    if per_success_1k <= 0:
        ratio = 1.0
    elif target <= 0:
        ratio = 0.0
    else:
        ratio = min(1.0, target / per_success_1k)
    points = weight * ratio
    return _cat(points, weight, True,
                f"cost/1k ${per_1k:.4f} / {rate_note} = ${per_success_1k:.4f} per successful 1k; "
                f"{weight:g} x min(1, ${target:g} / ${per_success_1k:.4f}) = {points:.2f}",
                cost_per_successful_1k=per_success_1k)


def score_architecture(review: dict[str, float] | None, weight: float) -> dict[str, Any]:
    max_total = sum(ARCHITECTURE_CHECKLIST.values())
    if not review:
        return _cat(0, weight, False, "No architecture review entered.")
    awarded = sum(min(float(review.get(cat, 0)), mx) for cat, mx in ARCHITECTURE_CHECKLIST.items())
    points = weight * awarded / max_total
    return _cat(points, weight, True, f"{weight:g} x {awarded:g} / {max_total} checklist points = {points:.2f}")


def score_team(
    metrics: dict[str, Any],
    cost: dict[str, Any] | None,
    review: dict[str, float] | None,
    scoring: dict[str, Any],
) -> dict[str, Any]:
    """Compute every category plus the final score for one team."""
    w = {k: float(v) for k, v in scoring["weights"].items()}
    categories = {
        "functional": score_functional(metrics.get("functional"), w["functional"], scoring["functional"]),
        "performance": score_performance(metrics.get("benchmark"), w["performance"], scoring["performance"]),
        "reliability": score_reliability(metrics.get("reliability"), w["reliability"]),
        "scalability": score_scalability(metrics.get("concurrency"), w["scalability"], scoring["scalability"]),
        "cost": score_cost(cost, metrics.get("success_rate"), w["cost"], scoring["cost"]),
        "architecture": score_architecture(review, w["architecture"]),
    }
    final = round(sum(c["points"] for c in categories.values()), 2)
    return {
        "categories": categories,
        "final_score": final,
        "max_total": sum(w.values()),
        "missing": [name for name, c in categories.items() if not c["evaluated"]],
    }
