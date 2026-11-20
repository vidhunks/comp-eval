import streamlit as st

from backend import evaluation_runner, results_service, team_manager, test_manager
from backend.config import load_reliability_tests
from frontend.common import STATUS_LABELS, bar_chart

st.title("🏁 Competition Evaluator")
st.caption("Automated evaluation and scoring of participant application endpoints.")

teams = team_manager.list_teams()
cases = test_manager.list_test_cases()
active = [t for t in teams if t["active_status"]]
runs = results_service.list_runs()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total teams", len(teams))
c2.metric("Active endpoints", len(active))
c3.metric("Test cases", len(cases), help="Reference queries used for performance and functional evaluation")
c4.metric("Reliability tests", len(load_reliability_tests()))

st.subheader("Evaluation status")
if evaluation_runner.is_running():
    progress = evaluation_runner.current_progress() or {}
    st.info(f"Run {progress.get('run_id')} in progress: {progress.get('current', '')}")
    st.page_link("frontend/evaluation.py", label="Open the evaluation page", icon="▶️")
elif runs:
    last = runs[0]
    st.write(f"Last run: **Run {last['run_id']}** · {STATUS_LABELS.get(last['evaluation_status'], last['evaluation_status'])}"
             f" · started {last['start_time'][:19].replace('T', ' ')} UTC")
else:
    st.write("No evaluation has been run yet.")

if not teams or not cases:
    st.subheader("Getting started")
    st.markdown(
        "1. Register teams and their endpoints on **Teams**.\n"
        "2. Add or upload the reference dataset on **Test cases**.\n"
        "3. Check the targets and weights on **Settings**.\n"
        "4. Run the evaluation on **Run evaluation**, then enter reviews on **Architecture & cost**.\n"
        "5. View the **Leaderboard**."
    )

st.subheader("Latest competition results")
run_id = results_service.latest_scored_run_id()
if run_id is None:
    st.write("No scored runs yet.")
else:
    scores = results_service.scores_frame(run_id)
    st.caption(f"From run {run_id}")
    left, right = st.columns([1, 1])
    left.dataframe(
        scores[["rank", "team_name", "final_score"]].head(10),
        hide_index=True, width="stretch",
        column_config={"rank": "Rank", "team_name": "Team",
                       "final_score": st.column_config.ProgressColumn("Final score", min_value=0, max_value=100,
                                                                      format="%.1f")},
    )
    right.plotly_chart(bar_chart(scores.head(10), "team_name", {"final_score": "Final score"},
                                 "Final score (top 10)", "Points / 100"), width="stretch")
