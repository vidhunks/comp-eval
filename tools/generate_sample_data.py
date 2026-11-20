"""Generate data/test_cases.json (sample reference dataset matching the mock catalogue).

    python tools/generate_sample_data.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_catalog import CATALOG, ideal_ranking  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "test_cases.json"


def main() -> None:
    cases = []
    # Every third song gives 12 functional test cases spread across all genres.
    for n, song in enumerate(CATALOG[::3], start=1):
        expected = [{"song_id": s["song_id"], "song_name": s["song_name"], "relevance": g}
                    for s, g in ideal_ranking(song)[:15]]
        cases.append({"test_id": f"TEST{n:03d}", "query": song["song_name"],
                      "expected_results": expected, "category": "functional"})
    cases.append({"test_id": "PERF001", "query": CATALOG[1]["song_name"], "expected_results": [],
                  "category": "performance"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cases, indent=2), encoding="utf-8")
    print(f"Wrote {len(cases)} test cases to {OUT}")


if __name__ == "__main__":
    main()
