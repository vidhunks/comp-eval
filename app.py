"""Competition Evaluator - Streamlit entry point.

    streamlit run app.py
"""

import streamlit as st

from backend.database import init_db
from backend.evaluation_runner import recover_interrupted_runs

st.set_page_config(page_title="Competition Evaluator", page_icon="🏁", layout="wide")


@st.cache_resource
def startup() -> int:
    """Runs once per server process."""
    init_db()
    return recover_interrupted_runs()


recovered = startup()
if recovered:
    st.toast(f"{recovered} interrupted run(s) from a previous session were marked as aborted.")

pages = {
    "Competition": [
        st.Page("frontend/dashboard.py", title="Home", icon="🏠", default=True),
        st.Page("frontend/leaderboard.py", title="Leaderboard", icon="🏆"),
        st.Page("frontend/results.py", title="Results", icon="📊"),
    ],
    "Evaluate": [
        st.Page("frontend/evaluation.py", title="Run evaluation", icon="▶️"),
        st.Page("frontend/reviews.py", title="Architecture & cost", icon="📝"),
    ],
    "Setup": [
        st.Page("frontend/teams.py", title="Teams", icon="👥"),
        st.Page("frontend/test_cases.py", title="Test cases", icon="🧪"),
        st.Page("frontend/settings.py", title="Settings", icon="⚙️"),
    ],
}
st.navigation(pages).run()
