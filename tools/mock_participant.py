"""Mock participant application for dry runs and tests.

Usage:
    python tools/mock_participant.py --mode good   --port 9001
    python tools/mock_participant.py --mode noisy  --port 9002
    python tools/mock_participant.py --mode slow   --port 9003
    python tools/mock_participant.py --mode broken --port 9004

Modes:
    good    correct rankings, fast, proper validation (422 on bad input)
    noisy   partially correct rankings, 30-120 ms latency
    slow    correct rankings, 300-600 ms latency, handles one request at a time
    broken  wrong response format on some queries, 500s on bad input, ~15% random failures
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_catalog import CATALOG, find_song, ideal_ranking  # noqa: E402


def _rng(query: str, salt: str = "") -> random.Random:
    seed = int(hashlib.sha256((salt + query).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def create_app(mode: str) -> FastAPI:
    app = FastAPI(title=f"Mock participant ({mode})")
    serial_lock = asyncio.Lock()
    counter = {"n": 0}

    def rank(query: str, limit: int) -> list[dict]:
        song = find_song(query)
        if song is None:
            return []
        ranked = [s for s, _ in ideal_ranking(song)]
        if mode == "noisy":
            rng = _rng(query)
            # swap a few positions and replace some results with unrelated songs
            for _ in range(3):
                i, j = rng.randrange(min(limit, len(ranked))), rng.randrange(min(limit, len(ranked)))
                ranked[i], ranked[j] = ranked[j], ranked[i]
            unrelated = [s for s in CATALOG if s["genre"] != song["genre"] and s not in ranked[:limit]]
            for pos in (2, 5, 7):
                if pos < len(ranked) and unrelated:
                    ranked[pos] = unrelated[rng.randrange(len(unrelated))]
        return [{"song_id": s["song_id"], "song_name": s["song_name"]} for s in ranked[:limit]]

    async def handle(query, limit):
        if mode == "broken":
            counter["n"] += 1
            if query is None or query == "":
                raise HTTPException(status_code=500, detail="internal error")
            if random.random() < 0.15:
                raise HTTPException(status_code=503, detail="temporarily unavailable")
        else:
            if query is None or not isinstance(query, str) or not query.strip():
                return JSONResponse(status_code=422, content={"detail": "query must be a non-empty string"})
            if len(query) > 2000:
                return JSONResponse(status_code=413, content={"detail": "query too long"})
        try:
            limit = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            return JSONResponse(status_code=422, content={"detail": "limit must be an integer"})

        if mode == "noisy":
            await asyncio.sleep(_rng(query, "lat").uniform(0.03, 0.12))
        elif mode == "slow":
            async with serial_lock:
                await asyncio.sleep(_rng(query, "lat").uniform(0.3, 0.6))
        elif mode == "broken":
            await asyncio.sleep(random.uniform(0.01, 0.2))

        results = rank(query, limit)
        if mode == "broken" and _rng(query, "fmt").random() < 0.3:
            return {"songs": results}
        return {"results": results}

    @app.post("/search")
    async def search_post(request: Request):
        try:
            body = await request.json()
        except Exception:
            if mode == "broken":
                raise HTTPException(status_code=500, detail="crash")
            return JSONResponse(status_code=400, content={"detail": "invalid JSON"})
        if not isinstance(body, dict):
            return JSONResponse(status_code=422, content={"detail": "body must be an object"})
        return await handle(body.get("query"), body.get("limit", 10))

    @app.get("/search")
    async def search_get(query: str | None = None, limit: int = 10):
        return await handle(query, limit)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": mode}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["good", "noisy", "slow", "broken"], default="good")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()
    uvicorn.run(create_app(args.mode), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
