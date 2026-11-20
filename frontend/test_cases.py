import json

import pandas as pd
import streamlit as st

from backend import test_manager
from backend.config import load_reliability_tests, save_reliability_tests

st.title("🧪 Test cases")
st.caption("Reference queries and graded expected results (0 = not relevant, 1 = somewhat, 2 = relevant, "
           "3 = highly relevant). Prepare the reference dataset before the competition.")

cases = test_manager.list_test_cases()
tab_view, tab_add, tab_edit, tab_upload, tab_rel = st.tabs(
    ["View", "Add", "Edit / delete", "Upload", "Reliability suite"])

with tab_view:
    if cases:
        categories = sorted({c["category"] for c in cases})
        chosen = st.multiselect("Category", categories, default=categories)
        shown = [c for c in cases if c["category"] in chosen]
        st.dataframe(pd.DataFrame([{
            "Test ID": c["test_id"], "Query": c["query"], "Category": c["category"],
            "Expected results": len(c["expected_results"]),
            "Relevant (grade ≥ 1)": sum(1 for e in c["expected_results"] if e["relevance"] >= 1),
            "Grades": ", ".join(f"{k}:{v}" for k, v in c["relevance_grades"].items()),
        } for c in shown]), hide_index=True, width="stretch")
        st.download_button("Download as JSON", test_manager.export_test_cases_json(), "test_cases.json",
                           "application/json")
    else:
        st.info("No test cases yet. Add them manually or upload a JSON/CSV file. "
                "A sample file is provided at data/test_cases.json.")

EXAMPLE = json.dumps([{"song_id": "101", "relevance": 3}, {"song_id": "102", "relevance": 2}], indent=2)


def case_form(key: str, initial: dict | None = None):
    initial = initial or {}
    with st.form(key):
        test_id = st.text_input("Test ID", initial.get("test_id", ""), disabled=bool(initial))
        query = st.text_input("Input query", initial.get("query", ""))
        cats = list(test_manager.VALID_CATEGORIES)
        cat = initial.get("category", "functional")
        if cat not in cats:
            cats.append(cat)
        category = st.selectbox("Category", cats, index=cats.index(cat))
        expected = st.text_area(
            "Expected results (JSON list)",
            json.dumps(initial["expected_results"], indent=2) if initial else EXAMPLE, height=220,
            help='List of {"song_id": "...", "relevance": 0-3, "song_name": optional}. '
                 "Leave [] for performance-only queries.")
        submitted = st.form_submit_button("Save", type="primary")
    if submitted:
        return {"test_id": initial.get("test_id", test_id), "query": query, "category": category,
                "expected_results": expected}
    return None


with tab_add:
    raw = case_form("add_case")
    if raw:
        try:
            raw["expected_results"] = json.loads(raw["expected_results"] or "[]")
            tc = test_manager.add_test_case(raw)
            st.success(f"Added {tc['test_id']}.")
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))

with tab_edit:
    if not cases:
        st.write("No test cases.")
    else:
        by_id = {c["test_id"]: c for c in cases}
        tid = st.selectbox("Test case", list(by_id), format_func=lambda t: f"{t} - {by_id[t]['query']}")
        raw = case_form(f"edit_case_{tid}", by_id[tid])
        if raw:
            try:
                raw["expected_results"] = json.loads(raw["expected_results"] or "[]")
                test_manager.upsert_test_case(raw)
                st.success("Saved.")
                st.rerun()
            except (ValueError, json.JSONDecodeError) as exc:
                st.error(str(exc))
        if st.button(f"Delete {tid}"):
            test_manager.delete_test_case(tid)
            st.success(f"Deleted {tid}.")
            st.rerun()

with tab_upload:
    st.markdown(
        "**JSON**: a list of test cases in the format below.\n\n"
        "**CSV**, either format:\n"
        "- one row per expected result: `test_id,query,category,song_id,relevance[,song_name]`\n"
        "- one row per test case: `test_id,query,category,expected_results` with "
        "`expected_results` as `101:3;102:2;103:1` or a JSON list"
    )
    st.code(json.dumps([{"test_id": "TEST001", "query": "Song A", "category": "functional",
                         "expected_results": [{"song_id": "101", "relevance": 3}]}], indent=2), language="json")
    upload = st.file_uploader("Reference dataset", type=["json", "csv"])
    replace = st.checkbox("Replace all existing test cases", value=False)
    if upload and st.button("Import", type="primary"):
        try:
            data = upload.getvalue()
            parsed = (test_manager.parse_csv_upload(data) if upload.name.lower().endswith(".csv")
                      else test_manager.parse_json_upload(data))
            count, errors = test_manager.import_test_cases(parsed, replace_all=replace)
            if errors:
                st.error("Nothing was imported. Fix these problems and upload again:")
                for err in errors[:50]:
                    st.write(f"- {err}")
            else:
                st.success(f"Imported {count} test cases.")
        except Exception as exc:
            st.error(f"Could not read file: {exc}")

with tab_rel:
    st.markdown(
        "Reliability tests are sent to every team. A test passes when the HTTP status is in "
        "`accepted_status` (and 2xx bodies are valid JSON). Timeouts, connection failures and "
        "other status codes fail. Types: `format_check`, `payload`, `raw_body`, `path_suffix`, `duplicate`. "
        "Placeholders: `__first_test_case__` (first test case query), `__long_query_10000__`."
    )
    tests = load_reliability_tests()
    st.dataframe(pd.DataFrame([{"ID": t["id"], "Name": t.get("name"), "Type": t["type"],
                                "Accepted status": ", ".join(map(str, t["accepted_status"])),
                                "Description": t.get("description", "")} for t in tests]),
                 hide_index=True, width="stretch")
    with st.expander("Edit reliability suite (JSON)"):
        text = st.text_area("reliability_tests.json", json.dumps(tests, indent=2), height=400)
        if st.button("Save reliability suite"):
            try:
                save_reliability_tests(json.loads(text))
                st.success("Saved.")
                st.rerun()
            except (ValueError, json.JSONDecodeError) as exc:
                st.error(str(exc))
