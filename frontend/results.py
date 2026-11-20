import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend import results_service
from frontend.common import SERIES, bar_chart, run_selector, style_figure

st.title("📊 Results")
run_id = run_selector("results_run")
if run_id is None:
    st.info("No evaluation runs yet.")
    st.stop()

run = results_service.get_run(run_id)
k = run["configuration"]["settings"]["quality"]["k"]
metrics = results_service.team_metrics_table(run_id)
if metrics.empty:
    st.info("This run has no teams.")
    st.stop()

st.subheader("Team metrics")
st.caption("Latency figures are from the sequential benchmark only (warm-up excluded) and are end-to-end "
           "HTTP round-trip times measured on this machine. Error rate covers benchmark, throughput and "
           "concurrency requests. p99 requires "
           f"{run['configuration']['settings']['statistics']['p99_min_samples']} successful samples.")
num = st.column_config.NumberColumn
st.dataframe(
    metrics.drop(columns=["team_id"]),
    hide_index=True, width="stretch",
    column_config={
        "team_name": "Team",
        "avg_latency_ms": num("Avg latency (ms)", format="%.1f"),
        "p50_latency_ms": num("p50 (ms)", format="%.1f"),
        "p95_latency_ms": num("p95 (ms)", format="%.1f"),
        "p99_latency_ms": num("p99 (ms)", format="%.1f"),
        "min_latency_ms": num("Min (ms)", format="%.1f"),
        "max_latency_ms": num("Max (ms)", format="%.1f"),
        "benchmark_success": "Benchmark OK",
        "throughput_rps": num("Throughput (req/s)", format="%.2f"),
        "error_rate_pct": num("Error rate (%)", format="%.1f"),
        "precision_at_k": num(f"Precision@{k}", format="%.3f"),
        "recall_at_k": num(f"Recall@{k}", format="%.3f"),
        "ndcg_at_k": num(f"NDCG@{k}", format="%.3f"),
        "reliability": "Reliability passed",
        "final_score": num("Final score", format="%.2f"),
    },
)

# ------------------------------------------------------------------ charts
st.subheader("Comparisons")
c1, c2 = st.columns(2)
if metrics["p50_latency_ms"].notna().any():
    c1.plotly_chart(bar_chart(metrics, "team_name", {"p50_latency_ms": "p50", "p95_latency_ms": "p95"},
                              "Latency comparison (lower is better)", "Milliseconds"), width="stretch")
if metrics["throughput_rps"].notna().any():
    c2.plotly_chart(bar_chart(metrics, "team_name", {"throughput_rps": "Throughput"},
                              "Sequential throughput (higher is better)", "Requests / second", ".2f"),
                    width="stretch")
c3, c4 = st.columns(2)
if metrics["ndcg_at_k"].notna().any():
    c3.plotly_chart(bar_chart(metrics, "team_name",
                              {"precision_at_k": f"Precision@{k}", "ndcg_at_k": f"NDCG@{k}"},
                              "Functional accuracy (higher is better)", "Score (0-1)", ".3f"), width="stretch")
if metrics["final_score"].notna().any():
    c4.plotly_chart(bar_chart(metrics.sort_values("final_score", ascending=False), "team_name",
                              {"final_score": "Final score"}, "Final score", "Points / 100", ".2f"),
                    width="stretch")

conc = results_service.concurrency_frame(run_id)
if not conc.empty:
    teams = list(dict.fromkeys(conc["team_name"]))
    if len(teams) <= len(SERIES):
        fig = go.Figure()
        for i, team in enumerate(teams):
            d = conc[conc["team_name"] == team].sort_values("level")
            fig.add_scatter(x=d["level"], y=d["p95_ms"], name=team, mode="lines+markers",
                            line=dict(color=SERIES[i], width=2), marker=dict(size=8),
                            customdata=d[["error_rate", "throughput_rps"]],
                            hovertemplate=f"<b>{team}</b><br>%{{x}} concurrent<br>p95 %{{y:.1f}} ms"
                                          "<br>error rate %{customdata[0]:.1%}"
                                          "<br>%{customdata[1]:.2f} req/s<extra></extra>")
        fig.update_xaxes(tickvals=sorted(conc["level"].unique()), title="Concurrent requests")
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(style_figure(fig, "p95 latency by concurrency level", "Milliseconds"), width="stretch")
    with st.expander("Concurrency details", expanded=len(teams) > len(SERIES)):
        st.dataframe(conc[["team_name", "level", "total_requests", "successful_requests", "error_rate",
                           "p50_ms", "p95_ms", "throughput_rps", "wall_time_s"]],
                     hide_index=True, width="stretch")

# ------------------------------------------------------------------ per request
st.subheader("Individual requests")
st.caption("Every measurement is traceable to a stored request row.")
raw = results_service.results_frame(run_id)
names = results_service.run_team_names(run_id)
raw.insert(2, "team_name", raw["team_id"].map(lambda t: names.get(int(t))))
f1, f2, f3 = st.columns(3)
team_filter = f1.multiselect("Team", sorted(raw["team_name"].dropna().unique()))
phase_filter = f2.multiselect("Phase", sorted(raw["phase"].unique()))
outcome_filter = f3.multiselect("Outcome", sorted(raw["outcome"].unique()))
view = raw
if team_filter:
    view = view[view["team_name"].isin(team_filter)]
if phase_filter:
    view = view[view["phase"].isin(phase_filter)]
if outcome_filter:
    view = view[view["outcome"].isin(outcome_filter)]
st.dataframe(
    view[["result_id", "team_name", "phase", "test_id", "request_index", "concurrency_level", "started_at",
          "latency_ms", "status_code", "success", "outcome", "attempts", "error_message", "precision_score",
          "recall_score", "ndcg_score"]],
    hide_index=True, width="stretch", height=350,
    column_config={"latency_ms": st.column_config.NumberColumn("latency_ms", format="%.2f")},
)
with st.expander("Inspect a request"):
    rid = st.number_input("result_id", min_value=0, step=1, value=int(view["result_id"].iloc[0]) if len(view) else 0)
    row = raw[raw["result_id"] == rid]
    if len(row):
        r = row.iloc[0]
        st.json({k: (None if pd.isna(v) else v) for k, v in r.drop(["response_payload", "details"]).items()})
        if isinstance(r["details"], str):
            st.write("Details")
            st.json(json.loads(r["details"]))
        if isinstance(r["response_payload"], str):
            st.write("Response payload")
            st.code(r["response_payload"][:5000], language="json")

# ------------------------------------------------------------------ export
st.subheader("Export")
if st.button("Export run to CSV", type="primary"):
    paths = results_service.export_run_csv(run_id)
    st.session_state["export_paths"] = paths
    st.success(f"Saved {len(paths)} files to the exports/ folder.")
for label, path in st.session_state.get("export_paths", {}).items():
    p = Path(path)
    if p.exists() and p.name.startswith(f"run{run_id}_"):
        st.download_button(f"Download {label}.csv", p.read_bytes(), p.name, "text/csv", key=f"dl_{label}")

with st.expander("Run configuration snapshot"):
    st.json(run["configuration"])
