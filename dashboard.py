#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from OpenCode usage CSV exports."""

import argparse
import glob
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
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
CATEGORY_LABELS = {
    "input_tokens": "Input",
    "output_tokens": "Output",
    "cache_read_tokens": "Cache read",
    "cache_write_tokens": "Cache write",
    "reasoning_tokens": "Reasoning",
}
TOP_N = 10

# Okabe-Ito, a colour-blind-safe categorical palette. Colour is chosen by what a
# chart encodes, never by rank: one aggregate series gets blue, nominal
# categories get fixed per-category hues, ordered levels get a Viridis ramp.
OKABE_ITO = {
    "black": "#000000",
    "orange": "#e69f00",
    "sky": "#56b4e9",
    "green": "#009e73",
    "yellow": "#f0e442",
    "blue": "#0072b2",
    "vermilion": "#d55e00",
    "purple": "#cc79a7",
}
UNKNOWN_COLOR = "#8c959f"
AGGREGATE_COLOR = OKABE_ITO["blue"]

# Fixed hue per token category, keyed by meaning rather than by current size, so
# "Input" stays blue whether or not cache read dwarfs it.
CATEGORY_COLORS = {
    "input_tokens": OKABE_ITO["blue"],
    "output_tokens": OKABE_ITO["green"],
    "cache_read_tokens": OKABE_ITO["sky"],
    "cache_write_tokens": OKABE_ITO["orange"],
    "reasoning_tokens": OKABE_ITO["purple"],
}

# Fallback hues for categorical series with no fixed colour of their own.
CATEGORICAL_COLORS = [
    OKABE_ITO["blue"], OKABE_ITO["vermilion"], OKABE_ITO["green"],
    OKABE_ITO["sky"], OKABE_ITO["orange"], OKABE_ITO["purple"],
    OKABE_ITO["yellow"], OKABE_ITO["black"],
]

TEMPLATE_NAME = "opencode"


def provider_colors(providers) -> dict:
    """Assign each provider a fixed hue, in alphabetical order.

    Keying colour by name rather than by rank keeps a provider's colour stable
    when the set of providers changes or the selection changes.
    """
    return {
        name: CATEGORICAL_COLORS[index % len(CATEGORICAL_COLORS)]
        for index, name in enumerate(sorted(providers))
    }


def plotly_template() -> str:
    """Register the shared Plotly template and return its name.

    Plotly's stock template paints a blue-grey plot area and cycles a bright
    categorical palette; both are overridden here so charts sit on the same
    white surface as the surrounding cards.
    """
    axis = dict(
        showgrid=True, gridcolor="#e5e7eb", gridwidth=1, zeroline=False,
        linecolor="#e5e7eb", ticks="outside", ticklen=4, tickcolor="#e5e7eb",
        tickfont=dict(size=11, color="#59636e"),
        title=dict(font=dict(size=12, color="#59636e")),
        automargin=True,
    )
    pio.templates[TEMPLATE_NAME] = go.layout.Template(
        layout=dict(
            font=dict(family="Segoe UI, sans-serif", size=12, color="#374151"),
            paper_bgcolor="#ffffff",
            plot_bgcolor="#ffffff",
            colorway=list(CATEGORICAL_COLORS),
            title=dict(font=dict(size=15, color="#111827"), x=0.5, xanchor="center"),
            xaxis=axis,
            yaxis=axis,
            legend=dict(
                font=dict(size=11, color="#59636e"),
                bgcolor="rgba(255,255,255,0)",
            ),
            hoverlabel=dict(bgcolor="#ffffff", font_size=12, font_family="Segoe UI, sans-serif"),
            margin=dict(t=48, b=56, l=64, r=24),
        )
    )
    return TEMPLATE_NAME

# Page stylesheet. Kept as a plain string rather than an f-string so the CSS
# braces do not need escaping, and so the design tokens live in one place.
PAGE_CSS = """
:root {
  --accent: #2f6feb;
  --accent-dark: #1a4fc4;
  --aggregate: #0072b2;
  --good: #1a7f37;
  --warn: #9a6700;
  --bg: #f4f4f4;
  --card-bg: #ffffff;
  --surface: #f6f8fb;
  --surface-hover: #eef1f6;
  --border: #e5e7eb;
  --text: #111827;
  --muted: #59636e;
  --shadow: none;
  --shadow-hover: 0 1px 2px rgba(20, 30, 50, 0.05);
}
* { box-sizing: border-box; }
body {
  font-family: "Segoe UI Variable", "Segoe UI", -apple-system, Roboto, system-ui, sans-serif;
  margin: 0; padding: 0; background: var(--bg); color: var(--text); line-height: 1.4;
  font-variant-numeric: tabular-nums;
}
.hero {
  background: var(--card-bg); padding: 20px 32px 14px; border-bottom: 1px solid var(--border);
}
.hero h1 { margin: 0 0 4px; font-size: 26px; font-weight: 650; letter-spacing: -0.01em; }
.hero .subtitle { color: var(--muted); margin: 0; font-size: 13px; }
.nav-pills { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 16px; }
.nav-pills a {
  color: var(--accent-dark); text-decoration: none; font-size: 13px; font-weight: 600;
  padding: 6px 12px; border-radius: 6px; background: var(--surface);
  border: 1px solid var(--border); transition: background 0.15s;
}
.nav-pills a:hover { background: #eef3ff; }
.nav-pills a:focus-visible { outline: 2px solid var(--accent-dark); outline-offset: 3px; }
.page { padding: 20px 32px 48px; max-width: 1600px; margin: auto; }
.section { margin-bottom: 8px; scroll-margin-top: 14px; }
.section-head { display: flex; align-items: baseline; gap: 10px; margin: 28px 0 10px; }
#sec-overview > .section-head { margin-top: 0; }
.section-head h2 { font-size: 19px; font-weight: 650; margin: 0; }
.section-desc { color: var(--muted); font-size: 13px; margin: 0 0 16px; max-width: 780px; }
.kpi-row {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 14px; margin-bottom: 12px;
}
/* Aggregate KPIs share one accent bar; chart palettes follow the encoded data type. */
.kpi {
  background: var(--card-bg); border: 1px solid var(--border);
  border-top: 3px solid var(--aggregate); border-radius: 10px;
  padding: 14px 18px; min-width: 0;
}
.kpi-label { font-size: 13px; color: var(--muted); margin-bottom: 4px; font-weight: 600; }
.kpi-value { font-size: 36px; font-weight: 700; letter-spacing: -0.01em; }
.kpi-note { font-size: 12px; color: var(--muted); margin-top: 6px; }
.scope-summary { color: var(--muted); font-size: 13px; margin-bottom: 12px; overflow-wrap: anywhere; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.card {
  background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px;
  padding: 14px; margin-bottom: 0; min-width: 0; box-shadow: var(--shadow);
  transition: box-shadow 0.15s;
}
.card:hover { box-shadow: var(--shadow-hover); }
.full { grid-column: 1 / -1; }
.footer-note {
  margin-top: 40px; padding-top: 18px; border-top: 1px solid var(--border);
  font-size: 12px; color: var(--muted);
}
.footer-note p { margin: 6px 0; }
@media (max-width: 1100px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
  .kpi-value { font-size: 30px; }
}
@media (max-width: 760px) {
  .hero { padding: 16px; }
  .hero h1 { font-size: 22px; }
  .page { padding: 16px; }
  .kpi-row { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .kpi { padding: 12px; }
  .kpi-value { font-size: 28px; }
  .card { padding: 10px; }
  .nav-pills a { min-height: 44px; display: inline-flex; align-items: center; }
}
"""

SECTIONS = [
    ("sec-overview", "Overview",
     "Recorded OpenCode usage totals, the day they were used, and how they "
     "split across projects and models. Cost is reported by OpenCode, not an invoice."),
    ("sec-composition", "Composition",
     "Which token categories make up the recorded usage, and how it splits "
     "across the providers that served it."),
]


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


def _fmt_tokens(value: float) -> str:
    """Format a token count compactly (e.g. 90858430 -> 90.9M)."""
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"{value / limit:,.1f}{suffix}"
    return f"{value:,.0f}"


def _fmt_cost(value: float) -> str:
    """Format a USD estimate, keeping precision for sub-cent totals."""
    if value == 0:
        return "$0.00"
    if abs(value) < 0.01:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def build_dashboard(data: pd.DataFrame, out_path: Path) -> None:
    tokens = int(data["total_tokens"].sum())
    cost = float(data["cost_usd"].sum())
    calls = int(data["calls"].sum())
    sessions = data["session_id"].nunique() if "session_id" in data else None
    cache_read = int(data["cache_read_tokens"].sum())

    daily = data.groupby("date", as_index=False).agg(
        total_tokens=("total_tokens", "sum"), cost_usd=("cost_usd", "sum")
    )
    composition = data[[
        "input_tokens", "output_tokens", "cache_read_tokens",
        "cache_write_tokens", "reasoning_tokens",
    ]].sum().rename_axis("category").reset_index(name="tokens")
    composition["label"] = composition["category"].map(CATEGORY_LABELS)
    # Cache read is a raw counter that can dwarf input+output, so it is charted
    # separately; otherwise it flattens every other category to a hairline.
    composition_main = composition[
        ~composition["category"].eq("cache_read_tokens")
    ]
    by_model = data.groupby("model", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False).head(TOP_N)
    model_tokens = int(data["total_tokens"].sum())
    model_shown = int(by_model["total_tokens"].sum())
    by_project = data.groupby("project", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False).head(TOP_N)
    by_provider = data.groupby("provider", as_index=False).agg(
        total_tokens=("total_tokens", "sum")
    ).sort_values("total_tokens", ascending=False)

    template = plotly_template()

    token_chart = px.line(
        daily, x="date", y="total_tokens", markers=True, title="Tokens by day",
        template=template,
        labels={"date": "Date", "total_tokens": "Input + output tokens"},
    )
    cost_chart = px.line(
        daily, x="date", y="cost_usd", markers=True, title="Recorded cost by day",
        template=template,
        labels={"date": "Date", "cost_usd": "Recorded cost (USD)"},
    )
    composition_chart = px.bar(
        composition_main, x="label", y="tokens",
        labels={"label": "Token category", "tokens": "Tokens"},
        title="Token categories (input, output, reasoning)",
        text="tokens", template=template,
    )
    model_chart = px.bar(
        by_model, x="total_tokens", y="model", orientation="h",
        labels={"total_tokens": "Input + output tokens", "model": ""},
        title=f"Top {len(by_model)} models by tokens",
        text="total_tokens", template=template,
    )
    project_chart = px.bar(
        by_project, x="total_tokens", y="project", orientation="h",
        labels={"total_tokens": "Input + output tokens", "project": ""},
        title=f"Top {len(by_project)} projects by tokens",
        text="total_tokens", template=template,
    )
    provider_chart = px.pie(
        by_provider, names="provider", values="total_tokens", hole=0.45,
        title="Tokens by provider", template=template,
        labels={"provider": "Provider", "total_tokens": "Tokens"},
    )

    # Aggregate charts carry a single series, so they share one blue.
    for single_series in (token_chart, cost_chart):
        single_series.update_traces(
            line=dict(color=AGGREGATE_COLOR, width=2.5),
            marker=dict(color=AGGREGATE_COLOR, size=6),
            fill="tozeroy",
            fillcolor="rgba(0, 114, 178, 0.10)",
            hovertemplate="%{x}<br>%{y:,}<extra></extra>",
        )
    for ranking in (model_chart, project_chart):
        ranking.update_traces(
            marker=dict(color=AGGREGATE_COLOR),
            texttemplate="%{text:,.0f}", textposition="outside", cliponaxis=False,
            textfont=dict(color="#374151", size=11),
            hovertemplate="%{y}<br>%{x:,} tokens<extra></extra>",
        )
    composition_chart.update_traces(
        marker=dict(color=[
            CATEGORY_COLORS.get(category, UNKNOWN_COLOR)
            for category in composition_main["category"]
        ]),
        texttemplate="%{text:,}", textposition="outside", cliponaxis=False,
        textfont=dict(color="#374151", size=11),
        hovertemplate="%{x}<br>%{y:,} tokens<extra></extra>",
    )
    providers = provider_colors(by_provider["provider"])
    provider_chart.update_traces(
        marker=dict(
            colors=[providers[name] for name in by_provider["provider"]],
            line=dict(width=0),
        ),
        texttemplate="%{percent}<br>%{value:,.0f}",
        hovertemplate="%{label}<br>%{value:,} tokens (%{percent})<extra></extra>",
    )
    provider_chart.update_layout(
        legend=dict(
            orientation="h", yanchor="top", y=-0.05, xanchor="center", x=0.5,
            title=dict(text=""),
        )
    )
    model_chart.update_layout(yaxis={"categoryorder": "total ascending"})
    project_chart.update_layout(yaxis={"categoryorder": "total ascending"})

    # Dates arrive as datetime.date from load_data, but callers may pass raw strings.
    dates = sorted(pd.Timestamp(value).date() for value in data["date"].unique())
    latest = dates[-1].isoformat()
    first = dates[0].isoformat()
    span = f"{first} to {latest}" if len(dates) > 1 else first
    users = data["user"].nunique()
    scope_summary = (
        f"{span} &middot; {data['project'].nunique()} projects &middot; "
        f"{data['model'].nunique()} models &middot; {users} user"
        f"{'s' if users != 1 else ''}"
    )
    subtitle = (
        f"Data through {latest} &middot; "
        f"generated {datetime.now():%Y-%m-%d %H:%M} &middot; local session-store exports"
    )
    hidden_models = data["model"].nunique() - len(by_model)

    figures = [
        (token_chart, 380),
        (cost_chart, 380),
        (composition_chart, 340),
        (model_chart, max(260, 34 * len(by_model) + 120)),
        (project_chart, max(260, 34 * len(by_project) + 120)),
        (provider_chart, 380),
    ]
    for fig, height in figures:
        fig.update_layout(height=height)

    def card(figure_html: str, full: bool = False) -> str:
        css = "card full" if full else "card"
        return f'<div class="{css}">{figure_html}</div>'

    first_plot = True

    def plot(figure, full: bool = False) -> str:
        nonlocal first_plot
        html_fragment = pio.to_html(
            figure,
            full_html=False,
            include_plotlyjs=first_plot,
            config={"responsive": True, "displaylogo": False},
        )
        first_plot = False
        return card(html_fragment, full)

    kpis = [("Input + output tokens", f"{tokens:,}", None),
            ("Recorded cost (USD)", _fmt_cost(cost), "Reported by OpenCode, not an invoice"),
            ("Assistant messages", f"{calls:,}", None)]
    if sessions is not None:
        kpis.append(("Sessions", f"{sessions:,}", None))
    kpis.append((
        "Cache read tokens", f"{cache_read:,}",
        "Independent counter, not part of the total above",
    ))
    kpi_html = "".join(
        f'<div class="kpi"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div>'
        + (f'<div class="kpi-note">{note}</div>' if note else "")
        + "</div>"
        for label, value, note in kpis
    )

    nav_html = "".join(
        f'<a href="#{anchor}">{name}</a>' for anchor, name, _ in SECTIONS
    )
    sections_html = []
    for anchor, name, desc in SECTIONS:
        if anchor == "sec-overview":
            body_html = (
                f'<div class="kpi-row">{kpi_html}</div>'
                f'<p class="scope-summary">{scope_summary}</p>'
                f'<div class="grid">'
                f"{plot(token_chart, full=True)}{plot(cost_chart, full=True)}"
                f"{plot(project_chart, full=True)}{plot(model_chart, full=True)}"
                "</div>"
            )
        else:
            body_html = (
                f'<div class="grid">'
                f"{plot(composition_chart)}{plot(provider_chart)}"
                "</div>"
            )
        sections_html.append(
            f'<section class="section" id="{anchor}">'
            f'<div class="section-head"><h2>{name}</h2></div>'
            f'<p class="section-desc">{desc}</p>{body_html}</section>'
        )

    notes = [
        "<p>Cache read is an independent counter reported separately from input "
        "and output; it is not part of the input + output total above.</p>"
    ]
    if hidden_models > 0:
        notes.append(
            f"<p>The model ranking shows the top {len(by_model)} of "
            f"{data['model'].nunique()} models, covering {model_shown:,} of "
            f"{model_tokens:,} tokens ({model_shown / model_tokens:.1%}).</p>"
        )

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OpenCode Usage Dashboard</title>
<style>{PAGE_CSS}</style></head><body>
<div class="hero">
<h1>OpenCode Usage Dashboard</h1>
<p class="subtitle">{subtitle}</p>
<nav class="nav-pills">{nav_html}</nav>
</div>
<div class="page">
{"".join(sections_html)}
<footer class="footer-note">{"".join(notes)}</footer>
</div>
</body></html>"""
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
