"""Start four mock participant servers (ports 9001-9004) in one process for a dry run.

    python tools/run_mock_teams.py
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mock_participant import create_app  # noqa: E402

MOCKS = [("good", 9001), ("noisy", 9002), ("slow", 9003), ("broken", 9004)]


def main() -> None:
    servers = []
    for mode, port in MOCKS:
        server = uvicorn.Server(uvicorn.Config(create_app(mode), host="127.0.0.1", port=port, log_level="warning"))
        threading.Thread(target=server.run, daemon=True).start()
        servers.append(server)
        print(f"  {mode:<7} http://127.0.0.1:{port}/search")
    print("Mock teams running. Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        for server in servers:
            server.should_exit = True


if __name__ == "__main__":
    main()
