"""Checks for SRS requirements not already covered by the other test modules."""

import io
import json
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi import FastAPI
from streamlit.testing.v1 import AppTest

from backend import evaluation_runner, results_service, review_manager, team_manager, test_manager
from backend.config import ARCHITECTURE_CHECKLIST, save_settings
from backend.database import get_connection
from backend.request_executor import build_request, effective_request_config, execute_request, make_client
from mock_participant import create_app
from tests.conftest import ServerThread, free_port, run

ROOT = Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "data" / "test_cases.json").read_text())


def wait_for_run(timeout=120):
    deadline = time.time() + timeout
    while evaluation_runner.is_running():
        assert time.time() < deadline
        time.sleep(0.1)


def test_team_edit_keeps_hidden_headers_and_delete_keeps_history(temp_db, settings, monkeypatch):
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    test_manager.import_test_cases(CASES)
    sec = settings["security"]
    with ServerThread(create_app("good"), free_port()) as srv:
        tid = team_manager.add_team("A", srv.url + "/search", "POST", {"headers": {"Authorization": "SECRET-XYZ"}}, True, sec)
        # editing without re-entering headers keeps them (participant IP change scenario)
        team_manager.update_team(tid, "A", srv.url.replace("127.0.0.1", "localhost") + "/search", "POST", "{}", True, sec)
        assert team_manager.get_team(tid)["request_config"]["headers"] == {"Authorization": "SECRET-XYZ"}
        assert team_manager.list_teams_for_display()[0]["request_config"]["headers"] == {"Authorization": "****"}
        run_id = evaluation_runner.start_run(["benchmark"])
        wait_for_run()
    snapshot = json.dumps(results_service.get_run(run_id)["configuration"])
    assert "SECRET-XYZ" not in snapshot  # header value never stored in the run snapshot
    team_manager.delete_team(tid)
    assert team_manager.list_teams() == []
    scores = results_service.scores_frame(run_id)
    assert scores.iloc[0]["team_name"] == "A"  # history and names preserved after deletion
    assert len(results_service.results_frame(run_id)) > 0


def test_inactive_team_not_evaluated(temp_db, settings, monkeypatch):
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    test_manager.import_test_cases(CASES)
    with ServerThread(create_app("good"), free_port()) as srv:
        team_manager.add_team("On", srv.url + "/search", "POST", {}, True, settings["security"])
        off = team_manager.add_team("Off", srv.url + "/search", "POST", {}, False, settings["security"])
        run_id = evaluation_runner.start_run(["connectivity"])
        wait_for_run()
    assert off not in set(results_service.results_frame(run_id)["team_id"])


def test_cancel_run(temp_db, settings, monkeypatch):
    settings["request"]["benchmark_requests"] = 500
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    test_manager.import_test_cases(CASES)
    with ServerThread(create_app("noisy"), free_port()) as srv:
        team_manager.add_team("T", srv.url + "/search", "POST", {}, True, settings["security"])
        run_id = evaluation_runner.start_run(["benchmark"])
        time.sleep(1)
        assert evaluation_runner.request_cancel()
        wait_for_run(30)
    run = results_service.get_run(run_id)
    assert run["evaluation_status"] == "cancelled" and run["cancel_requested"] == 1
    assert 0 < len(results_service.results_frame(run_id)) < 500  # partial results kept


def test_interrupted_run_recovered(temp_db):
    with get_connection() as conn:
        conn.execute("INSERT INTO Evaluation_Runs (start_time, evaluation_status, configuration) "
                     "VALUES ('2026-01-01', 'running', '{}')")
    assert evaluation_runner.recover_interrupted_runs() == 1
    assert results_service.list_runs()[0]["evaluation_status"] == "aborted"


def test_preflight_blocks_bad_runs(temp_db, settings, monkeypatch):
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    with pytest.raises(ValueError, match="No active teams"):
        evaluation_runner.start_run(["benchmark"])
    settings["concurrency"]["levels"] = [1, 50]
    team_manager.add_team("T", "http://127.0.0.1:9/search", "POST", {}, True, settings["security"])
    test_manager.import_test_cases(CASES)
    with pytest.raises(ValueError, match="max_concurrency"):
        evaluation_runner.start_run(["concurrency"])
    assert not evaluation_runner.is_running()


def test_settings_validation(tmp_path, settings):
    bad = json.loads(json.dumps(settings))
    bad["scoring"]["weights"]["functional"] = 50
    with pytest.raises(ValueError, match="add up to"):
        save_settings(bad, tmp_path / "s.json")
    save_settings(settings, tmp_path / "s.json")


def test_cost_csv_import_and_test_case_crud(temp_db, settings):
    tid = team_manager.add_team("Team A", "http://127.0.0.1:9001/search", "POST", {}, True, settings["security"])
    csv = ("team_name,ai_model,embedding_model,paid_api_usage,api_cost_per_1k_usd,infra_cost_per_1k_usd,"
           "uses_local_models,notes\nTeam A,gpt,mini-emb,yes,0.4,0.1,no,n\nNobody,x,y,no,1,1,no,\n")
    count, errors = team_manager.import_costs_csv(pd.read_csv(io.StringIO(csv)), {"Team A": tid})
    assert count == 1 and len(errors) == 1
    cost = team_manager.get_costs()[tid]
    assert cost["paid_api_usage"] == 1 and cost["api_cost_per_1k_usd"] == 0.4

    test_manager.add_test_case({"test_id": "X1", "query": "q", "expected_results": [{"song_id": "1", "relevance": 2}]})
    with pytest.raises(ValueError):
        test_manager.add_test_case({"test_id": "X1", "query": "q"})
    test_manager.upsert_test_case({"test_id": "X1", "query": "q2", "category": "edge_case", "expected_results": []})
    assert test_manager.get_test_case("X1")["query"] == "q2"
    test_manager.delete_test_case("X1")
    assert test_manager.get_test_case("X1") is None


def test_review_validation(temp_db):
    with pytest.raises(ValueError):
        review_manager.save_review(1, {"Architecture design": (4, "")})  # max 3
    review_manager.save_review(1, {c: (m, "") for c, m in ARCHITECTURE_CHECKLIST.items()})
    assert review_manager.current_review_points(1) == ARCHITECTURE_CHECKLIST


def test_response_size_cap(settings):
    app = FastAPI()

    @app.post("/big")
    async def big():
        return {"results": [{"song_id": str(i), "song_name": "x" * 100} for i in range(50_000)]}

    settings["request"]["max_response_bytes"] = 100_000
    with ServerThread(app, free_port()) as srv:
        team = {"team_id": 1, "endpoint_url": srv.url + "/big", "http_method": "POST", "request_config": {}}

        async def go():
            async with make_client(settings) as client:
                return await execute_request(client, build_request(team, settings, "q"), settings,
                                             effective_request_config(team, settings))
        assert run(go()).outcome == "too_large"


def test_leaderboard_recompute_button(temp_db, settings, monkeypatch):
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    monkeypatch.chdir(ROOT)
    test_manager.import_test_cases(CASES)
    with ServerThread(create_app("good"), free_port()) as srv:
        tid = team_manager.add_team("T", srv.url + "/search", "POST", {}, True, settings["security"])
        run_id = evaluation_runner.start_run(["functional"])
        wait_for_run()
    assert results_service.scores_frame(run_id).iloc[0]["architecture_score"] == 0
    review_manager.save_review(tid, {c: (m, "") for c, m in ARCHITECTURE_CHECKLIST.items()})
    at = AppTest.from_file(str(ROOT / "frontend" / "leaderboard.py"), default_timeout=30)
    at.run()
    next(b for b in at.button if b.label == "Recompute scores").click().run()
    assert not at.exception
    assert results_service.scores_frame(run_id).iloc[0]["architecture_score"] == 15
