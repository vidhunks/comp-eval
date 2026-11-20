"""Shared helpers for the Streamlit pages (formatting, charts, run selection)."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend import results_service

# Categorical slots in fixed order (validated light-mode palette). Never cycled:
# charts with more series than slots switch to a different form.
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRID = "rgba(128,128,128,0.18)"

STATUS_LABELS = {
    "running": "⏳ Running",
    "completed": "✅ Completed",
    "completed_with_errors": "⚠️ Completed with errors",
    "cancelled": "⏹️ Cancelled",
    "failed": "❌ Failed",
    "aborted": "❌ Aborted (app restarted)",
}


def fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return str(value)


def style_figure(fig: go.Figure, title: str, y_title: str, height: int = 360) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=15)),
        height=height,
        margin=dict(l=10, r=10, t=50, b=10),
        bargap=0.35,
        bargroupgap=0.08,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title=None),
        hoverlabel=dict(namelength=-1),
    )
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(title=y_title, gridcolor=GRID, zeroline=False)
    return fig


def bar_chart(df: pd.DataFrame, x: str, series: dict[str, str], title: str, y_title: str,
              value_fmt: str = ".1f") -> go.Figure:
    """Bar chart with one bar group per row of ``df``; ``series`` maps column -> label.
    Slots are assigned in fixed order; a single series needs no legend."""
    fig = go.Figure()
    for i, (col, label) in enumerate(series.items()):
        fig.add_bar(
            x=df[x], y=df[col], name=label,
            marker=dict(color=SERIES[i], cornerradius=4, line=dict(width=0)),
            hovertemplate=f"<b>%{{x}}</b><br>{label}: %{{y:{value_fmt}}}<extra></extra>",
        )
    fig.update_layout(showlegend=len(series) > 1, barmode="group")
    return style_figure(fig, title, y_title)


def run_selector(key: str, scored_only: bool = False, default_run: int | None = None) -> int | None:
    runs = results_service.list_runs()
    if scored_only:
        scored = set(results_service.latest_scored_run_ids())
        runs = [r for r in runs if r["run_id"] in scored]
    if not runs:
        return None
    labels = {
        r["run_id"]: f"Run {r['run_id']} · {r['start_time'][:19].replace('T', ' ')} UTC · "
                     f"{STATUS_LABELS.get(r['evaluation_status'], r['evaluation_status'])} · "
                     f"{', '.join(r['phases'])}"
        for r in runs
    }
    ids = list(labels)
    index = ids.index(default_run) if default_run in ids else 0
    return st.selectbox("Evaluation run", ids, index=index, format_func=labels.get, key=key)


def show_problems(problems: list[str]) -> None:
    for problem in problems:
        st.error(problem)
