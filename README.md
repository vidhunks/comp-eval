# Competition Evaluator

A lightweight, local web application that registers competition teams, sends
standardised requests to their HTTP endpoints, measures latency, throughput and
concurrency behaviour, evaluates result quality against a graded reference
dataset, tests reliability, calculates scores, and shows a leaderboard.

Runs entirely on one laptop: Python, Streamlit, SQLite. No cloud services, no LLM.

## Quick start

Requires **Python 3.10 or newer** (Windows, macOS or Linux).

```bash
git clone <repo-url>
cd competition_evaluator
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501. The database (`data/competition.db`) is created on first start.

### Dry run with mock teams

```bash
python tools/run_mock_teams.py    # 4 mock participants on ports 9001-9004 (keep running)
python tools/seed_demo.py         # registers them + imports data/test_cases.json
streamlit run app.py              # Run evaluation -> Run complete evaluation
```

Mock modes: `good` (correct and fast), `noisy` (partly wrong rankings, 30–120 ms, GET API),
`slow` (correct, 300–600 ms, one request at a time), `broken` (wrong format on some
queries, 5xx on bad input, random failures). To keep demo data out of the real database,
set `EVALUATOR_DB` to another file path before running the seed script and Streamlit.

### Tests

```bash
python -m pytest
```

There are 45 tests (see `docs/REQUIREMENTS_CHECK.md` for how they map to the SRS). They cover the metric formulas, scoring, URL validation, outcome
classification (timeout, connection error, invalid JSON, invalid format, HTTP error,
redirect), a full end-to-end run against live mock servers, every dashboard page
(empty and populated), and a UI flow that adds a team and starts a run.

## Workflow on competition day

1. **Settings**: confirm the timeout, request counts, target p95 latency, cost target and
   weights *before* the competition. Each run stores a snapshot of its settings.
2. **Test cases**: upload the reference dataset (JSON or CSV, see below).
3. **Teams**: register each team's endpoint URL and method, then use *Test connectivity*.
4. **Run evaluation**: *Run complete evaluation* (or individual phases). Progress, errors
   and a log are shown live. Only one run can execute at a time, and it can be cancelled.
5. **Architecture & cost**: enter checklist review scores and participant cost estimates.
6. **Leaderboard**: click *Recompute scores* to apply reviews and costs entered after the
   run. The breakdown shows the formula and inputs for every category.
7. **Results**: view metrics, charts and every individual request, and export CSV files to `exports/`.

## Participant API contract (default)

```
POST <endpoint>   {"query": "Song A", "limit": 10}
GET  <endpoint>?query=Song+A&limit=10
->   {"results": [{"song_id": "101", "song_name": "Song B"}, ...]}
```

Each team can override the defaults with its *request configuration* JSON:
`query_field`, `limit_field`, `results_path` (dotted, e.g. `data.items`), `id_field`,
`name_field`, `extra_body`. If a team's API requires headers (e.g. an API key), they are
entered separately. Header values are never displayed and never stored with results or
run snapshots.

To use the platform for a different problem statement, change the field names in the
request template (Settings → raw JSON) and upload a reference dataset whose
`expected_results` use the new ID field.

## Reference dataset format

JSON:
```json
[{"test_id": "TEST001", "query": "Song A", "category": "functional",
  "expected_results": [{"song_id": "101", "relevance": 3}, {"song_id": "102", "relevance": 2}]}]
```
CSV, one row per expected result: `test_id,query,category,song_id,relevance[,song_name]`.
CSV, one row per test case: `test_id,query,category,expected_results`, where
`expected_results` is `101:3;102:2` or a JSON list.

Grades: 0 = not relevant, 1 = somewhat relevant, 2 = relevant, 3 = highly relevant. A
result counts as relevant when its grade is at least `quality.relevance_threshold`
(default 1). Test cases without relevant expected results (e.g. category `performance`)
are only used as benchmark queries. An upload is validated in full first: if any row is
invalid, nothing is imported.

## How things are measured

| What | How |
|---|---|
| Latency | `time.perf_counter()` from just before sending until the full response body is received (JSON parsing excluded). This is end-to-end HTTP round-trip time from the evaluator, **not** backend processing time. |
| Warm-up | `request.warmup_requests` requests always run before the benchmark on the same connection pool. They are stored with `phase = warmup` and never used in scores. |
| Benchmark | `request.benchmark_requests` sequential requests, cycling through the test-case queries in a fixed order (the same sequence for every team). Reports avg/min/max/p50/p95, plus p99 once there are at least `statistics.p99_min_samples` successful samples. Percentiles use linear interpolation. |
| Throughput | `throughput.requests` sequential requests. Throughput = successful requests ÷ total wall-clock seconds. |
| Concurrency | For each level L (default 1, 3, 5, 10): L × `rounds_per_level` requests with at most L in flight (`asyncio` semaphore + `httpx.AsyncClient` pool). Levels above `max_concurrency` are refused. |
| Functional | Precision@K, Recall@K and NDCG@K for each test case, then averaged. Matching uses IDs (compared as strings). Names are normalised and matched only when a returned item has no ID. Duplicates score zero after their first occurrence, and failed or empty responses score 0. |
| Reliability | Suite in `config/reliability_tests.json` (editable on the Test cases page): empty query, missing field, invalid JSON, 10,000-character query, unknown path, duplicate-query consistency, and response format. Each test has predefined acceptable status codes. Timeouts, connection failures and 5xx responses always fail. Raw-body tests are skipped for GET endpoints. |

Outcomes recorded per request: `success`, `http_error`, `connection_error`, `timeout`,
`invalid_json`, `invalid_response`, `too_large`, `blocked`, `error`, with the status code,
latency, attempt count, error message and (truncated) payload.

Fairness and robustness:
- Teams are evaluated **one at a time**, so they never compete for the evaluator's network.
- Results are written to SQLite only between phases, so database I/O never runs while a
  request is being timed.
- Retries are off by default (`retry_attempts = 0`) so they cannot distort latency.
- After `max_consecutive_failures` (default 5) connection errors or timeouts in a row, the
  team is marked unreachable and its remaining phases are skipped. One dead endpoint
  cannot stall the run, and the run continues with the next team.

## Scoring (defaults; all configurable on the Settings page)

| Category | Max | Formula |
|---|---|---|
| Functional | 30 | 30 × (0.4 × mean P@10 + 0.6 × mean NDCG@10) |
| Performance | 20 | 20 × min(1, target p95 ÷ actual p95) × benchmark success rate |
| Reliability | 15 | 15 × passed ÷ total |
| Scalability | 10 | 10 × mean over levels of (0.4 × min(1, target p95 ÷ p95) + 0.3 × min(1, req/s ÷ target req/s) + 0.3 × (1 − error rate)) |
| Cost | 10 | 10 × min(1, target ÷ cost per 1k successful requests), where cost per 1k successful = (API + infra cost per 1k) ÷ success rate. Missing data gets `missing_data_points` (default 0). |
| Architecture | 15 | 15 × checklist points ÷ 15 |

A category that was not evaluated in a run scores 0 and is flagged on the leaderboard.
Scores are stored per run, together with the metrics, review and cost inputs and the
scoring configuration used, so every number can be reproduced. *Recompute scores*
re-derives them from the stored request rows without repeating any measurement.
Tied teams share a rank.

Two defaults worth reviewing before the competition:
- **Precision denominator**: standard Precision@K divides by K. If a query has fewer than
  K relevant references, even a perfect answer scores below 1. Set
  `quality.precision_denominator` to `min_k_relevant` to divide by min(K, number of relevant references).
- **Performance × success rate**: with this on, a team that answers only a few requests
  quickly cannot get full performance points. Turn it off to apply the SRS formula exactly.

## Security

- Endpoints must be `http`/`https`, with no credentials in the URL, a port that is not on
  the blocked list (databases, SSH, SMB, RDP and the evaluator's own port 8501), and a host
  that resolves only to addresses inside `security.allowed_networks` (default loopback and
  private LAN ranges) or is listed in `security.allowed_hosts`. Cloud metadata and public
  addresses are rejected.
- URLs are checked when a team is registered or edited, and again at the start of every run.
- Redirects are not followed, so a participant cannot point the evaluator at another service.
  Proxy environment variables are ignored.
- Response bodies are capped at `request.max_response_bytes`.
- Requests are only sent to registered teams marked active (plus the explicit connectivity test).

## Database

SQLite at `data/competition.db` (WAL mode). Tables follow the SRS: `Teams`, `Test_Cases`,
`Evaluation_Runs`, `Evaluation_Results`, `Team_Scores`, `Architecture_Reviews`, plus
`Team_Costs`. Extra columns support traceability: `phase`, `outcome`, `request_index`,
`concurrency_level`, `attempts`, `details`.

Every result row belongs to a run. Deleting a team keeps its historical results.
A run left `running` by a crashed or restarted app is marked `aborted` on the next start.

## Project layout

```
app.py                     Streamlit entry point (navigation)
backend/
  config.py                settings load/save/validation, architecture checklist
  database.py              SQLite schema and connections
  security.py              endpoint URL allowlist, header masking
  team_manager.py          teams, endpoints, cost data
  test_manager.py          test cases, JSON/CSV import
  request_executor.py      request building, timing, outcome classification
  run_context.py           result buffering, progress, cancel, circuit breaker
  latency_evaluator.py     warm-up, benchmark, percentile statistics
  throughput_evaluator.py  sequential throughput
  concurrency_evaluator.py bounded asyncio concurrency
  quality_evaluator.py     Precision@K, Recall@K, NDCG@K
  reliability_evaluator.py reliability suite
  review_manager.py        architecture reviews
  scoring_engine.py        pure scoring functions with formula traces
  results_service.py       aggregation, score storage, leaderboard, CSV export
  evaluation_runner.py     run orchestration, single-run lock, background thread
frontend/                  Streamlit pages (no scoring or HTTP logic)
config/settings.json       all tunable settings
config/reliability_tests.json
data/test_cases.json       sample reference dataset (matches the mock catalogue)
tools/                     mock participants, demo seeding, sample data generator
tests/                     pytest suite
exports/                   CSV exports
```
