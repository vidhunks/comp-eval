import copy
import json

import streamlit as st

from backend import evaluation_runner
from backend.config import load_settings, save_settings, validate_settings

st.title("⚙️ Settings")
st.caption("Stored in config/settings.json. Each run stores a snapshot of the settings it used, "
           "so changing them never alters past results.")

settings = load_settings()
locked = evaluation_runner.is_running()
if locked:
    st.info("Settings are read-only while an evaluation is running.")

problems = validate_settings(settings)
for p in problems:
    st.warning(p)

s = copy.deepcopy(settings)
with st.form("settings_form"):
    st.subheader("Requests")
    r = s["request"]
    c1, c2, c3 = st.columns(3)
    r["timeout_seconds"] = c1.number_input("Request timeout (s)", 0.5, 300.0, float(r["timeout_seconds"]), 0.5)
    r["warmup_requests"] = c2.number_input("Warm-up requests", 0, 100, int(r["warmup_requests"]))
    r["benchmark_requests"] = c3.number_input("Benchmark requests", 1, 1000, int(r["benchmark_requests"]))
    r["result_limit"] = c1.number_input("Result limit", 1, 100, int(r["result_limit"]))
    r["retry_attempts"] = c2.number_input("Retry attempts (connection errors/timeouts)", 0, 5, int(r["retry_attempts"]),
                                          help="Keep 0 for benchmarks: retries distort latency.")
    r["http_method"] = c3.selectbox("Default HTTP method", ["POST", "GET"], index=0 if r["http_method"] == "POST" else 1)
    r["max_consecutive_failures"] = c1.number_input(
        "Mark team unreachable after N consecutive failures", 0, 100, int(r["max_consecutive_failures"]),
        help="Connection errors/timeouts in a row before the team's remaining phases are skipped. 0 disables.")
    s["throughput"]["requests"] = c2.number_input("Throughput requests", 1, 2000, int(s["throughput"]["requests"]))

    st.subheader("Concurrency")
    c1, c2, c3 = st.columns(3)
    levels = c1.text_input("Levels (comma separated)", ", ".join(str(x) for x in s["concurrency"]["levels"]))
    s["concurrency"]["rounds_per_level"] = c2.number_input("Rounds per level", 1, 20,
                                                           int(s["concurrency"]["rounds_per_level"]))
    s["concurrency"]["max_concurrency"] = c3.number_input("Max concurrency (safety cap)", 1, 100,
                                                          int(s["concurrency"]["max_concurrency"]))

    st.subheader("Functional quality")
    q = s["quality"]
    c1, c2, c3, c4 = st.columns(4)
    q["k"] = c1.number_input("K", 1, 100, int(q["k"]))
    q["relevance_threshold"] = c2.number_input("Relevant if grade ≥", 1, 3, int(q["relevance_threshold"]))
    q["precision_denominator"] = c3.selectbox(
        "Precision denominator", ["k", "min_k_relevant"], index=["k", "min_k_relevant"].index(q["precision_denominator"]),
        help="'k' = standard Precision@K. 'min_k_relevant' divides by min(K, number of relevant references), "
             "so a perfect answer scores 1 even when fewer than K references exist.")
    q["ndcg_gain"] = c4.selectbox("NDCG gain", ["exponential", "linear"],
                                  index=["exponential", "linear"].index(q["ndcg_gain"]))

    st.subheader("Scoring weights (must total 100)")
    w = s["scoring"]["weights"]
    cols = st.columns(6)
    for col, key in zip(cols, list(w)):
        w[key] = col.number_input(key.title(), 0.0, 100.0, float(w[key]), 1.0)
    st.caption(f"Current total: {sum(w.values()):g}")

    st.subheader("Scoring parameters")
    sc = s["scoring"]
    c1, c2, c3 = st.columns(3)
    sc["functional"]["precision_weight"] = c1.number_input("Functional: Precision weight", 0.0, 1.0,
                                                           float(sc["functional"]["precision_weight"]), 0.05)
    sc["functional"]["ndcg_weight"] = c1.number_input("Functional: NDCG weight", 0.0, 1.0,
                                                      float(sc["functional"]["ndcg_weight"]), 0.05)
    sc["performance"]["target_p95_ms"] = c2.number_input("Performance: target p95 (ms)", 1.0, 60000.0,
                                                         float(sc["performance"]["target_p95_ms"]), 10.0)
    sc["performance"]["apply_success_rate"] = c2.checkbox(
        "Performance: multiply by benchmark success rate", bool(sc["performance"]["apply_success_rate"]))
    sc["cost"]["target_cost_per_1k_usd"] = c3.number_input("Cost: target USD per 1k successful", 0.0, 1000.0,
                                                           float(sc["cost"]["target_cost_per_1k_usd"]), 0.1)
    sc["cost"]["missing_data_points"] = c3.number_input("Cost: points when no data", 0.0, 100.0,
                                                        float(sc["cost"]["missing_data_points"]), 1.0)
    c1, c2, c3 = st.columns(3)
    sl = sc["scalability"]
    sl["target_p95_ms"] = c1.number_input("Scalability: target p95 (ms)", 1.0, 60000.0, float(sl["target_p95_ms"]), 10.0)
    sl["target_throughput_rps"] = c2.number_input("Scalability: target throughput (req/s)", 0.1, 10000.0,
                                                  float(sl["target_throughput_rps"]), 0.5)
    sl["latency_weight"] = c3.number_input("Scalability: latency weight", 0.0, 1.0, float(sl["latency_weight"]), 0.05)
    sl["throughput_weight"] = c3.number_input("Scalability: throughput weight", 0.0, 1.0,
                                              float(sl["throughput_weight"]), 0.05)
    sl["error_weight"] = c3.number_input("Scalability: error weight", 0.0, 1.0, float(sl["error_weight"]), 0.05)

    st.subheader("Security")
    sec = s["security"]
    nets = st.text_area("Allowed networks (CIDR, one per line)", "\n".join(sec["allowed_networks"]))
    hosts = st.text_area("Allowed host names (one per line)", "\n".join(sec["allowed_hosts"]))
    ports = st.text_input("Blocked ports", ", ".join(str(p) for p in sec["blocked_ports"]),
                          help="8501 is the evaluator's own Streamlit port.")

    if st.form_submit_button("Save settings", type="primary", disabled=locked):
        try:
            s["concurrency"]["levels"] = [int(x) for x in levels.replace(" ", "").split(",") if x]
            sec["allowed_networks"] = [x.strip() for x in nets.splitlines() if x.strip()]
            sec["allowed_hosts"] = [x.strip() for x in hosts.splitlines() if x.strip()]
            sec["blocked_ports"] = [int(x) for x in ports.replace(" ", "").split(",") if x]
            for key in ("warmup_requests", "benchmark_requests", "result_limit", "retry_attempts",
                        "max_consecutive_failures"):
                r[key] = int(r[key])
            save_settings(s)
            st.success("Settings saved.")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))

with st.expander("Advanced: edit raw settings JSON (incl. default request template)"):
    raw = st.text_area("settings.json", json.dumps(settings, indent=2), height=500)
    if st.button("Save raw JSON", disabled=locked):
        try:
            save_settings(json.loads(raw))
            st.success("Saved.")
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))
