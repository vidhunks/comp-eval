import pandas as pd
import streamlit as st

from backend import review_manager, team_manager
from backend.config import ARCHITECTURE_CHECKLIST

st.title("📝 Architecture & cost")
st.caption("Manual inputs. After editing, recompute scores on the Leaderboard page to apply them to a past run; "
           "new runs pick them up automatically.")

teams = team_manager.list_teams()
if not teams:
    st.info("No teams registered.")
    st.stop()
by_id = {t["team_id"]: t for t in teams}

tab_arch, tab_cost = st.tabs(["Architecture review", "Cost data"])

with tab_arch:
    team_id = st.selectbox("Team", list(by_id), format_func=lambda i: by_id[i]["team_name"], key="arch_team")
    current = review_manager.get_review(team_id)
    with st.form(f"review_{team_id}"):
        entries = {}
        for category, maximum in ARCHITECTURE_CHECKLIST.items():
            c1, c2 = st.columns([1, 3])
            pts = c1.number_input(f"{category} (max {maximum})", min_value=0.0, max_value=float(maximum), step=0.5,
                                  value=float(current.get(category, {}).get("points", 0.0)))
            note = c2.text_input("Comments", current.get(category, {}).get("comments", ""),
                                 key=f"c_{team_id}_{category}")
            entries[category] = (pts, note)
        total = sum(p for p, _ in entries.values())
        if st.form_submit_button("Save review", type="primary"):
            try:
                review_manager.save_review(team_id, entries)
                st.success(f"Saved: {total:g} / {sum(ARCHITECTURE_CHECKLIST.values())} points.")
            except ValueError as exc:
                st.error(str(exc))

    rows = []
    for t in teams:
        review = review_manager.get_review(t["team_id"])
        rows.append({"Team": t["team_name"], "Reviewed": bool(review),
                     **{c: review.get(c, {}).get("points") for c in ARCHITECTURE_CHECKLIST},
                     "Total": sum(v["points"] for v in review.values()) if review else None})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

with tab_cost:
    st.markdown("Participant-provided estimates. Score uses (API + infrastructure cost per 1,000 requests) "
                "÷ success rate = cost per 1,000 *successful* requests, compared with the target in Settings.")
    costs = team_manager.get_costs()
    team_id = st.selectbox("Team", list(by_id), format_func=lambda i: by_id[i]["team_name"], key="cost_team")
    cur = costs.get(team_id, {})
    with st.form(f"cost_{team_id}"):
        ai_model = st.text_input("AI model", cur.get("ai_model") or "")
        emb = st.text_input("Embedding model", cur.get("embedding_model") or "")
        paid = st.checkbox("Uses paid APIs", bool(cur.get("paid_api_usage")))
        local = st.checkbox("Uses local models", bool(cur.get("uses_local_models")))
        api = st.number_input("Estimated API cost per 1,000 requests (USD)", min_value=0.0, step=0.01, format="%.4f",
                              value=float(cur.get("api_cost_per_1k_usd") or 0.0))
        infra = st.number_input("Estimated infrastructure cost per 1,000 requests (USD)", min_value=0.0, step=0.01,
                                format="%.4f", value=float(cur.get("infra_cost_per_1k_usd") or 0.0))
        notes = st.text_area("Notes", cur.get("notes") or "")
        if st.form_submit_button("Save cost data", type="primary"):
            team_manager.upsert_cost(team_id, {
                "ai_model": ai_model, "embedding_model": emb, "paid_api_usage": paid, "uses_local_models": local,
                "api_cost_per_1k_usd": api, "infra_cost_per_1k_usd": infra, "notes": notes})
            st.success("Saved.")
            st.rerun()

    with st.expander("Import cost data from CSV"):
        st.code("team_name,ai_model,embedding_model,paid_api_usage,api_cost_per_1k_usd,"
                "infra_cost_per_1k_usd,uses_local_models,notes", language=None)
        upload = st.file_uploader("Cost CSV", type=["csv"])
        if upload and st.button("Import costs"):
            df = pd.read_csv(upload)
            count, errors = team_manager.import_costs_csv(df, {t["team_name"]: t["team_id"] for t in teams})
            st.success(f"Imported {count} rows.")
            for err in errors:
                st.error(err)

    st.dataframe(pd.DataFrame([{
        "Team": t["team_name"],
        **{k: costs.get(t["team_id"], {}).get(k) for k in team_manager.COST_FIELDS},
    } for t in teams]), hide_index=True, width="stretch")
