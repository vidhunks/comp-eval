import json
import time
from pathlib import Path

import pandas as pd

from backend import evaluation_runner, results_service, review_manager, team_manager, test_manager
from backend.config import ARCHITECTURE_CHECKLIST
from mock_participant import create_app
from tests.conftest import ServerThread, free_port

DATA = Path(__file__).resolve().parent.parent / "data" / "test_cases.json"


def test_csv_import_formats(temp_db):
    long_csv = b"test_id,query,category,song_id,relevance\nT1,Song A,functional,101,3\nT1,Song A,functional,102,2\n"
    cases = test_manager.parse_csv_upload(long_csv)
    assert cases[0]["expected_results"] == [{"song_id": "101", "relevance": "3"}, {"song_id": "102", "relevance": "2"}]
    compact = b'test_id,query,category,expected_results\nT2,Song B,functional,101:3;102:1\n'
    count, errors = test_manager.import_test_cases(cases + test_manager.parse_csv_upload(compact))
    assert (count, errors) == (2, [])
    assert test_manager.get_test_case("T2")["relevance_grades"] == {"101": 3, "102": 1}
    _, errors = test_manager.import_test_cases([{"test_id": "BAD", "query": "x",
                                                 "expected_results": [{"song_id": "1", "relevance": 7}]}])
    assert errors


def test_full_evaluation(temp_db, settings, monkeypatch):
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    count, errors = test_manager.import_test_cases(json.loads(DATA.read_text()))
    assert not errors and count > 0

    with ServerThread(create_app("good"), free_port()) as good, \
         ServerThread(create_app("noisy"), free_port()) as noisy, \
         ServerThread(create_app("broken"), free_port()) as broken:
        sec = settings["security"]
        ids = {
            "good": team_manager.add_team("Good", good.url + "/search", "POST", {}, True, sec),
            "noisy": team_manager.add_team("Noisy", noisy.url + "/search", "GET", {}, True, sec),
            "broken": team_manager.add_team("Broken", broken.url + "/search", "POST", {}, True, sec),
        }
        # an unreachable team must not stop the run
        ids["down"] = team_manager.add_team("Down", f"http://127.0.0.1:{free_port()}/search", "POST", {}, True, sec)
        review_manager.save_review(ids["good"], {c: (m, "ok") for c, m in ARCHITECTURE_CHECKLIST.items()})
        team_manager.upsert_cost(ids["good"], {"api_cost_per_1k_usd": 0.2, "infra_cost_per_1k_usd": 0})

        run_id = evaluation_runner.start_run(list(evaluation_runner.FULL_EVALUATION))
        try:
            evaluation_runner.start_run(["benchmark"])
            raise AssertionError("second concurrent run must be rejected")
        except RuntimeError:
            pass
        deadline = time.time() + 120
        while evaluation_runner.is_running():
            assert time.time() < deadline, "run did not finish"
            time.sleep(0.2)

    run = results_service.get_run(run_id)
    assert run["evaluation_status"] == "completed_with_errors"  # the 'Down' team logs errors
    assert any("Down" in e for e in run["error_log"])

    raw = results_service.results_frame(run_id)
    good_rows = raw[raw["team_id"] == ids["good"]]
    assert (good_rows["phase"] == "warmup").sum() == settings["request"]["warmup_requests"]
    assert (good_rows["phase"] == "benchmark").sum() == settings["request"]["benchmark_requests"]

    scores = results_service.scores_frame(run_id).set_index("team_name")
    assert scores.loc["Good", "rank"] == 1
    assert scores.loc["Good", "final_score"] > scores.loc["Noisy", "final_score"] > scores.loc["Down", "final_score"]
    assert scores.loc["Good", "reliability_score"] == 15
    assert scores.loc["Good", "architecture_score"] == 15
    assert scores.loc["Broken", "reliability_score"] < 15
    assert scores.loc["Down", "final_score"] == 0

    # warm-up exclusion: benchmark stats come only from benchmark rows
    metrics = results_service.team_metrics_table(run_id).set_index("team_name")
    bench = good_rows[(good_rows["phase"] == "benchmark") & (good_rows["success"] == 1)]["latency_ms"]
    assert metrics.loc["Good", "avg_latency_ms"] == bench.mean()
    assert metrics.loc["Good", "precision_at_k"] > metrics.loc["Noisy", "precision_at_k"]

    paths = results_service.export_run_csv(run_id)
    assert len(pd.read_csv(paths["raw_results"])) == len(raw)
    for p in paths.values():
        Path(p).unlink()

    # rescoring with different weights is reproducible from stored rows
    new_scoring = json.loads(json.dumps(settings["scoring"]))
    new_scoring["weights"].update(functional=50, performance=0)
    results_service.compute_and_store_scores(run_id, new_scoring)
    rescored = results_service.scores_frame(run_id).set_index("team_name")
    assert rescored.loc["Good", "performance_score"] == 0
