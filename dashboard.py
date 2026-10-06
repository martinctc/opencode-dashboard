#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from OpenCode usage CSV exports."""

import argparse
import glob
import warnings
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.io as pio


REQUIRED_COLUMNS = {
    "user", "date", "project", "provider", "model", "calls",
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens", "cost_usd",
    "variant", "session_id", "exported_at",
}
NUMERIC_COLUMNS = (
    "calls", "total_tokens", "input_tokens", "output_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "cost_usd",
)


def load_data(pattern: str) -> pd.DataFrame:
    paths = sorted(Path(path) for path in glob.glob(pattern))
    if not paths and Path(pattern).is_file():
        paths = [Path(pattern)]
    if not paths:
        raise FileNotFoundError(f"No input CSV files matched: {pattern}")
    frames = []
    for path in paths:
        frame = pd.read_csv(path)
        missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        frame["exported_at"] = pd.to_datetime(
            frame["exported_at"], errors="raise", utc=True
        )
        if frame["exported_at"].isna().any():
            raise ValueError(f"{path} has a missing export timestamp")
        missing_session = (
            frame["session_id"].isna()
            | frame["session_id"].astype(str).str.strip().eq("")
        )
        if missing_session.any():
            raise ValueError(f"{path} has a missing session ID")
        for column in NUMERIC_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
            if frame[column].isna().any() or (frame[column] < 0).any():
                raise ValueError(f"{path} has missing or negative values in {column}")
        frames.append(frame)
    data = pd.concat(frames, ignore_index=True)
    identity = ["user", "session_id", "date", "provider", "model", "variant"]
    if data["variant"].isna().any():
        data["variant"] = data["variant"].fillna("n/a")
    duplicate_mask = data.duplicated(identity, keep=False)
    if duplicate_mask.any():
        duplicate_rows = data.loc[duplicate_mask]
        conflicting_groups = 0
        for _, group in duplicate_rows.groupby(identity, dropna=False):
            if any(group[column].nunique(dropna=False) > 1 for column in NUMERIC_COLUMNS):
                conflicting_groups += 1
        warnings.warn(
            f"Found {int(duplicate_mask.sum())} overlapping export row(s) "
            f"({conflicting_groups} with changed totals); keeping the latest "
            "export for each session, date, provider, model, and variant.",
            RuntimeWarning,
        )
        data = (
            data.sort_values("exported_at", kind="stable")
            .drop_duplicates(identity, keep="last")
        )
    return data


def build_dashboard(data: pd.DataFrame, out_path: Path) -> None:
    tokens = int(data["total_tokens"].sum())
    cost = float(data["cost_usd"].sum())
    calls = int(data["calls"].sum())
    sessions = data["session_id"].nunique() if "session_id" in data else None

    daily = data.groupby("date", as_index=False).agg(
        total_tokens=("total_tokens", "sum"), cost_usd=("cost_usd", "sum")
    )
    composition = data[[
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens",
    ]].sum().rename_axis("category").reset_index(name="tokens")
    by_model = data.groupby("model", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False).head(15)
    by_project = data.groupby("project", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False).head(15)
    by_provider = data.groupby("provider", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False)
    token_chart = px.line(daily, x="date", y="total_tokens", markers=True,
                          title="Tokens by day")
    cost_chart = px.line(daily, x="date", y="cost_usd", markers=True,
                         title="Recorded cost by day")
    composition_chart = px.bar(composition, x="category", y="tokens",
                               title="Token categories")
    model_chart = px.bar(by_model, x="total_tokens", y="model", orientation="h",
                         title="Top models by tokens")
    project_chart = px.bar(by_project, x="total_tokens", y="project", orientation="h",
                           title="Top projects by tokens")
    provider_chart = px.pie(by_provider, names="provider", values="total_tokens",
                            title="Tokens by provider")
    model_chart.update_layout(yaxis={"categoryorder": "total ascending"})
    project_chart.update_layout(yaxis={"categoryorder": "total ascending"})
    charts = "".join(
        pio.to_html(fig, full_html=False, include_plotlyjs=True if index == 0 else False)
        for index, fig in enumerate((
            token_chart, cost_chart, composition_chart, model_chart,
            project_chart, provider_chart,
        ))
    )
    session_metric = f"<article><span>Sessions</span><strong>{sessions:,}</strong></article>" if sessions is not None else ""
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OpenCode Usage Dashboard</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#17212b}}
h1{{margin-bottom:.3rem}}.muted{{color:#536273}}.cards{{display:flex;flex-wrap:wrap;gap:1rem;margin:1.5rem 0}}
article{{background:#f1f5f9;border-radius:8px;padding:1rem 1.4rem;min-width:150px}}
article span{{display:block;color:#536273}}article strong{{font-size:1.5rem}}.chart{{margin:1.5rem 0}}
</style></head><body><h1>OpenCode Usage Dashboard</h1>
<p class="muted">Recorded OpenCode usage. Cost is reported by OpenCode, not an invoice.</p>
<section class="cards"><article><span>Input + output tokens</span><strong>{tokens:,}</strong></article>
<article><span>Recorded cost (USD)</span><strong>${cost:,.2f}</strong></article>
<article><span>Assistant messages</span><strong>{calls:,}</strong></article>{session_metric}</section>
{charts}</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_pattern", default="opencode_usage_*.csv",
                        help="Input CSV path or glob pattern")
    parser.add_argument("--out", type=Path, default=Path("usage_dashboard.html"),
                        help="HTML output path")
    args = parser.parse_args(argv)
    data = load_data(args.input_pattern)
    build_dashboard(data, args.out)
    print(f"Wrote dashboard to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
