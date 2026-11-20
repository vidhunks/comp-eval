"""Loading and saving of the JSON configuration files."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
EXPORT_DIR = PROJECT_ROOT / "exports"
SETTINGS_PATH = CONFIG_DIR / "settings.json"
RELIABILITY_TESTS_PATH = CONFIG_DIR / "reliability_tests.json"

# Fallback values, used when a key is missing from settings.json.
DEFAULT_SETTINGS: dict[str, Any] = {
    "request": {
        "timeout_seconds": 15,
        "warmup_requests": 3,
        "benchmark_requests": 20,
        "result_limit": 10,
        "retry_attempts": 0,
        "max_consecutive_failures": 5,
        "http_method": "POST",
        "max_response_bytes": 2_000_000,
        "max_stored_payload_chars": 20_000,
    },
    "request_template": {
        "query_field": "query",
        "limit_field": "limit",
        "results_path": "results",
        "id_field": "song_id",
        "name_field": "song_name",
        "extra_body": {},
        "headers": {},
    },
    "throughput": {"requests": 50},
    "concurrency": {"levels": [1, 3, 5, 10], "rounds_per_level": 2, "max_concurrency": 20},
    "quality": {
        "k": 10,
        "relevance_threshold": 1,
        "precision_denominator": "k",
        "ndcg_gain": "exponential",
    },
    "statistics": {"p99_min_samples": 50},
    "security": {
        "allowed_networks": ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "allowed_hosts": ["localhost"],
        "blocked_ports": [22, 23, 25, 53, 135, 139, 445, 1433, 3306, 3389, 5432, 6379, 8501, 9200, 11211, 27017],
        "follow_redirects": False,
    },
    "scoring": {
        "weights": {
            "functional": 30,
            "performance": 20,
            "reliability": 15,
            "scalability": 10,
            "cost": 10,
            "architecture": 15,
        },
        "functional": {"precision_weight": 0.4, "ndcg_weight": 0.6},
        "performance": {"target_p95_ms": 500, "apply_success_rate": True},
        "scalability": {
            "target_p95_ms": 1000,
            "target_throughput_rps": 5,
            "latency_weight": 0.4,
            "throughput_weight": 0.3,
            "error_weight": 0.3,
        },
        "cost": {"target_cost_per_1k_usd": 1.0, "missing_data_points": 0},
    },
}

# Architecture review checklist (category -> maximum points). Total = 15.
ARCHITECTURE_CHECKLIST: dict[str, int] = {
    "Architecture design": 3,
    "API design": 2,
    "Database design": 2,
    "Security practices": 2,
    "Logging and observability": 2,
    "Documentation": 2,
    "Maintainability": 2,
}


def deep_merge(base: dict, override: dict) -> dict:
    """Return a copy of ``base`` with ``override`` merged in recursively."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    if path.exists():
        with path.open("r", encoding="utf-8") as fh:
            user_settings = json.load(fh)
    else:
        user_settings = {}
    return deep_merge(DEFAULT_SETTINGS, user_settings)


def validate_settings(settings: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems with the settings (empty = valid)."""
    problems: list[str] = []
    req = settings.get("request", {})
    if not (0 < float(req.get("timeout_seconds", 0)) <= 300):
        problems.append("request.timeout_seconds must be between 0 and 300.")
    for key in ("warmup_requests", "benchmark_requests", "retry_attempts", "max_consecutive_failures"):
        if int(req.get(key, -1)) < 0:
            problems.append(f"request.{key} must be >= 0.")
    if int(req.get("result_limit", 0)) < 1:
        problems.append("request.result_limit must be >= 1.")
    if str(req.get("http_method", "")).upper() not in ("GET", "POST"):
        problems.append("request.http_method must be GET or POST.")

    conc = settings.get("concurrency", {})
    levels = conc.get("levels", [])
    max_conc = int(conc.get("max_concurrency", 0))
    if not levels or any(int(level) < 1 for level in levels):
        problems.append("concurrency.levels must be a non-empty list of positive integers.")
    if max_conc < 1 or max_conc > 100:
        problems.append("concurrency.max_concurrency must be between 1 and 100.")
    elif any(int(level) > max_conc for level in levels):
        problems.append("Every concurrency level must be <= concurrency.max_concurrency.")

    quality = settings.get("quality", {})
    if int(quality.get("k", 0)) < 1:
        problems.append("quality.k must be >= 1.")
    if quality.get("precision_denominator") not in ("k", "min_k_relevant"):
        problems.append("quality.precision_denominator must be 'k' or 'min_k_relevant'.")
    if quality.get("ndcg_gain") not in ("exponential", "linear"):
        problems.append("quality.ndcg_gain must be 'exponential' or 'linear'.")

    scoring = settings.get("scoring", {})
    weights = scoring.get("weights", {})
    if any(float(v) < 0 for v in weights.values()):
        problems.append("Scoring weights must be >= 0.")
    total = sum(float(v) for v in weights.values())
    if abs(total - 100) > 1e-6:
        problems.append(f"Scoring weights add up to {total:g}, expected 100.")
    if float(scoring.get("performance", {}).get("target_p95_ms", 0)) <= 0:
        problems.append("scoring.performance.target_p95_ms must be > 0.")
    if float(scoring.get("scalability", {}).get("target_p95_ms", 0)) <= 0:
        problems.append("scoring.scalability.target_p95_ms must be > 0.")
    if float(scoring.get("scalability", {}).get("target_throughput_rps", 0)) <= 0:
        problems.append("scoring.scalability.target_throughput_rps must be > 0.")
    if float(scoring.get("cost", {}).get("target_cost_per_1k_usd", -1)) < 0:
        problems.append("scoring.cost.target_cost_per_1k_usd must be >= 0.")
    return problems


def save_settings(settings: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    problems = validate_settings(settings)
    if problems:
        raise ValueError("Invalid settings: " + " ".join(problems))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(settings, fh, indent=2)
    tmp.replace(path)


def load_reliability_tests(path: Path = RELIABILITY_TESTS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        tests = json.load(fh)
    if not isinstance(tests, list):
        raise ValueError("reliability_tests.json must contain a JSON list.")
    return tests


def save_reliability_tests(tests: list[dict[str, Any]], path: Path = RELIABILITY_TESTS_PATH) -> None:
    valid_types = {"format_check", "payload", "raw_body", "path_suffix", "duplicate"}
    seen: set[str] = set()
    for test in tests:
        if not test.get("id") or test["id"] in seen:
            raise ValueError("Every reliability test needs a unique 'id'.")
        seen.add(test["id"])
        if test.get("type") not in valid_types:
            raise ValueError(f"Test {test['id']}: type must be one of {sorted(valid_types)}.")
        if not isinstance(test.get("accepted_status"), list) or not test["accepted_status"]:
            raise ValueError(f"Test {test['id']}: accepted_status must be a non-empty list.")
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(tests, fh, indent=2)
    tmp.replace(path)
