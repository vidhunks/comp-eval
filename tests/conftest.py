from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from backend import database  # noqa: E402
from backend.config import load_settings  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 10
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture
def temp_db(tmp_path):
    original = database.DB_PATH
    database.set_db_path(tmp_path / "test.db")
    database.init_db()
    yield tmp_path / "test.db"
    database.set_db_path(original)


@pytest.fixture
def settings():
    s = load_settings()
    s["request"].update(timeout_seconds=2, warmup_requests=2, benchmark_requests=6)
    s["throughput"]["requests"] = 6
    s["concurrency"].update(levels=[1, 3], rounds_per_level=1)
    return s


def run(coro):
    return asyncio.run(coro)
