import math

import pytest

from backend.config import ARCHITECTURE_CHECKLIST, load_settings
from backend.latency_evaluator import latency_stats, percentile
from backend.quality_evaluator import compute_quality_metrics
from backend.scoring_engine import score_team
from backend.security import validate_endpoint_url

EXPECTED = [
    {"song_id": "101", "relevance": 3},
    {"song_id": "102", "relevance": 2},
    {"song_id": "103", "relevance": 1},
]


def ids(*values):
    return [{"song_id": v} for v in values]


def test_precision_recall_ndcg_perfect():
    m = compute_quality_metrics(ids("101", "102", "103"), EXPECTED, k=3)
    assert m["precision"] == 1.0
    assert m["recall"] == 1.0
    assert m["ndcg"] == pytest.approx(1.0)


def test_precision_at_k_uses_k_denominator():
    m = compute_quality_metrics(ids("101", "102", "103", "999", "998"), EXPECTED, k=5)
    assert m["precision"] == pytest.approx(3 / 5)
    m2 = compute_quality_metrics(ids("101"), EXPECTED, k=10, precision_denominator="min_k_relevant")
    assert m2["precision"] == pytest.approx(1 / 3)


def test_ndcg_rewards_correct_order():
    best = compute_quality_metrics(ids("101", "102", "103"), EXPECTED, k=3)["ndcg"]
    worst = compute_quality_metrics(ids("103", "102", "101"), EXPECTED, k=3)["ndcg"]
    assert best > worst
    # hand calculation, exponential gain
    dcg = 1 / math.log2(2) + 3 / math.log2(3) + 7 / math.log2(4)
    idcg = 7 / math.log2(2) + 3 / math.log2(3) + 1 / math.log2(4)
    assert worst == pytest.approx(dcg / idcg)


def test_duplicates_do_not_inflate():
    m = compute_quality_metrics(ids("101", "101", "101"), EXPECTED, k=3)
    assert m["relevant_hits"] == 1
    assert m["duplicates"] == 2
    assert m["precision"] == pytest.approx(1 / 3)


def test_empty_and_missing_results():
    for returned in ([], None):
        m = compute_quality_metrics(returned, EXPECTED, k=10)
        assert (m["precision"], m["recall"], m["ndcg"]) == (0, 0, 0)


def test_name_fallback_only_without_id():
    expected = [{"song_id": "101", "song_name": "Café  Song!", "relevance": 3}]
    m = compute_quality_metrics([{"song_name": "cafe song"}], expected, k=1)
    assert m["precision"] == 1.0 and m["matched_by_name"] == 1
    # an item that has a (wrong) id is not rescued by its name
    m2 = compute_quality_metrics([{"song_id": "555", "song_name": "Cafe Song"}], expected, k=1)
    assert m2["precision"] == 0.0


def test_ids_compare_as_strings():
    m = compute_quality_metrics([{"song_id": 101}], EXPECTED, k=1)
    assert m["relevant_hits"] == 1


def test_percentiles():
    data = list(range(1, 101))
    assert percentile(data, 50) == pytest.approx(50.5)
    assert percentile(data, 95) == pytest.approx(95.05)
    stats = latency_stats([10, 20, 30], p99_min_samples=50)
    assert stats["p99_ms"] is None and stats["avg_ms"] == 20 and stats["p50_ms"] == 20


@pytest.mark.parametrize("url,ok", [
    ("http://192.168.1.10:8000/search", True),
    ("http://127.0.0.1:9001/search", True),
    ("http://localhost:9001/search", True),
    ("ftp://192.168.1.10/search", False),
    ("http://8.8.8.8/search", False),
    ("http://169.254.169.254/latest/meta-data", False),
    ("http://127.0.0.1:5432/", False),
    ("http://127.0.0.1:8501/", False),
    ("http://user:pass@192.168.1.10:8000/", False),
    ("not a url", False),
])
def test_url_validation(url, ok):
    assert validate_endpoint_url(url, load_settings()["security"]).ok is ok


def full_metrics(p95=250.0):
    return {
        "functional": {"tests": 5, "precision": 0.8, "ndcg": 0.75, "recall": 0.6},
        "benchmark": {"total": 20, "count": 20, "p95_ms": p95},
        "reliability": {"total": 7, "passed": 7},
        "concurrency": [{"level": 1, "p95_ms": 100, "throughput_rps": 10, "error_rate": 0.0}],
        "success_rate": 1.0,
    }


def test_score_team_formulas():
    scoring = load_settings()["scoring"]
    review = dict(ARCHITECTURE_CHECKLIST)  # full marks
    cost = {"api_cost_per_1k_usd": 0.5, "infra_cost_per_1k_usd": 0.0}
    result = score_team(full_metrics(), cost, review, scoring)
    c = result["categories"]
    assert c["functional"]["points"] == pytest.approx((0.4 * 0.8 + 0.6 * 0.75) * 30)
    assert c["performance"]["points"] == pytest.approx(20)  # p95 250 < target 500
    assert c["reliability"]["points"] == pytest.approx(15)
    assert c["scalability"]["points"] == pytest.approx(10)
    assert c["cost"]["points"] == pytest.approx(10)
    assert c["architecture"]["points"] == pytest.approx(15)
    assert result["final_score"] == pytest.approx(23.1 + 20 + 15 + 10 + 10 + 15)


def test_performance_scales_with_p95():
    scoring = load_settings()["scoring"]
    result = score_team(full_metrics(p95=1000), None, None, scoring)
    assert result["categories"]["performance"]["points"] == pytest.approx(10)  # 20 * 500/1000
    assert "cost" in result["missing"] and "architecture" in result["missing"]


def test_missing_categories_score_zero():
    result = score_team({}, None, None, load_settings()["scoring"])
    assert result["final_score"] == 0
    assert set(result["missing"]) == {"functional", "performance", "reliability", "scalability", "cost",
                                      "architecture"}
