#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation.
# Copyright (c) 2026 OpenCode Usage Dashboard contributors.
# Licensed under the MIT License.
#
# Derived from microsoft/ghc-cli-dashboard. See README.md for attribution.
"""Build a self-contained HTML dashboard from OpenCode usage CSV exports."""

import argparse
import glob
import html
import json
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs
from plotly.utils import PlotlyJSONEncoder


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

# Fraction of the value axis left empty past the largest bar, so its outside
# label has somewhere to sit.
LABEL_HEADROOM = 0.18

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
.plot-slot { min-height: 1px; }
[hidden] { display: none !important; }
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


def _json_for_script(value) -> str:
    """Serialise a value for embedding inside a <script> element.

    A `</script>` sequence inside a JSON string would close the tag early and
    `<!--` can start an HTML comment, so angle brackets and ampersands are
    emitted as escapes that JSON.parse still reads back unchanged.

    PlotlyJSONEncoder is used rather than the stock encoder because the shared
    template contains numpy scalars, which would otherwise serialise as strings.
    """
    return (
        json.dumps(value, cls=PlotlyJSONEncoder, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# The client render layer. Charts are built here rather than in Python so that
# filters and date ranges can re-slice the embedded rows and re-render, without
# a round trip or a rebuild. Kept as a plain string so JS braces do not need
# escaping; the page shell injects the data it needs via window.__DASHBOARD__.
CLIENT_JS = r"""
(function () {
  "use strict";

  var D = window.__DASHBOARD__;
  var CFG = D.config;
  var BASE = CFG.template.layout;
  var SUM_FIELDS = ["total_tokens", "cost_usd", "calls", "input_tokens",
                    "output_tokens", "cache_read_tokens", "cache_write_tokens",
                    "reasoning_tokens"];

  // Phase 5c filters mutate this. Until then it is simply every row.
  var state = { rows: D.rows };

  function $(id) { return document.getElementById(id); }

  function esc(value) {
    return String(value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function group(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function fmtTokens(value) {
    var v = Math.abs(value);
    if (v >= 1e9) { return (value / 1e9).toFixed(1) + "B"; }
    if (v >= 1e6) { return (value / 1e6).toFixed(1) + "M"; }
    if (v >= 1e3) { return (value / 1e3).toFixed(1) + "K"; }
    return group(Math.round(value));
  }

  function fmtCost(value) {
    if (value === 0) { return "$0.00"; }
    if (Math.abs(value) < 0.01) { return "$" + value.toFixed(4); }
    return "$" + value.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function fmtCostBar(value) {
    if (value === 0) { return "$0.00"; }
    if (Math.abs(value) < 0.01) { return "$" + value.toFixed(4); }
    return "$" + value.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function bump(map, key, row) {
    var entry = map.get(key);
    if (!entry) {
      entry = { key: key };
      for (var i = 0; i < SUM_FIELDS.length; i++) { entry[SUM_FIELDS[i]] = 0; }
      map.set(key, entry);
    }
    for (var j = 0; j < SUM_FIELDS.length; j++) {
      entry[SUM_FIELDS[j]] += Number(row[SUM_FIELDS[j]]) || 0;
    }
    return entry;
  }

  function byTokensDesc(map) {
    return Array.from(map.values()).sort(function (a, b) {
      return b.total_tokens - a.total_tokens;
    });
  }

  function axis(base, extra) {
    var merged = {};
    var source = BASE[base] || {};
    for (var k in source) { if (Object.prototype.hasOwnProperty.call(source, k)) { merged[k] = source[k]; } }
    for (var j in extra) { if (Object.prototype.hasOwnProperty.call(extra, j)) { merged[j] = extra[j]; } }
    return merged;
  }

  function layout(extra, height) {
    var out = {};
    for (var k in BASE) { if (Object.prototype.hasOwnProperty.call(BASE, k)) { out[k] = BASE[k]; } }
    out.height = height;
    return Object.assign(out, extra || {});
  }

  // Plotly does not reserve room for textposition="outside" when scaling an
  // axis, so the label on the longest bar would run past the card edge.
  function paddedRange(maximum) {
    if (!maximum || maximum <= 0) { return undefined; }
    return [0, maximum * (1 + CFG.labelHeadroom)];
  }

  function maxOf(entries, field) {
    var best = 0;
    for (var i = 0; i < entries.length; i++) {
      if (entries[i][field] > best) { best = entries[i][field]; }
    }
    return best;
  }

  function subtitleHtml(d) {
    var through = d.daily.length
      ? d.daily[d.daily.length - 1].key : "no dated usage";
    return "Data through " + through + " &middot; generated " +
      CFG.generatedAt + " &middot; local session-store exports";
  }

  function aggregate(rows) {
    var totals = { tokens: 0, cost: 0, calls: 0, cacheRead: 0 };
    var categories = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0,
                       cache_write_tokens: 0, reasoning_tokens: 0 };
    var daily = new Map(), models = new Map(), projects = new Map(),
        providers = new Map(), efforts = new Map();
    var sessions = new Set();
    var costRows = 0;

    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      totals.tokens += r.total_tokens;
      totals.cost += r.cost_usd;
      totals.calls += r.calls;
      totals.cacheRead += r.cache_read_tokens;
      if (r.cost_usd > 0) { costRows += 1; }
      sessions.add(r.session_id);
      for (var k in categories) { categories[k] += r[k] || 0; }
      bump(daily, r.date, r);
      bump(models, r.model, r);
      bump(projects, r.project, r);
      bump(providers, r.provider, r);
      bump(efforts, r.variant || "n/a", r);
    }

    var allModels = byTokensDesc(models);
    var costed = modelsBy(costRows, models);

    return {
      rows: rows,
      totals: totals,
      categories: categories,
      sessions: sessions.size,
      costRows: costRows,
      daily: Array.from(daily.values()).sort(function (a, b) {
        return a.key < b.key ? -1 : (a.key > b.key ? 1 : 0);
      }),
      models: allModels,
      modelCount: allModels.length,
      projects: byTokensDesc(projects),
      projectCount: projects.size,
      providers: byTokensDesc(providers),
      costed: costed,
      freeModelCount: allModels.length - costed.length,
      efforts: byTokensDesc(efforts)
    };
  }

  function modelsBy(_costRows, models) {
    var out = [];
    models.forEach(function (entry) {
      if (entry.cost_usd > 0) { out.push(entry); }
    });
    return out.sort(function (a, b) { return b.cost_usd - a.cost_usd; });
  }

  function providerColors(providers) {
    var names = providers.map(function (p) { return p.key; }).sort();
    var colors = {};
    names.forEach(function (name, index) {
      colors[name] = CFG.categoricalColors[index % CFG.categoricalColors.length];
    });
    return colors;
  }

  function kpiHtml(d) {
    var items = [
      ["Input + output tokens", group(d.totals.tokens), null],
      ["Recorded cost (USD)", fmtCost(d.totals.cost),
       "Reported by OpenCode, not an invoice"],
      ["Assistant messages", group(d.totals.calls), null],
      ["Sessions", group(d.sessions), null],
      ["Cache read tokens", group(d.totals.cacheRead),
       "Independent counter, not part of the total above"]
    ];
    return items.map(function (item) {
      return '<div class="kpi"><div class="kpi-label">' + esc(item[0]) +
        '</div><div class="kpi-value">' + esc(item[1]) + "</div>" +
        (item[2] ? '<div class="kpi-note">' + esc(item[2]) + "</div>" : "") +
        "</div>";
    }).join("");
  }

  function insightHtml(d) {
    var out = [];
    if (d.projects.length && d.totals.tokens) {
      var lead = d.projects[0];
      out.push(["info", "<b>" + esc(lead.key) + "</b> accounts for <b>" +
        (lead.total_tokens / d.totals.tokens * 100).toFixed(0) +
        "%</b> of recorded tokens."]);
    }
    if (d.models.length && d.totals.tokens) {
      var lm = d.models[0];
      out.push(["info", "Leading model: <b>" + esc(lm.key) + "</b> at <b>" +
        (lm.total_tokens / d.totals.tokens * 100).toFixed(0) +
        "%</b> of tokens."]);
    }
    if (d.totals.cacheRead && d.totals.tokens) {
      var ratio = d.totals.cacheRead / d.totals.tokens;
      if (ratio >= 1) {
        out.push(["info", "Cache reads are <b>" + ratio.toFixed(0) +
          "x</b> the input + output total. Most work is re-reading context, " +
          "not generating it."]);
      }
    }
    if (d.rows.length && d.costRows < d.rows.length) {
      out.push([d.costRows === 0 ? "warn" : "info",
        "Cost was recorded for <b>" + group(d.costRows) + " of " +
        group(d.rows.length) + "</b> exported rows (" +
        (d.costRows / d.rows.length * 100).toFixed(1) +
        "%). Providers only report cost for billable models; <b>" +
        d.freeModelCount + "</b> of " + d.modelCount + " models recorded none."]);
    }
    return out.map(function (item) {
      return '<div class="insight ' + item[0] + '"><div>' + item[1] + "</div></div>";
    }).join("");
  }

  function scopeHtml(d) {
    var dates = d.daily.map(function (row) { return row.key; });
    if (!dates.length) { return "No dated usage available"; }
    var span = dates.length > 1 ? dates[0] + " to " + dates[dates.length - 1] : dates[0];
    var users = CFG.userCount;
    return span + " &middot; " + d.projectCount + " projects &middot; " +
      d.modelCount + " models &middot; " + users + " user" + (users === 1 ? "" : "s");
  }

  function trendSpec(entries, field, unit) {
    if (!entries.length) { return null; }
    var maxEntry = entries[0];
    for (var i = 1; i < entries.length; i++) {
      if (entries[i][field] > maxEntry[field]) { maxEntry = entries[i]; }
    }
    return {
      traces: [{
        type: "scatter", mode: "lines+markers",
        x: entries.map(function (e) { return e.key; }),
        y: entries.map(function (e) { return e[field]; }),
        line: { color: CFG.aggregateColor, width: 2.5 },
        marker: { color: CFG.aggregateColor, size: 6 },
        fill: "tozeroy", fillcolor: "rgba(0, 114, 178, 0.10)",
        hovertemplate: "%{x}<br>%{y:,}<extra></extra>"
      }],
      layout: layout({
        title: { text: unit === "cost" ? "Recorded cost by day" : "Tokens by day" },
        xaxis: axis("xaxis", { type: "date" }),
        yaxis: axis("yaxis", { title: { text: unit === "cost"
          ? "Recorded cost (USD)" : "Input + output tokens" } }),
        annotations: [{
          x: maxEntry.key, y: maxEntry[field],
          text: "<b>" + (unit === "cost" ? fmtCost(maxEntry[field])
                                        : fmtTokens(maxEntry[field])) + "</b>",
          showarrow: false, yshift: 16,
          font: { size: 11, color: "#59636e" }
        }]
      }, 380)
    };
  }

  function compositionSpec(d) {
    var keep = ["input_tokens", "output_tokens", "cache_write_tokens",
                "reasoning_tokens"];
    var labels = [], values = [], colors = [], best = 0;
    for (var i = 0; i < keep.length; i++) {
      labels.push(CFG.categoryLabels[keep[i]]);
      values.push(d.categories[keep[i]]);
      colors.push(CFG.categoryColors[keep[i]]);
      if (d.categories[keep[i]] > best) { best = d.categories[keep[i]]; }
    }
    return {
      traces: [{
        type: "bar", x: labels, y: values,
        text: values.map(function (v) { return group(v); }),
        texttemplate: "%{text}", textposition: "outside", cliponaxis: false,
        textfont: { color: "#374151", size: 11 },
        marker: { color: colors },
        hovertemplate: "%{x}<br>%{y:,} tokens<extra></extra>"
      }],
      layout: layout({
        title: { text: "Token categories (input, output, reasoning)" },
        xaxis: axis("xaxis", { title: { text: "Token category" } }),
        yaxis: axis("yaxis", { title: { text: "Tokens" }, range: paddedRange(best) })
      }, 340)
    };
  }

  function rankingSpec(entries, title, noun) {
    var n = Math.min(entries.length, CFG.topN);
    var slice = entries.slice(0, n);
    if (!slice.length) { return null; }
    var values = slice.map(function (e) { return e.total_tokens; });
    return {
      traces: [{
        type: "bar", orientation: "h",
        x: values, y: slice.map(function (e) { return e.key; }),
        text: values.map(function (v) { return fmtTokens(v); }),
        texttemplate: "%{text}", textposition: "outside", cliponaxis: false,
        textfont: { color: "#374151", size: 11 },
        marker: { color: CFG.aggregateColor },
        hovertemplate: "%{y}<br>%{x:,} tokens<extra></extra>"
      }],
      layout: layout({
        title: { text: title },
        xaxis: axis("xaxis", { title: { text: "Input + output tokens" },
                                range: paddedRange(Math.max.apply(null, values)) }),
        yaxis: axis("yaxis", { categoryorder: "total ascending",
                               title: { text: noun } })
      }, Math.max(260, 34 * n + 120))
    };
  }

  function providerSpec(d) {
    if (!d.providers.length) { return null; }
    var colors = providerColors(d.providers);
    var total = d.providers.reduce(function (acc, p) { return acc + p.total_tokens; }, 0);
    var texts = d.providers.map(function (p) {
      var share = total ? p.total_tokens / total : 0;
      return share >= CFG.minLabelShare
        ? (share * 100).toFixed(1) + "%<br>" + fmtTokens(p.total_tokens) : "";
    });
    return {
      traces: [{
        type: "pie", hole: 0.45,
        labels: d.providers.map(function (p) { return p.key; }),
        values: d.providers.map(function (p) { return p.total_tokens; }),
        text: texts, texttemplate: "%{text}", textposition: "inside",
        textfont: { color: "#ffffff", size: 11 },
        marker: { colors: d.providers.map(function (p) { return colors[p.key]; }),
                  line: { width: 0 } },
        hovertemplate: "%{label}<br>%{value:,} tokens (%{percent})<extra></extra>"
      }],
      layout: layout({
        title: { text: "Tokens by provider" },
        legend: Object.assign({}, BASE.legend || {}, {
          orientation: "h", yanchor: "top", y: -0.05,
          xanchor: "center", x: 0.5, title: { text: "" }
        })
      }, 380)
    };
  }

  function costByModelSpec(d) {
    if (!d.costed.length) { return null; }
    var values = d.costed.map(function (m) { return m.cost_usd; });
    return {
      traces: [{
        type: "bar", orientation: "h",
        x: values, y: d.costed.map(function (m) { return m.key; }),
        text: values.map(function (v) { return fmtCostBar(v); }),
        texttemplate: "%{text}", textposition: "outside", cliponaxis: false,
        textfont: { color: "#374151", size: 11 },
        marker: { color: CFG.aggregateColor },
        hovertemplate: "%{y}<br>$%{x:,.4f}<extra></extra>"
      }],
      layout: layout({
        title: { text: "Estimated cost by model" },
        xaxis: axis("xaxis", { title: { text: "Estimated cost (USD)" },
                                range: paddedRange(Math.max.apply(null, values)) }),
        yaxis: axis("yaxis", { categoryorder: "total ascending",
                               title: { text: "" } })
      }, Math.max(260, 34 * d.costed.length + 120))
    };
  }

  function efficiencySpec(d) {
    var rows = d.costed.filter(function (m) { return m.calls >= CFG.minCallsForEfficiency; });
    rows = rows.map(function (m) {
      return { key: m.key, value: m.total_tokens / m.cost_usd };
    }).sort(function (a, b) { return b.value - a.value; });
    if (!rows.length) { return null; }
    var values = rows.map(function (r) { return r.value; });
    return {
      traces: [{
        type: "bar", orientation: "h",
        x: values, y: rows.map(function (r) { return r.key; }),
        text: values.map(function (v) { return group(Math.round(v)) + " tok/$"; }),
        texttemplate: "%{text}", textposition: "outside", cliponaxis: false,
        textfont: { color: "#374151", size: 11 },
        marker: { color: CFG.aggregateColor },
        hovertemplate: "%{y}<br>%{x:,.0f} tokens per $<extra></extra>"
      }],
      layout: layout({
        title: { text: "Pricing efficiency by model (" +
                       CFG.minCallsForEfficiency + "+ calls)" },
        xaxis: axis("xaxis", {
          title: { text: "Tokens per estimated USD (confirmed calls)" },
          range: paddedRange(Math.max.apply(null, values)) }),
        yaxis: axis("yaxis", { categoryorder: "total ascending",
                               title: { text: "" } })
      }, Math.max(260, 34 * rows.length + 120))
    };
  }

  function effortSpec(d) {
    if (d.efforts.length < 2) { return null; }
    var withTokens = d.efforts.filter(function (e) { return e.total_tokens > 0; });
    if (withTokens.length < 2) { return null; }
    var values = d.efforts.map(function (e) { return e.total_tokens; });
    return {
      traces: [{
        type: "bar",
        x: d.efforts.map(function (e) { return e.key; }), y: values,
        text: values.map(function (v) { return group(v); }),
        texttemplate: "%{text}", textposition: "outside", cliponaxis: false,
        textfont: { color: "#374151", size: 11 },
        marker: { color: d.efforts.map(function (e) {
          return CFG.effortColors[e.key] || CFG.unknownColor; }) },
        hovertemplate: "%{x}<br>%{y:,} tokens<extra></extra>"
      }],
      layout: layout({
        title: { text: "Tokens by reasoning effort" },
        xaxis: axis("xaxis", { title: { text: "Configured reasoning effort" } }),
        yaxis: axis("yaxis", { title: { text: "Input + output tokens" },
                                range: paddedRange(Math.max.apply(null, values)) })
      }, Math.max(260, 34 * d.efforts.length + 120))
    };
  }

  function present(id, spec) {
    var plotEl = $("plot-" + id);
    var card = document.querySelector('[data-slot="' + id + '"]');
    if (!plotEl || !card) { return; }
    if (spec) {
      card.hidden = false;
      plotEl.hidden = false;
      Plotly.react(plotEl, spec.traces, spec.layout, CFG.plotlyConfig);
    } else {
      Plotly.purge(plotEl);
      plotEl.innerHTML = "";
      plotEl.hidden = true;
      card.hidden = true;
    }
  }

  function presentNotice(id, show, html) {
    var card = document.querySelector('[data-slot="' + id + '"]');
    var emptyEl = document.querySelector('[data-empty-for="' + id + '"]');
    if (!card || !emptyEl) { return; }
    card.hidden = !show;
    emptyEl.hidden = !show;
    emptyEl.innerHTML = show ? (html || "") : "";
  }

  function costEmpty(d) {
    return "<b>Not enough priced usage to rank yet.</b> Cost was recorded for " +
      "<b>" + d.costed.length + " of " + d.modelCount + "</b> models (" +
      group(d.costRows) + " of " + group(d.rows.length) + " rows, " +
      (d.rows.length ? (d.costRows / d.rows.length * 100).toFixed(1) : "0.0") +
      "% coverage). The remaining " + d.freeModelCount +
      " models recorded no cost, which usually means a free tier or an " +
      "unreported provider. Both charts appear here once at least " +
      CFG.minCostModels + " models carry cost.";
  }

  // A single priced model makes a stray bar rather than a ranking, so the gate
  // covers the whole section rather than each chart separately.
  function enoughPricedModels(d) {
    return d.costed.length >= CFG.minCostModels;
  }

  function effortEmpty() {
    return "<b>No reasoning effort to compare.</b> Every message in this export " +
      "used the provider default, so there is nothing to rank. The chart " +
      "appears once two or more effort levels are recorded.";
  }

  function footerHtml(d) {
    var out = ["<p>Cache read is an independent counter reported separately from " +
               "input and output; it is not part of the input + output total " +
               "above.</p>"];
    if (d.modelCount > CFG.topN) {
      var shown = d.models.slice(0, CFG.topN).reduce(function (a, m) {
        return a + m.total_tokens; }, 0);
      out.push("<p>The model ranking shows the top " + CFG.topN + " of " +
        d.modelCount + " models, covering " + group(shown) + " of " +
        group(d.totals.tokens) + " tokens (" +
        (d.totals.tokens ? (shown / d.totals.tokens * 100).toFixed(1) : "0.0") +
        "%).</p>");
    }
    return out.join("");
  }

  function render() {
    var d = aggregate(state.rows);

    $("kpis").innerHTML = kpiHtml(d);
    $("scope").innerHTML = scopeHtml(d);
    $("subtitle").innerHTML = subtitleHtml(d);
    $("insights").innerHTML = insightHtml(d);
    $("footer-notes").innerHTML = footerHtml(d);

    present("trend-tokens", trendSpec(d.daily, "total_tokens", "tokens"));
    present("trend-cost", trendSpec(d.daily, "cost_usd", "cost"));
    present("projects", rankingSpec(d.projects,
      "Top " + Math.min(d.projects.length, CFG.topN) + " projects by tokens", ""));
    present("models", rankingSpec(d.models,
      "Top " + Math.min(d.models.length, CFG.topN) + " models by tokens", ""));
    present("composition", compositionSpec(d));
    present("providers", providerSpec(d));

    var priced = enoughPricedModels(d);
    presentNotice("cost-notice", !priced, costEmpty(d));
    present("cost-by-model", priced ? costByModelSpec(d) : null);
    present("efficiency", priced ? efficiencySpec(d) : null);
    present("effort", effortSpec(d));
    presentNotice("effort-notice", !effortSpec(d), effortEmpty());

    // Cards hidden by a gate start at zero width, so charts revealed on a later
    // render need an explicit resize once they are visible.
    if (window.Plotly && Plotly.Plots) {
      Array.prototype.forEach.call(
        document.querySelectorAll(".plot-slot:not([hidden])"),
        function (el) { Plotly.Plots.resize(el); }
      );
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }

  // Exposed so tests can drive the render path the way a filter would.
  window.__DASHBOARD_RENDER__ = render;
})();
"""


def _client_rows(data: pd.DataFrame) -> list:
    """The rows the page needs, trimmed to the columns it actually uses."""
    columns = [
        "date", "project", "provider", "model", "calls", "input_tokens",
        "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "total_tokens", "cost_usd", "session_id",
    ]
    has_variant = "variant" in data.columns
    rows = []
    for record in data.to_dict("records"):
        row = {column: record[column] for column in columns}
        row["date"] = pd.Timestamp(record["date"]).date().isoformat()
        row["variant"] = record.get("variant") or "n/a" if has_variant else "n/a"
        rows.append(row)
    return rows


def _client_config(data: pd.DataFrame, rows: list, generated_at: str) -> dict:
    """Everything the client needs that is not per-row data."""
    return {
        "template": {"layout": pio.templates[TEMPLATE_NAME].layout.to_plotly_json()},
        "plotlyConfig": {"responsive": True, "displaylogo": False},
        "aggregateColor": AGGREGATE_COLOR,
        "categoricalColors": list(CATEGORICAL_COLORS),
        "categoryColors": dict(CATEGORY_COLORS),
        "categoryLabels": dict(CATEGORY_LABELS),
        "effortColors": dict(EFFORT_COLORS),
        "unknownColor": UNKNOWN_COLOR,
        "topN": TOP_N,
        "minLabelShare": MIN_LABEL_SHARE,
        "labelHeadroom": LABEL_HEADROOM,
        "minCostModels": MIN_COST_MODELS,
        "minCallsForEfficiency": MIN_CALLS_FOR_EFFICIENCY,
        "userCount": int(data["user"].nunique()) if "user" in data.columns else 1,
        "generatedAt": generated_at,
    }


def _slot(chart_id: str, css: str = "") -> str:
    """A chart card holding a plot target and an insufficient-data fallback."""
    classes = ("card " + css).strip()
    return (
        f'<div class="{classes}" data-slot="{chart_id}">'
        f'<div class="plot-slot" id="plot-{chart_id}"></div>'
        f'<div class="empty-state" data-empty-for="{chart_id}" hidden></div>'
        "</div>"
    )


def _glossary_html() -> str:
    items = "".join(
        f'<dl class="glossary-item'
        f'{" highlight" if name == "Cache read" else ""}">'
        f"<dt>{html.escape(name)}</dt><dd>{html.escape(body)}</dd></dl>"
        for name, body in CATEGORY_EXPLAINERS.items()
    )
    return (
        '<details class="explain"><summary>What do these token '
        f'categories mean?</summary><div class="glossary-grid">{items}</div>'
        '<p class="explain-note">Cache read and reasoning are tracked as '
        "independent counters, so they are not part of the input + output "
        "headline.</p></details>"
    )


def build_dashboard(data: pd.DataFrame, out_path: Path) -> None:
    plotly_template()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = _client_rows(data)
    payload = _json_for_script({
        "rows": rows,
        "config": _client_config(data, rows, generated_at),
    })

    sections_html = []
    for anchor, name, desc in SECTIONS:
        if anchor == "sec-overview":
            body = (
                '<div class="kpi-row" id="kpis"></div>'
                '<p class="scope-summary" id="scope"></p>'
                '<div class="insight-bar" id="insights"></div>'
                '<div class="grid">'
                f'{_slot("trend-tokens", "full")}{_slot("trend-cost", "full")}'
                f'{_slot("projects")}{_slot("models")}'
                "</div>"
            )
        elif anchor == "sec-cost":
            body = (
                _slot("cost-notice", "full")
                + '<div class="grid">'
                f'{_slot("cost-by-model", "full")}{_slot("efficiency")}'
                "</div>"
            )
        else:
            body = (
                '<div class="grid">'
                f'{_slot("composition")}{_slot("providers")}'
                "</div>"
                + _slot("effort-notice")
                + _slot("effort")
                + f'<div class="grid full">{_glossary_html()}</div>'
            )
        sections_html.append(
            f'<section class="section" id="{anchor}">'
            f'<div class="section-head"><h2>{name}</h2></div>'
            f'<p class="section-desc">{desc}</p>{body}</section>'
        )

    nav_html = "".join(
        f'<a href="#{anchor}">{name}</a>' for anchor, name, _ in SECTIONS
    )

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OpenCode Usage Dashboard</title>
<style>{PAGE_CSS}</style>
<script>{get_plotlyjs()}</script>
</head><body>
<div class="hero">
<h1>OpenCode Usage Dashboard</h1>
<p class="subtitle" id="subtitle"></p>
<nav class="nav-pills">{nav_html}</nav>
</div>
<div class="page">
{"".join(sections_html)}
<footer class="footer-note" id="footer-notes"></footer>
</div>
<script>window.__DASHBOARD__ = {payload};</script>
<script>{CLIENT_JS}</script>
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