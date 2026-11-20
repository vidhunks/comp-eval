# SRS v1.0 – Requirements check

Status: ✅ met and covered by automated tests · ✔️ met, checked by code review or a manual run
· ⚠️ met, but with an interpretation or deviation to review · ⬜ not verifiable on the dev machine

Test suite: `python -m pytest`. 45 tests pass.

## Acceptance criteria (SRS §14)

| ID | Criterion | Status | Evidence |
|---|---|---|---|
| AC01 | Register at least two teams | ✅ | `test_end_to_end::test_full_evaluation` (4 teams), `test_ui_flow` (via the form) |
| AC02 | Configure participant endpoints | ✅ | Teams page edit; `test_requirements::test_team_edit_keeps_hidden_headers_and_delete_keeps_history` |
| AC03 | Send requests to all registered teams | ✅ | `test_full_evaluation`. Inactive teams are excluded: `test_inactive_team_not_evaluated` |
| AC04 | Measure HTTP response latency correctly | ✅ | `time.perf_counter()` around send → full body received (`request_executor._send_once`); `test_executor` |
| AC05 | Warm-up excluded from benchmark | ✅ | Stored as `phase='warmup'`; `test_full_evaluation` asserts avg latency = mean of benchmark rows only |
| AC06 | Average, p50 and p95 latency | ✅ | `latency_evaluator.latency_stats`; `test_percentiles` (also min/max/p99) |
| AC07 | Sequential throughput | ✅ | `throughput_evaluator`; dry run: good mock 398 req/s, slow mock 2.1 req/s |
| AC08 | Basic concurrency tests | ✅ | Levels 1/3/5/10 with asyncio + `httpx.AsyncClient`; cap enforced (`test_preflight_blocks_bad_runs`) |
| AC09 | Compare responses with reference results | ✅ | `quality_evaluator.compute_quality_metrics` |
| AC10 | Precision@K and NDCG@K | ✅ | `test_metrics_and_scoring` (hand-calculated NDCG), plus Recall@K |
| AC11 | Identify failed and timed-out requests | ✅ | `test_outcome_classification`, `test_connection_error`, `test_response_size_cap` |
| AC12 | Reliability scores | ✅ | `reliability_evaluator`; good mock 7/7, broken mock 2/7 |
| AC13 | Enter architecture review scores | ✅ | Architecture & cost page; `test_review_validation`, `test_leaderboard_recompute_button` |
| AC14 | Final score out of 100 | ✅ | `test_score_team_formulas`; weights must total 100 (`test_settings_validation`) |
| AC15 | Dashboard shows rankings | ✅ | Leaderboard page; `test_pages` renders every page |
| AC16 | Results stored in SQLite | ✅ | `data/competition.db`; all phases stored per run |
| AC17 | Export results as CSV | ✅ | `results_service.export_run_csv`; `test_full_evaluation` re-reads the CSV |
| AC18 | Runs on a laptop without cloud | ✔️ | Local SQLite + Streamlit; no external services or LLM |

## Functional modules

| SRS | Requirement | Status | Notes |
|---|---|---|---|
| §3 M1 | Add / edit / delete / view teams, endpoint, method, request config, active flag | ✅ | Deleting a team keeps its historical results |
| §3 M2 | Add / edit / delete test cases, JSON & CSV upload, categories, graded expected results | ✅ | `test_csv_import_formats`, `test_cost_csv_import_and_test_case_crud` |
| §3 M3 | Active teams, construct request, record response, handle errors, continue on failure | ✅ | Unreachable team does not stop the run (`test_full_evaluation`, "Down" team) |
| §3 M3 | Configurable timeout 15s, warm-up 3, benchmark 20, limit 10, retries 0, POST | ✅ | `config/settings.json` defaults match the SRS exactly |
| §3 M3 | Distinguish HTTP error / connection error / timeout / invalid JSON / success | ✅ | Plus `invalid_response`, `too_large`, `blocked` |
| §4.1 | perf_counter, ms, status codes, same payloads, failures recorded separately | ✅ | Queries cycled in a fixed order, so every team gets identical requests |
| §4.1 | p99 only with sufficient samples | ✅ | `statistics.p99_min_samples` (default 50) |
| §4.1 | Do not claim backend time | ✔️ | Labelled "end-to-end HTTP round-trip" on the Results and Evaluation pages |
| §4.2 | Sequential throughput formula | ✅ | |
| §4.3 | Concurrency levels configurable, bounded | ✅ | `max_concurrency` safety cap |
| §5 | IDs first, names only if no ID, duplicates, missing, empty | ✅ | Tests for each case |
| §5 | No external LLM | ✔️ | |
| §6 | Empty query, missing field, invalid JSON, very long query, duplicate query, response format | ✅ | `config/reliability_tests.json`, with predefined accepted status codes |
| §6 | "Invalid endpoint → connection failure recorded", "Timeout → timeout recorded" | ⚠️ | These describe what the **evaluator** records, and it does (`connection_error`, `timeout` outcomes, tested). For the participant's *score*, an "unknown path → 404/405" test is used, and any timeout or connection failure during a reliability test counts as a fail. |
| §7 | Cost fields, manual entry, CSV import, no automatic measurement | ✅ | Missing cost data → 0 points by default (configurable) |
| §8 | Checklist 3+2+2+2+2+2+2 = 15 with comments | ✅ | |
| §9 | Category maxima 30/20/15/10/10/15, configurable, transparent | ✅ | Each score stores its formula trace and inputs |
| §9 | Functional = (0.4 P@10 + 0.6 NDCG@10) × 30 | ✅ | |
| §9 | Performance = 20 × min(1, target / p95) | ⚠️ | By default this is **also multiplied by the benchmark success rate**, so a team failing most requests can't get full points. Switch off "multiply by benchmark success rate" in Settings for the exact SRS formula. |
| §9 | Precision@K = relevant in top K / K | ⚠️ | Implemented exactly as written. Side effect: a query with fewer than K relevant references cannot reach 1.0. The optional `min_k_relevant` setting avoids this. |
| §10.1–10.6 | Home, Teams (incl. connectivity test), Test cases, Evaluation (7 actions, progress, errors, single-run lock), Results (metrics table + 4 comparison charts), Leaderboard with breakdown | ✅ | Plus a concurrency chart, request-level inspection, and run cancellation |
| §11 | Six tables with all listed columns | ✅ | Plus `Team_Costs` and extra traceability columns |

## Non-functional and security (§12)

| # | Requirement | Status | Notes |
|---|---|---|---|
| 1–3 | Local, no LLM, SQLite | ✔️ | |
| 4–6 | Configurable participants, test cases, timeouts | ✅ | |
| 7 | Endpoint failures don't crash | ✅ | Per-phase error isolation plus a circuit breaker for dead endpoints |
| 8, 12 | Store all measurements, preserve history | ✅ | Every row is linked to a run; run snapshot of settings, teams and test-case fingerprint |
| 9 | Clear error messages | ✔️ | Errors shown live on the Evaluation page and stored with the run |
| 10 | No unrestricted concurrency | ✅ | |
| 11 | CSV export | ✅ | |
| 13 | Repeatable scoring | ✅ | Recompute from stored rows without re-measuring (`test_full_evaluation`, `test_leaderboard_recompute_button`) |
| Sec | Approved endpoints only / allowlisted local networks / URL validation | ✅ | `test_url_validation` (public IPs, cloud metadata, DB ports, embedded credentials rejected) |
| Sec | Don't expose API keys in dashboard or logs | ✅ | Per-team header values are never displayed and never stored in run snapshots (tested). Exception: headers placed in the *global* request template are visible in the raw Settings JSON editor, so keep secrets in per-team headers. |
| Sec | Don't send secrets unnecessarily | ✔️ | A team's headers are only sent to that team. Redirects are not followed, and proxy env vars are ignored. |

## Hardware target (§2.1)

| Target | Measured |
|---|---|
| 4 GB RAM | Peak 128 MB for the evaluator process during a full 4-team run (544 requests) with the dashboard libraries loaded |
| ~500 MB storage | 431 MB installed packages + 0.6 MB project; database ≈ 0.5 MB per full 4-team run |
| 2 CPU cores | ⬜ Dev machine has 16 cores. The work is I/O-bound with bounded concurrency, so 2 cores should be enough; confirm on the competition laptop. |

## Implementation instructions (§16)

No microservices or Kubernetes ✔️ · no local LLM ✔️ · all configuration editable (Settings page, raw JSON,
reliability suite editor) ✔️ · scoring separate from frontend (`backend/scoring_engine.py`, pure functions) ✔️ ·
HTTP execution separate from scoring (`backend/request_executor.py`) ✔️ · every measurement traceable to a
request row ✔️ · monotonic clock ✔️ · reproducible benchmark (fixed query order, sequential teams, no retries) ✔️

## Not yet checked

- Visual layout in a real browser (pages are render-tested only, via Streamlit's test harness).
- Behaviour on a 2-core laptop and over real Wi-Fi with other people's machines.
