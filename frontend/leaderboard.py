import json

import streamlit as st

from backend import evaluation_runner, results_service
from backend.config import load_settings
from backend.scoring_engine import CATEGORIES
from frontend.common import run_selector

st.title("🏆 Leaderboard")
run_id = run_selector("leaderboard_run", scored_only=True, default_run=results_service.latest_scored_run_id())
if run_id is None:
    st.info("No scored runs yet. Run an evaluation first.")
    st.stop()

scores = results_service.scores_frame(run_id)
details = {int(r.team_id): json.loads(r.details) for r in scores.itertuples()}
weights = next(iter(details.values()))["scoring_config"]["weights"] if details else {}

table = scores[["rank", "team_name", "functional_score", "performance_score", "reliability_score",
                "scalability_score", "cost_score", "architecture_score", "final_score"]]
num = st.column_config.NumberColumn
st.dataframe(
    table, hide_index=True, width="stretch",
    column_config={
        "rank": "Rank", "team_name": "Team",
        "functional_score": num(f"Functional /{weights.get('functional', 30):g}", format="%.2f"),
        "performance_score": num(f"Performance /{weights.get('performance', 20):g}", format="%.2f"),
        "reliability_score": num(f"Reliability /{weights.get('reliability', 15):g}", format="%.2f"),
        "scalability_score": num(f"Scalability /{weights.get('scalability', 10):g}", format="%.2f"),
        "cost_score": num(f"Cost /{weights.get('cost', 10):g}", format="%.2f"),
        "architecture_score": num(f"Architecture /{weights.get('architecture', 15):g}", format="%.2f"),
        "final_score": st.column_config.ProgressColumn("Final /100", min_value=0, max_value=100, format="%.2f"),
    },
)
computed = scores["computed_at"].max()
st.caption(f"Scores computed {computed} UTC. Ties share a rank.")

st.subheader("Score breakdown")
for r in scores.itertuples():
    d = details[int(r.team_id)]
    missing = d.get("missing", [])
    label = f"#{r.rank} {r.team_name} — {r.final_score:.2f}"
    if missing:
        label += f"  (not evaluated: {', '.join(missing)})"
    with st.expander(label):
        for cat in CATEGORIES:
            c = d["categories"][cat]
            mark = "" if c["evaluated"] else " ⚠️"
            st.markdown(f"**{cat.title()}**: {c['points']:.2f} / {c['max']:g}{mark}  \n`{c['formula']}`")
        if d.get("review_input"):
            st.write("Architecture review used:", d["review_input"])
        if d.get("cost_input"):
            st.write("Cost data used:", {k: v for k, v in d["cost_input"].items() if k not in ("team_id",)})

st.subheader("Recompute")
st.caption("Recompute this run's scores from its stored measurements, using the current Settings scoring "
           "configuration and the current architecture reviews and cost data. Measurements are not repeated.")
if st.button("Recompute scores", disabled=evaluation_runner.is_running()):
    results_service.compute_and_store_scores(run_id, load_settings()["scoring"])
    st.success("Scores recomputed.")
    st.rerun()
st.download_button("Download leaderboard CSV", table.to_csv(index=False), f"leaderboard_run{run_id}.csv", "text/csv")
