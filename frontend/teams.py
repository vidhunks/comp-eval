import json

import pandas as pd
import streamlit as st

from backend import team_manager, test_manager
from backend.config import load_settings
from backend.request_executor import run_connectivity_check
from backend.security import mask_request_config

st.title("👥 Teams")
settings = load_settings()
security = settings["security"]

CONFIG_HELP = (
    "Optional JSON overriding the default request template, e.g. "
    '{"query_field": "q", "results_path": "data.items", "id_field": "id"}. '
    "Allowed keys: " + ", ".join(sorted(team_manager.REQUEST_CONFIG_KEYS - {"headers"})) + ". "
    "Headers are set separately and are never displayed."
)

teams = team_manager.list_teams()

# ------------------------------------------------------------------ list
if teams:
    df = pd.DataFrame([{
        "ID": t["team_id"], "Team": t["team_name"], "Endpoint": t["endpoint_url"], "Method": t["http_method"],
        "Active": t["active_status"],
        "Request config": json.dumps(mask_request_config(t["request_config"])) if t["request_config"] else "",
        "Created": t["created_at"],
    } for t in teams])
    st.dataframe(df, hide_index=True, width="stretch")
else:
    st.info("No teams registered yet.")

tab_add, tab_edit, tab_test = st.tabs(["Add team", "Edit / delete", "Test connectivity"])

# ------------------------------------------------------------------- add
with tab_add:
    with st.form("add_team", clear_on_submit=True):
        name = st.text_input("Team name")
        url = st.text_input("Endpoint URL", placeholder="http://192.168.1.10:8000/search")
        method = st.selectbox("HTTP method", ["POST", "GET"],
                              index=0 if settings["request"]["http_method"] == "POST" else 1)
        config_text = st.text_area("Request configuration (JSON, optional)", "{}", help=CONFIG_HELP)
        headers_text = st.text_area("Request headers (JSON, optional)", "",
                                    help="Only if the participant API requires them. Values are stored but never shown.")
        active = st.checkbox("Active (participates in evaluation)", value=True)
        if st.form_submit_button("Add team", type="primary"):
            try:
                config = team_manager.parse_request_config(config_text or "{}")
                if headers_text.strip():
                    config["headers"] = json.loads(headers_text)
                    team_manager.parse_request_config(config)
                team_id = team_manager.add_team(name, url, method, config, active, security)
                st.success(f"Team '{name}' added (ID {team_id}).")
                st.rerun()
            except (ValueError, json.JSONDecodeError) as exc:
                st.error(str(exc))
            except Exception as exc:  # e.g. unique constraint
                st.error(f"Could not add team: {exc}")

# ------------------------------------------------------------------ edit
with tab_edit:
    if not teams:
        st.write("No teams to edit.")
    else:
        by_id = {t["team_id"]: t for t in teams}
        team_id = st.selectbox("Team", list(by_id), format_func=lambda i: f"{by_id[i]['team_name']} (ID {i})")
        team = by_id[team_id]
        visible_config = {k: v for k, v in team["request_config"].items() if k != "headers"}
        has_headers = bool(team["request_config"].get("headers"))
        with st.form(f"edit_team_{team_id}"):
            name = st.text_input("Team name", team["team_name"])
            url = st.text_input("Endpoint URL", team["endpoint_url"])
            method = st.selectbox("HTTP method", ["POST", "GET"], index=0 if team["http_method"] == "POST" else 1)
            config_text = st.text_area("Request configuration (JSON)", json.dumps(visible_config, indent=2),
                                       help=CONFIG_HELP)
            st.caption(f"Headers: {', '.join(team['request_config']['headers']) + ' (values hidden)' if has_headers else 'none'}")
            headers_text = st.text_area("Replace headers (JSON)", "",
                                        help="Leave empty to keep the current headers. Enter {} to remove them.")
            active = st.checkbox("Active", value=team["active_status"])
            if st.form_submit_button("Save changes", type="primary"):
                try:
                    new_headers = json.loads(headers_text) if headers_text.strip() else None
                    team_manager.update_team(team_id, name, url, method, config_text or "{}", active, security,
                                             new_headers=new_headers)
                    st.success("Saved.")
                    st.rerun()
                except (ValueError, json.JSONDecodeError) as exc:
                    st.error(str(exc))
                except Exception as exc:
                    st.error(f"Could not save: {exc}")

        with st.expander("Delete team"):
            st.warning("Deleting removes the registration, current review and cost data. "
                       "Results from past runs are kept.")
            confirm = st.text_input("Type the team name to confirm", key=f"del_confirm_{team_id}")
            if st.button("Delete team", type="secondary", disabled=confirm != team["team_name"]):
                team_manager.delete_team(team_id)
                st.success("Deleted.")
                st.rerun()

# ------------------------------------------------------------------ test
with tab_test:
    query = st.text_input("Test query", test_manager.default_query())
    st.caption("Sends one standard request to each selected endpoint and reports the outcome and round-trip latency.")
    if teams:
        by_id = {t["team_id"]: t for t in teams}
        selected = st.multiselect("Teams", list(by_id), default=[t["team_id"] for t in teams if t["active_status"]],
                                  format_func=lambda i: by_id[i]["team_name"])
        if st.button("Run connectivity test", type="primary", disabled=not selected):
            rows = []
            with st.spinner("Testing endpoints..."):
                for tid in selected:
                    out = run_connectivity_check(by_id[tid], settings, query)
                    rows.append({
                        "Team": by_id[tid]["team_name"],
                        "Outcome": ("✅ " if out.success else "❌ ") + out.outcome,
                        "HTTP status": out.status_code,
                        "Latency (ms)": None if out.latency_ms is None else round(out.latency_ms, 1),
                        "Results returned": len(out.results) if out.results is not None else None,
                        "Error": out.error_message or "",
                    })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
