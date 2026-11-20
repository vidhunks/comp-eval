"""Register the four mock teams and import the sample reference dataset.

    python tools/seed_demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import team_manager, test_manager  # noqa: E402
from backend.config import load_settings  # noqa: E402
from backend.database import init_db  # noqa: E402

TEAMS = [("Demo Good", 9001, "POST"), ("Demo Noisy", 9002, "GET"), ("Demo Slow", 9003, "POST"),
         ("Demo Broken", 9004, "POST")]


def main() -> None:
    init_db()
    security = load_settings()["security"]
    existing = {t["team_name"] for t in team_manager.list_teams()}
    for name, port, method in TEAMS:
        if name in existing:
            print(f"skip   {name} (already registered)")
            continue
        team_manager.add_team(name, f"http://127.0.0.1:{port}/search", method, {}, True, security)
        print(f"added  {name}")
    cases = json.loads((ROOT / "data" / "test_cases.json").read_text(encoding="utf-8"))
    count, errors = test_manager.import_test_cases(cases)
    print(f"imported {count} test cases" if not errors else "\n".join(errors))


if __name__ == "__main__":
    main()
