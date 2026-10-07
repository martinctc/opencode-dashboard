#!/usr/bin/env python3
"""Build a self-contained HTML dashboard from OpenCode usage CSV exports."""

import argparse
import glob
import html
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs


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

# Donut slices below this share lose their in-place label: at small angles the
# text overlaps its neighbours and is unreadable either way.
MIN_LABEL_SHARE = 0.05

# A cost or efficiency ranking needs more than one qualifying model to be a
# chart rather than a stray bar. Below these thresholds the section states what
# it can actually prove instead.
MIN_COST_MODELS = 2
MIN_CALLS_FOR_EFFICIENCY = 5

# Viridis samples for ordered levels: reasoning effort runs purple (low) to
# yellow (high). Unrecorded effort is grey so it reads as a catch-all.
EFFORT_COLORS = {
    "none": "#440154",
    "low": "#31688e",
    "medium": "#35b779",
    "high": "#fde725",
}

CATEGORY_EXPLAINERS = {
    "Input": "Tokens sent to the model, including the prompt and any context "
             "OpenCode attached. This is part of the headline total.",
    "Output": "Tokens the model generated in reply. This is part of the "
              "headline total.",
    "Cache read": "Tokens OpenCode re-read from the provider's prompt cache "
                  "instead of resending. Usually far larger than the headline "
                  "total, because repeated turns re-send the same context. "
                  "Cheap or free, but not part of input + output.",
    "Cache write": "Tokens written into the prompt cache for later reuse. "
                   "Zero when the provider does not report it.",
    "Reasoning": "Tokens a reasoning model spent thinking before answering. "
                 "Counted separately from output, so reasoning-heavy models "
                 "look cheaper than they are.",
}

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
# Unrecorded or unrecognised reasoning effort reads as grey, never as a ramp step.
EFFORT_COLORS["n/a"] = UNKNOWN_COLOR

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
.insight-bar { display: flex; flex-direction: column; gap: 8px; margin: 0 0 14px; }
.insight {
  display: flex; gap: 10px; align-items: flex-start; font-size: 13px;
  background: #eef4ff; border-left: 3px solid var(--accent);
  border-radius: 6px; padding: 9px 12px; color: #1b3a7a;
}
.insight.warn { background: #fff8e6; border-left-color: var(--warn); color: #6b4d00; }
.insight.good { background: #edf9f0; border-left-color: var(--good); color: #14532d; }
.insight b { font-weight: 700; }
.empty-state {
  font-size: 13px; color: var(--muted); background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px;
}
.empty-state b { color: var(--text); font-weight: 650; }
.glossary-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 14px; margin: 0 0 14px;
}
.glossary-item {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 10px 12px; margin: 0;
}
.glossary-item.highlight { background: #eef3ff; border-color: #c9dcff; }
.glossary-item dt { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
.glossary-item dd { margin: 0; font-size: 12.5px; color: var(--muted); line-height: 1.45; }
details.explain { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
details.explain summary { cursor: pointer; font-size: 14px; font-weight: 650; color: var(--muted); }
details.explain[open] summary { margin-bottom: 14px; }
details.explain summary:focus-visible { outline: 2px solid var(--accent-dark); outline-offset: 3px; }
.explain-note { font-size: 12px; color: var(--muted); margin: 12px 0 0; }
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
    ("sec-cost", "Cost &amp; pricing efficiency",
     "Where recorded cost concentrates, and which models return the most "
     "tokens per dollar spent. Value here means pricing efficiency, not output "
     "quality."),
    ("sec-composition", "Composition",
     "Which token categories make up the recorded usage, how it splits across "
     "the providers that served it, and which reasoning effort was configured."),
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


def _escape(value) -> str:
    """Escape a value from the data before interpolating it into HTML.

    Project and model names come from disk; without this a name containing
    markup would be injected into the page.
    """
    return html.escape(str(value), quote=True)


def _label_peak(figure, frame: pd.DataFrame, value_column: str, unit: str) -> None:
    """Annotate the largest point so the headline day is readable without hovering."""
    if frame.empty:
        return
    peak = frame.loc[frame[value_column].idxmax()]
    formatter = _fmt_cost if unit == "cost" else _fmt_tokens
    figure.add_annotation(
        x=peak["date"],
        y=peak[value_column],
        text=f"<b>{formatter(peak[value_column])}</b>",
        showarrow=False,
        yshift=16,
        font=dict(size=11, color="#59636e"),
    )


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

    # Cost is optional: providers only report it for billable models, so most
    # rows can carry a genuine zero. Coverage is tracked separately from total.
    by_model_cost = data.groupby("model", as_index=False).agg(
        cost_usd=("cost_usd", "sum"),
        total_tokens=("total_tokens", "sum"),
        calls=("calls", "sum"),
    )
    costed = by_model_cost[by_model_cost["cost_usd"] > 0].sort_values(
        "cost_usd", ascending=False
    )
    free_model_count = len(by_model_cost) - len(costed)
    cost_rows = int((data["cost_usd"] > 0).sum())
    cost_coverage = cost_rows / len(data) if len(data) else 0.0
    costed["label"] = [_fmt_cost(value) for value in costed["cost_usd"]]
    efficiency = costed[costed["calls"] >= MIN_CALLS_FOR_EFFICIENCY].copy()
    efficiency["tokens_per_dollar"] = (
        efficiency["total_tokens"] / efficiency["cost_usd"]
    )
    efficiency = efficiency.sort_values("tokens_per_dollar", ascending=False)
    efficiency["label"] = [
        f"{value:,.0f} tok/$" for value in efficiency["tokens_per_dollar"]
    ]

    # load_data() supplies `variant`; a caller passing a bare frame may not.
    if "variant" in data:
        effort = (
            data.groupby("variant", as_index=False)
            .agg(total_tokens=("total_tokens", "sum"), calls=("calls", "sum"))
            .sort_values("total_tokens", ascending=False)
        )
    else:
        effort = pd.DataFrame({"variant": [], "total_tokens": [], "calls": []})

    # Compact labels for the in-chart text; exact values stay available on hover.
    by_model["label"] = [_fmt_tokens(value) for value in by_model["total_tokens"]]
    by_project["label"] = [_fmt_tokens(value) for value in by_project["total_tokens"]]
    composition_main["label_value"] = [
        _fmt_tokens(value) for value in composition_main["tokens"]
    ]

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
        composition_main, x="label", y="tokens", text="label_value",
        labels={"label": "Token category", "tokens": "Tokens"},
        title="Token categories (input, output, reasoning)",
        template=template,
    )
    model_chart = px.bar(
        by_model, x="total_tokens", y="model", orientation="h", text="label",
        labels={"total_tokens": "Input + output tokens", "model": ""},
        title=f"Top {len(by_model)} models by tokens",
        template=template,
    )
    project_chart = px.bar(
        by_project, x="total_tokens", y="project", orientation="h", text="label",
        labels={"total_tokens": "Input + output tokens", "project": ""},
        title=f"Top {len(by_project)} projects by tokens",
        template=template,
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
            texttemplate="%{text}", textposition="outside", cliponaxis=False,
            textfont=dict(color="#374151", size=11),
            hovertemplate="%{y}<br>%{x:,} tokens<extra></extra>",
        )
    composition_chart.update_traces(
        marker=dict(color=[
            CATEGORY_COLORS.get(category, UNKNOWN_COLOR)
            for category in composition_main["category"]
        ]),
        texttemplate="%{text}", textposition="outside", cliponaxis=False,
        textfont=dict(color="#374151", size=11),
        hovertemplate="%{x}<br>%{y:,} tokens<extra></extra>",
    )
    providers = provider_colors(by_provider["provider"])
    provider_total = by_provider["total_tokens"].sum()
    # Slices below MIN_LABEL_SHARE collide with their neighbours and are
    # unreadable, so they are left to the legend and hover instead.
    provider_chart.update_traces(
        text=[
            f"{share:.1%}<br>{_fmt_tokens(value)}"
            if share >= MIN_LABEL_SHARE else ""
            for share, value in zip(
                by_provider["total_tokens"] / provider_total,
                by_provider["total_tokens"],
            )
        ],
        texttemplate="%{text}",
        textposition="inside",
        textfont=dict(color="#ffffff", size=11),
        marker=dict(
            colors=[providers[name] for name in by_provider["provider"]],
            line=dict(width=0),
        ),
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
    _label_peak(token_chart, daily, "total_tokens", "tokens")
    _label_peak(cost_chart, daily, "cost_usd", "cost")

    cost_chart_by_model = px.bar(
        costed, x="cost_usd", y="model", orientation="h", text="label",
        labels={"cost_usd": "Estimated cost (USD)", "model": ""},
        title="Estimated cost by model", template=template,
    )
    cost_chart_by_model.update_traces(
        marker=dict(color=AGGREGATE_COLOR),
        texttemplate="%{text}", textposition="outside", cliponaxis=False,
        textfont=dict(color="#374151", size=11),
        hovertemplate="%{y}<br>$%{x:,.4f}<extra></extra>",
    )
    cost_chart_by_model.update_layout(yaxis={"categoryorder": "total ascending"})

    efficiency_chart = px.bar(
        efficiency, x="tokens_per_dollar", y="model", orientation="h",
        text="label",
        labels={
            "tokens_per_dollar": "Tokens per estimated USD (confirmed calls)",
            "model": "",
        },
        title=f"Pricing efficiency by model ({MIN_CALLS_FOR_EFFICIENCY}+ calls)",
        template=template,
    )
    efficiency_chart.update_traces(
        marker=dict(color=AGGREGATE_COLOR),
        texttemplate="%{text}", textposition="outside", cliponaxis=False,
        textfont=dict(color="#374151", size=11),
        hovertemplate="%{y}<br>%{x:,.0f} tokens per $<extra></extra>",
    )
    efficiency_chart.update_layout(yaxis={"categoryorder": "total ascending"})

    effort_chart = px.bar(
        effort, x="variant", y="total_tokens", text="total_tokens",
        labels={"variant": "Configured reasoning effort", "total_tokens": "Input + output tokens"},
        title="Tokens by reasoning effort", template=template,
    )
    effort_chart.update_traces(
        marker=dict(color=[
            EFFORT_COLORS.get(str(value), UNKNOWN_COLOR) for value in effort["variant"]
        ]),
        texttemplate="%{text:,.0f}", textposition="outside", cliponaxis=False,
        textfont=dict(color="#374151", size=11),
        hovertemplate="%{x}<br>%{y:,} tokens<extra></extra>",
    )

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

    def plot(figure, full: bool = False) -> str:
        # plotly.js is emitted once in the head, so no figure is responsible for
        # carrying it. That keeps rendering independent of section order.
        return card(
            pio.to_html(
                figure,
                full_html=False,
                include_plotlyjs=False,
                config={"responsive": True, "displaylogo": False},
            ),
            full,
        )

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

    # Observations are derived from the data rather than written by hand, so
    # they stay true as the exports change.
    insights = []
    if not by_project.empty and model_tokens:
        lead_project = by_project.iloc[0]
        project_share = lead_project["total_tokens"] / model_tokens
        insights.append((
            "info",
            f"<b>{_escape(lead_project['project'])}</b> accounts for "
            f"<b>{project_share:.0%}</b> of recorded tokens.",
        ))
    if not by_model.empty and model_tokens:
        lead_model = by_model.iloc[0]
        insights.append((
            "info",
            f"Leading model: <b>{_escape(lead_model['model'])}</b> at "
            f"<b>{lead_model['total_tokens'] / model_tokens:.0%}</b> of tokens.",
        ))
    if cache_read and tokens:
        ratio = cache_read / tokens
        if ratio >= 1:
            insights.append((
                "info",
                f"Cache reads are <b>{ratio:.0f}x</b> the input + output total. "
                "Most work is re-reading context, not generating it.",
            ))
    if cost_coverage < 1:
        insights.append((
            "warn" if cost_coverage == 0 else "info",
            f"Cost was recorded for <b>{cost_rows} of {len(data)}</b> exported "
            f"rows ({cost_coverage:.1%}). Providers only report cost for "
            f"billable models; <b>{free_model_count}</b> of "
            f"{len(by_model_cost)} models recorded none.",
        ))

    def insight_bar(items) -> str:
        if not items:
            return ""
        rows = "".join(
            f'<div class="insight {kind}"><div>{text}</div></div>'
            for kind, text in items
        )
        return f'<div class="insight-bar">{rows}</div>'

    def empty_state(message: str) -> str:
        return f'<div class="card"><div class="empty-state">{message}</div></div>'

    def glossary_html() -> str:
        items = "".join(
            f'<dl class="glossary-item'
            f'{" highlight" if name == "Cache read" else ""}">'
            f"<dt>{name}</dt><dd>{body}</dd></dl>"
            for name, body in CATEGORY_EXPLAINERS.items()
        )
        return (
            '<details class="explain"><summary>What do these token '
            f'categories mean?</summary><div class="glossary-grid">{items}</div>'
            "<p class=\"explain-note\">"
            "Cache read and reasoning are tracked as independent counters, so "
            "they are not part of the input + output headline.</p></details>"
        )

    if len(costed) >= MIN_COST_MODELS:
        cost_body = f'<div class="grid">{plot(cost_chart_by_model, full=True)}'
        if not efficiency.empty:
            cost_body += plot(efficiency_chart)
        cost_body += "</div>"
    else:
        cost_body = (
            '<div class="grid">'
            + empty_state(
                f"<b>Not enough priced usage to rank yet.</b> Cost was recorded "
                f"for <b>{len(costed)} of {len(by_model_cost)}</b> models "
                f"({cost_rows} of {len(data)} rows, {cost_coverage:.1%} coverage). "
                f"The remaining {free_model_count} models recorded no cost, which "
                "usually means a free tier or an unreported provider. Both "
                "charts appear here once at least "
                f"{MIN_COST_MODELS} models carry cost."
            )
            + "</div>"
        )

    # A single non-default variant is not a comparison. Only render the chart
    # once at least two variants actually carry tokens.
    if int((effort["total_tokens"] > 0).sum()) >= 2:
        effort_body = f'<div class="grid">{plot(effort_chart)}</div>'
    else:
        effort_body = (
            '<div class="grid full">'
            + empty_state(
                "<b>No reasoning effort to compare.</b> Every message in this "
                "export used the provider default, so there is nothing to rank. "
                "The chart appears once two or more effort levels are recorded."
            )
            + "</div>"
        )

    section_bodies = {
        "sec-overview": (
            f'<div class="kpi-row">{kpi_html}</div>'
            f'<p class="scope-summary">{scope_summary}</p>'
            f"{insight_bar(insights)}"
            f'<div class="grid">'
            f"{plot(token_chart, full=True)}{plot(cost_chart, full=True)}"
            f"{plot(project_chart)}{plot(model_chart)}"
            "</div>"
        ),
        "sec-cost": cost_body,
        "sec-composition": (
            f'<div class="grid">'
            f"{plot(composition_chart)}{plot(provider_chart)}"
            "</div>"
            f"{effort_body}"
            f'{glossary_html()}'
        ),
    }

    sections_html = []
    for anchor, name, desc in SECTIONS:
        sections_html.append(
            f'<section class="section" id="{anchor}">'
            f'<div class="section-head"><h2>{name}</h2></div>'
            f'<p class="section-desc">{desc}</p>'
            f"{section_bodies[anchor]}</section>"
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
<style>{PAGE_CSS}</style>
<script>{get_plotlyjs()}</script></head><body>
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
