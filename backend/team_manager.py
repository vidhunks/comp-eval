"""Team registration and endpoint configuration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.database import get_connection
from backend.security import mask_request_config, validate_endpoint_url

# Keys a team may override in its request configuration.
REQUEST_CONFIG_KEYS = {
    "query_field",
    "limit_field",
    "results_path",
    "id_field",
    "name_field",
    "extra_body",
    "headers",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_request_config(raw: str | dict | None) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    config = json.loads(raw) if isinstance(raw, str) else dict(raw)
    if not isinstance(config, dict):
        raise ValueError("Request configuration must be a JSON object.")
    unknown = set(config) - REQUEST_CONFIG_KEYS
    if unknown:
        raise ValueError(
            f"Unknown request configuration keys: {sorted(unknown)}. Allowed: {sorted(REQUEST_CONFIG_KEYS)}"
        )
    for key in ("extra_body", "headers"):
        if key in config and not isinstance(config[key], dict):
            raise ValueError(f"'{key}' must be a JSON object.")
    return config


def _row_to_team(row) -> dict[str, Any]:
    team = dict(row)
    team["request_config"] = json.loads(team.get("request_config") or "{}")
    team["active_status"] = bool(team["active_status"])
    return team


def _validate(team_name: str, endpoint_url: str, http_method: str, security: dict) -> None:
    if not team_name or not team_name.strip():
        raise ValueError("Team name is required.")
    if http_method.upper() not in ("GET", "POST"):
        raise ValueError("HTTP method must be GET or POST.")
    check = validate_endpoint_url(endpoint_url, security)
    if not check.ok:
        raise ValueError(f"Endpoint URL rejected: {check.reason}")


def add_team(
    team_name: str,
    endpoint_url: str,
    http_method: str,
    request_config: dict | str | None,
    active_status: bool,
    security: dict,
) -> int:
    _validate(team_name, endpoint_url, http_method, security)
    config = parse_request_config(request_config)
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO Teams (team_name, endpoint_url, http_method, request_config, active_status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (team_name.strip(), endpoint_url.strip(), http_method.upper(), json.dumps(config), int(active_status), _now()),
        )
        return int(cur.lastrowid)


def update_team(
    team_id: int,
    team_name: str,
    endpoint_url: str,
    http_method: str,
    request_config: dict | str | None,
    active_status: bool,
    security: dict,
    new_headers: dict | None = None,
) -> None:
    """Update a team. Existing headers are kept unless ``new_headers`` is given,
    so header values (which may hold API keys) never need to be shown to edit a team."""
    _validate(team_name, endpoint_url, http_method, security)
    config = parse_request_config(request_config)
    existing = get_team(team_id)
    if existing is None:
        raise ValueError(f"Team {team_id} does not exist.")
    config.pop("headers", None)
    if new_headers is not None:
        if not isinstance(new_headers, dict):
            raise ValueError("Headers must be a JSON object.")
        if new_headers:
            config["headers"] = new_headers
    elif existing["request_config"].get("headers"):
        config["headers"] = existing["request_config"]["headers"]
    with get_connection() as conn:
        conn.execute(
            "UPDATE Teams SET team_name=?, endpoint_url=?, http_method=?, request_config=?, active_status=?, updated_at=?"
            " WHERE team_id=?",
            (team_name.strip(), endpoint_url.strip(), http_method.upper(), json.dumps(config), int(active_status), _now(), team_id),
        )


def delete_team(team_id: int) -> None:
    """Delete the team registration. Historical results of past runs are kept."""
    with get_connection() as conn:
        conn.execute("DELETE FROM Teams WHERE team_id=?", (team_id,))
        conn.execute("DELETE FROM Architecture_Reviews WHERE team_id=? AND run_id IS NULL", (team_id,))
        conn.execute("DELETE FROM Team_Costs WHERE team_id=?", (team_id,))


def get_team(team_id: int) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM Teams WHERE team_id=?", (team_id,)).fetchone()
    return _row_to_team(row) if row else None


def list_teams(active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM Teams"
    if active_only:
        sql += " WHERE active_status = 1"
    sql += " ORDER BY team_id"
    with get_connection() as conn:
        rows = conn.execute(sql).fetchall()
    return [_row_to_team(r) for r in rows]


def list_teams_for_display() -> list[dict[str, Any]]:
    """Teams with header values masked."""
    teams = list_teams()
    for team in teams:
        team["request_config"] = mask_request_config(team["request_config"])
    return teams


def team_names() -> dict[int, str]:
    """Map of team_id -> name for currently registered teams."""
    with get_connection() as conn:
        rows = conn.execute("SELECT team_id, team_name FROM Teams").fetchall()
    return {r["team_id"]: r["team_name"] for r in rows}


# ---------------------------------------------------------------- cost data

COST_FIELDS = (
    "ai_model",
    "embedding_model",
    "paid_api_usage",
    "api_cost_per_1k_usd",
    "infra_cost_per_1k_usd",
    "uses_local_models",
    "notes",
)


def upsert_cost(team_id: int, values: dict[str, Any]) -> None:
    for key in ("api_cost_per_1k_usd", "infra_cost_per_1k_usd"):
        value = values.get(key)
        if value is not None and value != "" and float(value) < 0:
            raise ValueError(f"{key} must be >= 0.")
    row = {k: values.get(k) for k in COST_FIELDS}
    for key in ("paid_api_usage", "uses_local_models"):
        row[key] = int(bool(row[key]))
    for key in ("api_cost_per_1k_usd", "infra_cost_per_1k_usd"):
        row[key] = None if row[key] in (None, "") else float(row[key])
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO Team_Costs (team_id, ai_model, embedding_model, paid_api_usage, api_cost_per_1k_usd,"
            " infra_cost_per_1k_usd, uses_local_models, notes, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(team_id) DO UPDATE SET ai_model=excluded.ai_model, embedding_model=excluded.embedding_model,"
            " paid_api_usage=excluded.paid_api_usage, api_cost_per_1k_usd=excluded.api_cost_per_1k_usd,"
            " infra_cost_per_1k_usd=excluded.infra_cost_per_1k_usd, uses_local_models=excluded.uses_local_models,"
            " notes=excluded.notes, updated_at=excluded.updated_at",
            (team_id, row["ai_model"], row["embedding_model"], row["paid_api_usage"], row["api_cost_per_1k_usd"],
             row["infra_cost_per_1k_usd"], row["uses_local_models"], row["notes"], _now()),
        )


def get_costs() -> dict[int, dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM Team_Costs").fetchall()
    return {r["team_id"]: dict(r) for r in rows}


def import_costs_csv(df, name_to_id: dict[str, int]) -> tuple[int, list[str]]:
    """Import cost rows from a DataFrame with a 'team_name' column plus COST_FIELDS columns."""
    imported, errors = 0, []
    if "team_name" not in df.columns:
        return 0, ["CSV must contain a 'team_name' column."]
    for idx, rec in df.iterrows():
        name = str(rec["team_name"]).strip()
        team_id = name_to_id.get(name)
        if team_id is None:
            errors.append(f"Row {idx + 2}: unknown team '{name}'.")
            continue
        values = {}
        for field in COST_FIELDS:
            value = rec.get(field)
            if value is not None and str(value) == "nan":
                value = None
            if field in ("paid_api_usage", "uses_local_models"):
                value = str(value).strip().lower() in ("1", "true", "yes", "y")
            values[field] = value
        try:
            upsert_cost(team_id, values)
            imported += 1
        except ValueError as exc:
            errors.append(f"Row {idx + 2}: {exc}")
    return imported, errors
