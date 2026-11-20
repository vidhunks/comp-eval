"""Test case (reference dataset) management."""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd

from backend.database import get_connection

VALID_CATEGORIES = ("functional", "performance", "reliability", "edge_case", "other")


def normalize_test_case(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a test case and reconcile ``expected_results`` with ``relevance_grades``.

    ``expected_results`` is a list of ``{"song_id": ..., "relevance": 0-3, "song_name": optional}``.
    ``relevance_grades`` is a map ``{song_id: grade}``. Either may be given; missing grades
    are filled from the other. When both define a grade for an id, ``relevance_grades`` wins.
    """
    test_id = str(raw.get("test_id", "")).strip()
    if not test_id:
        raise ValueError("test_id is required.")
    query = raw.get("query")
    if query is None or str(query).strip() == "":
        raise ValueError(f"{test_id}: query is required.")
    category = str(raw.get("category") or "functional").strip().lower()

    expected = raw.get("expected_results") or []
    if isinstance(expected, str):
        expected = json.loads(expected) if expected.strip() else []
    grades = raw.get("relevance_grades") or {}
    if isinstance(grades, str):
        grades = json.loads(grades) if grades.strip() else {}
    if not isinstance(expected, list) or not isinstance(grades, dict):
        raise ValueError(f"{test_id}: expected_results must be a list and relevance_grades an object.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in expected:
        if isinstance(item, (str, int)):
            item = {"song_id": str(item)}
        if not isinstance(item, dict):
            raise ValueError(f"{test_id}: each expected result must be an object.")
        sid = item.get("song_id", item.get("id"))
        name = item.get("song_name", item.get("name"))
        if sid is None and not name:
            raise ValueError(f"{test_id}: expected results need a song_id or song_name.")
        key = str(sid) if sid is not None else None
        entry: dict[str, Any] = {}
        if key is not None:
            if key in seen:
                raise ValueError(f"{test_id}: duplicate expected song_id '{key}'.")
            seen.add(key)
            entry["song_id"] = key
        if name:
            entry["song_name"] = str(name)
        rel = item.get("relevance", grades.get(key) if key else None)
        rel = 1 if rel is None else rel
        entry["relevance"] = _grade(rel, test_id)
        normalized.append(entry)

    for sid, grade in grades.items():
        sid = str(sid)
        if sid in seen:
            for entry in normalized:
                if entry.get("song_id") == sid:
                    entry["relevance"] = _grade(grade, test_id)
        else:
            normalized.append({"song_id": sid, "relevance": _grade(grade, test_id)})
            seen.add(sid)

    merged_grades = {e["song_id"]: e["relevance"] for e in normalized if "song_id" in e}
    return {
        "test_id": test_id,
        "query": str(query),
        "expected_results": normalized,
        "category": category,
        "relevance_grades": merged_grades,
    }


def _grade(value: Any, test_id: str) -> int:
    try:
        grade = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{test_id}: relevance grade '{value}' is not an integer 0-3.") from None
    if grade < 0 or grade > 3:
        raise ValueError(f"{test_id}: relevance grade {grade} must be between 0 and 3.")
    return grade


def upsert_test_case(raw: dict[str, Any]) -> dict[str, Any]:
    tc = normalize_test_case(raw)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO Test_Cases (test_id, query, expected_results, category, relevance_grades)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT(test_id) DO UPDATE SET query=excluded.query,"
            " expected_results=excluded.expected_results, category=excluded.category,"
            " relevance_grades=excluded.relevance_grades",
            (tc["test_id"], tc["query"], json.dumps(tc["expected_results"]), tc["category"],
             json.dumps(tc["relevance_grades"])),
        )
    return tc


def add_test_case(raw: dict[str, Any]) -> dict[str, Any]:
    tc = normalize_test_case(raw)
    if get_test_case(tc["test_id"]) is not None:
        raise ValueError(f"Test case {tc['test_id']} already exists.")
    return upsert_test_case(tc)


def delete_test_case(test_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM Test_Cases WHERE test_id=?", (test_id,))


def _row_to_tc(row) -> dict[str, Any]:
    tc = dict(row)
    tc["expected_results"] = json.loads(tc["expected_results"] or "[]")
    tc["relevance_grades"] = json.loads(tc["relevance_grades"] or "{}")
    return tc


def get_test_case(test_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM Test_Cases WHERE test_id=?", (test_id,)).fetchone()
    return _row_to_tc(row) if row else None


def list_test_cases(category: str | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM Test_Cases", ()
    if category:
        sql, params = sql + " WHERE category=?", (category,)
    with get_connection() as conn:
        rows = conn.execute(sql + " ORDER BY test_id", params).fetchall()
    return [_row_to_tc(r) for r in rows]


def default_query() -> str:
    """Query used for connectivity checks: the first test case, or a placeholder."""
    cases = list_test_cases()
    return cases[0]["query"] if cases else "Song A"


# ------------------------------------------------------------------ import

def parse_json_upload(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data.decode("utf-8-sig"))
    if isinstance(payload, dict) and "test_cases" in payload:
        payload = payload["test_cases"]
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("JSON must be a list of test cases or {\"test_cases\": [...]}.")
    return payload


def parse_csv_upload(data: bytes) -> list[dict[str, Any]]:
    """CSV formats supported:

    1. One row per test case: ``test_id,query,category,expected_results`` where
       ``expected_results`` is JSON, or ``id:grade;id:grade`` (e.g. ``101:3;102:2``).
    2. One row per expected result: ``test_id,query,category,song_id,relevance[,song_name]``.
    """
    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"test_id", "query"} <= set(df.columns):
        raise ValueError("CSV must contain at least 'test_id' and 'query' columns.")

    cases: dict[str, dict[str, Any]] = {}
    for _, rec in df.iterrows():
        tid = rec["test_id"].strip()
        if not tid:
            continue
        case = cases.setdefault(
            tid,
            {"test_id": tid, "query": rec["query"], "category": rec.get("category", "") or "functional",
             "expected_results": []},
        )
        if "song_id" in df.columns:
            if rec["song_id"].strip():
                item = {"song_id": rec["song_id"].strip(), "relevance": rec.get("relevance", "") or 1}
                if rec.get("song_name"):
                    item["song_name"] = rec["song_name"]
                case["expected_results"].append(item)
        elif rec.get("expected_results", "").strip():
            text = rec["expected_results"].strip()
            if text.startswith("["):
                case["expected_results"] = json.loads(text)
            else:
                for pair in text.split(";"):
                    if not pair.strip():
                        continue
                    sid, _, grade = pair.partition(":")
                    case["expected_results"].append({"song_id": sid.strip(), "relevance": grade.strip() or 1})
    return list(cases.values())


def import_test_cases(raw_cases: list[dict[str, Any]], replace_all: bool = False) -> tuple[int, list[str]]:
    """Validate every case first; write nothing if any is invalid."""
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for idx, raw in enumerate(raw_cases, start=1):
        try:
            tc = normalize_test_case(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Item {idx}: {exc}")
            continue
        if tc["test_id"] in ids:
            errors.append(f"Item {idx}: duplicate test_id {tc['test_id']} in upload.")
            continue
        ids.add(tc["test_id"])
        normalized.append(tc)
    if errors:
        return 0, errors
    with get_connection() as conn:
        if replace_all:
            conn.execute("DELETE FROM Test_Cases")
        for tc in normalized:
            conn.execute(
                "INSERT INTO Test_Cases (test_id, query, expected_results, category, relevance_grades)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(test_id) DO UPDATE SET query=excluded.query,"
                " expected_results=excluded.expected_results, category=excluded.category,"
                " relevance_grades=excluded.relevance_grades",
                (tc["test_id"], tc["query"], json.dumps(tc["expected_results"]), tc["category"],
                 json.dumps(tc["relevance_grades"])),
            )
    return len(normalized), []


def export_test_cases_json() -> str:
    cases = list_test_cases()
    return json.dumps(
        [{k: tc[k] for k in ("test_id", "query", "expected_results", "category")} for tc in cases],
        indent=2,
    )
