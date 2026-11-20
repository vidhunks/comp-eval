import pandas as pd
import streamlit as st

from backend import evaluation_runner, results_service, team_manager
from backend.config import load_settings
from frontend.common import STATUS_LABELS

st.title("▶️ Run evaluation")
settings = load_settings()
req = settings["request"]

st.caption(
    f"Timeout {req['timeout_seconds']}s · warm-up {req['warmup_requests']} · benchmark {req['benchmark_requests']} · "
    f"throughput {settings['throughput']['requests']} · concurrency levels {settings['concurrency']['levels']} "
    f"× {settings['concurrency']['rounds_per_level']} rounds · retries {req['retry_attempts']}. "
    "Teams are evaluated one at a time so they never compete for the evaluator's network. "
    "Latency is end-to-end HTTP round-trip time from this machine."
)

ACTIONS = [
    ("Run connectivity test", ["connectivity"]),
    ("Start warm-up", ["warmup"]),
    ("Start performance benchmark", ["benchmark", "throughput"]),
    ("Start functional evaluation", ["functional"]),
    ("Start reliability evaluation", ["reliability"]),
    ("Start concurrency evaluation", ["concurrency"]),
]


@st.fragment(run_every=1.0)
def live_progress() -> None:
    progress = evaluation_runner.current_progress()
    running = evaluation_runner.is_running()
    if not progress:
        st.write("No run started in this session.")
        return
    total = max(progress["total_steps"], 1)
    status = STATUS_LABELS.get(progress["status"], progress["status"])
    st.progress(min(progress["done_steps"] / total, 1.0),
                text=f"Run {progress['run_id']} · {status} · step {progress['done_steps']}/{total}")
    st.write(f"**Current:** {progress['current']}")
    if running and st.button("Cancel run", type="secondary"):
        evaluation_runner.request_cancel()
        st.warning("Cancellation requested; the run stops after the current request.")
    if progress["errors"]:
        with st.expander(f"Errors ({len(progress['errors'])})", expanded=True):
            for err in progress["errors"][-50:]:
                st.error(err)
    with st.expander("Log", expanded=running):
        st.code("\n".join(progress["log"][-60:]) or "(empty)", language=None)
    if not running and st.session_state.get("was_running"):
        st.session_state["was_running"] = False
        st.success("Run finished. See the Results and Leaderboard pages.")
    st.session_state["was_running"] = running


teams = team_manager.list_teams(active_only=True)
running = evaluation_runner.is_running()

st.subheader("Teams")
if not teams:
    st.warning("No active teams. Register teams on the Teams page.")
by_id = {t["team_id"]: t for t in teams}
selected = st.multiselect("Teams to evaluate", list(by_id), default=list(by_id),
                          format_func=lambda i: f"{by_id[i]['team_name']} ({by_id[i]['endpoint_url']})")

st.subheader("Actions")


def launch(phases: list[str]) -> None:
    try:
        run_id = evaluation_runner.start_run(phases, selected)
        st.session_state["was_running"] = True
        st.toast(f"Run {run_id} started.")
    except (RuntimeError, ValueError) as exc:
        st.error(str(exc))


disabled = running or not selected
if running:
    st.info("An evaluation is running. New runs are disabled until it finishes or is cancelled.")
if st.button("🚀 Run complete evaluation", type="primary", disabled=disabled, width="stretch"):
    launch(list(evaluation_runner.FULL_EVALUATION))
cols = st.columns(3)
for i, (label, phases) in enumerate(ACTIONS):
    if cols[i % 3].button(label, disabled=disabled, width="stretch", help="Phases: " + ", ".join(phases)):
        launch(phases)
st.caption("The benchmark always starts with warm-up requests, which are stored but excluded from scores. "
           "Scores are computed automatically at the end of each run that includes scoring phases.")

st.subheader("Progress")
live_progress()

st.subheader("Run history")
runs = results_service.list_runs()
if runs:
    st.dataframe(pd.DataFrame([{
        "Run": r["run_id"], "Started (UTC)": r["start_time"][:19].replace("T", " "),
        "Ended (UTC)": (r["end_time"] or "")[:19].replace("T", " "),
        "Status": STATUS_LABELS.get(r["evaluation_status"], r["evaluation_status"]),
        "Phases": ", ".join(r["phases"]),
    } for r in runs]), hide_index=True, width="stretch")
else:
    st.write("No runs yet.")
