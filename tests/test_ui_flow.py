import json
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from backend import evaluation_runner, results_service, team_manager, test_manager
from mock_participant import create_app
from tests.conftest import ServerThread, free_port

ROOT = Path(__file__).resolve().parent.parent


def page(name):
    return AppTest.from_file(str(ROOT / "frontend" / f"{name}.py"), default_timeout=30)


def test_add_team_and_run_from_ui(temp_db, settings, monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(evaluation_runner, "load_settings", lambda: settings)
    test_manager.import_test_cases(json.loads((ROOT / "data" / "test_cases.json").read_text()))

    with ServerThread(create_app("good"), free_port()) as srv:
        at = page("teams")
        at.run()
        at.text_input[0].input("UI Team")
        at.text_input[1].input(srv.url + "/search")
        at.text_area[1].input('{"X-Api-Key": "super-secret"}')
        at.button[0].click().run()
        assert not at.exception
        teams = team_manager.list_teams()
        assert [t["team_name"] for t in teams] == ["UI Team"]
        assert teams[0]["request_config"]["headers"] == {"X-Api-Key": "super-secret"}
        # header values must never be rendered
        at = page("teams")
        at.run()
        rendered = json.dumps([str(df.value) for df in at.dataframe]) + " ".join(c.value for c in at.caption)
        assert "super-secret" not in rendered

        # a URL outside the allowed networks is rejected
        at.text_input[0].input("Evil")
        at.text_input[1].input("http://8.8.8.8/search")
        at.button[0].click().run()
        assert any("rejected" in e.value for e in at.error)

        at = page("evaluation")
        at.run()
        run_button = next(b for b in at.button if "complete evaluation" in b.label)
        run_button.click().run()
        assert not at.exception
        deadline = time.time() + 60
        while evaluation_runner.is_running():
            assert time.time() < deadline
            time.sleep(0.2)

    scores = results_service.scores_frame(results_service.latest_scored_run_id())
    assert scores.iloc[0]["team_name"] == "UI Team"
    assert scores.iloc[0]["functional_score"] > 0
