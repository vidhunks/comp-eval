import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from backend.request_executor import build_request, effective_request_config, execute_request, make_client
from tests.conftest import ServerThread, free_port, run


def make_app():
    app = FastAPI()

    @app.post("/ok")
    async def ok(body: dict):
        return {"results": [{"song_id": "1", "song_name": body.get("query")}]}

    @app.post("/slow")
    async def slow():
        await asyncio.sleep(3)
        return {"results": []}

    @app.post("/badjson")
    async def badjson():
        return PlainTextResponse("not json", media_type="application/json")

    @app.post("/error")
    async def error():
        return JSONResponse(status_code=500, content={"detail": "boom"})

    @app.post("/wrongformat")
    async def wrongformat():
        return {"songs": []}

    @app.post("/redirect")
    async def redirect():
        return RedirectResponse("http://example.com/")

    @app.get("/get")
    async def get(query: str, limit: int):
        return {"results": [{"song_id": query, "song_name": str(limit)}]}

    return app


def call(url, settings, method="POST"):
    team = {"team_id": 1, "team_name": "T", "endpoint_url": url, "http_method": method, "request_config": {}}

    async def go():
        async with make_client(settings) as client:
            spec = build_request(team, settings, "Song A")
            return await execute_request(client, spec, settings, effective_request_config(team, settings))

    return run(go())


def test_outcome_classification(settings):
    with ServerThread(make_app(), free_port()) as srv:
        ok = call(srv.url + "/ok", settings)
        assert ok.outcome == "success" and ok.status_code == 200 and ok.latency_ms > 0
        assert ok.results[0]["song_name"] == "Song A"

        assert call(srv.url + "/slow", settings).outcome == "timeout"
        assert call(srv.url + "/badjson", settings).outcome == "invalid_json"
        err = call(srv.url + "/error", settings)
        assert err.outcome == "http_error" and err.status_code == 500
        assert call(srv.url + "/wrongformat", settings).outcome == "invalid_response"
        redirect = call(srv.url + "/redirect", settings)
        assert redirect.outcome == "http_error" and redirect.status_code == 307

        got = call(srv.url + "/get", settings, method="GET")
        assert got.outcome == "success" and got.results[0] == {"song_id": "Song A", "song_name": "10"}


def test_connection_error(settings):
    out = call(f"http://127.0.0.1:{free_port()}/search", settings)
    assert out.outcome == "connection_error" and not out.success
