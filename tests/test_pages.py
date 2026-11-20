import json
import time
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from backend import evaluation_runner, team_manager, test_manager
from mock_participant import create_app
from tests.conftest import ServerThread, free_port

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["dashboard", "teams", "test_cases", "evaluation", "results", "leaderboard", "reviews", "settings"]


@pytest.fixture(scope="module")
def populated(tmp_path_factory):
    from backend import database
    from tests.conftest import load_settings

    settings = load_settings()
    settings["request"].update(timeout_seconds=2, warmup_requests=2, benchmark_requests=6)
    settings["throughput"]["requests"] = 6
    settings["concurrency"].update(levels=[1, 3], rounds_per_level=1)
    original_db, original_loader = database.DB_PATH, evaluation_runner.load_settings
    database.set_db_path(tmp_path_factory.mktemp("pages") / "pages.db")
    database.init_db()
    evaluation_runner.load_settings = lambda: settings
    test_manager.import_test_cases(json.loads((ROOT / "data" / "test_cases.json").read_text()))
    with ServerThread(create_app("good"), free_port()) as a, ServerThread(create_app("noisy"), free_port()) as b:
        team_manager.add_team("Alpha", a.url + "/search", "POST", {}, True, settings["security"])
        team_manager.add_team("Beta", b.url + "/search", "POST", {}, True, settings["security"])
        evaluation_runner.start_run(list(evaluation_runner.FULL_EVALUATION))
        while evaluation_runner.is_running():
            time.sleep(0.2)
    yield
    database.set_db_path(original_db)
    evaluation_runner.load_settings = original_loader


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(populated, page, monkeypatch):
    monkeypatch.chdir(ROOT)
    at = AppTest.from_file(str(ROOT / "frontend" / f"{page}.py"), default_timeout=30)
    at.run()
    assert not at.exception, [e.message for e in at.exception]


def test_empty_database_pages(temp_db, monkeypatch):
    monkeypatch.chdir(ROOT)
    for page in PAGES:
        at = AppTest.from_file(str(ROOT / "frontend" / f"{page}.py"), default_timeout=30)
        at.run()
        assert not at.exception, (page, [e.message for e in at.exception])
