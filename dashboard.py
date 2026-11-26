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
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from plotly.offline import get_plotlyjs
from plotly.utils import PlotlyJSONEncoder

from _version import __version__


# Columns every export must carry. Export metadata is deliberately absent:
# exports written before versioning existed have neither `exported_at` nor
# `export_format_version`, and are still loadable (see load_data).
REQUIRED_COLUMNS = {
    "user", "date", "project", "provider", "model", "calls",
    "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens", "cost_usd",
    "variant", "session_id",
}

# Columns whose absence is expected and already covered by a dedicated warning:
# legacy exports predate exported_at, and exports without export_format_version
# predate versioning. Do not double-report them as a column mismatch.
OPTIONAL_METADATA_COLUMNS = {"exported_at", "export_format_version"}

# The newest export format this dashboard understands. An export from a later
# format may carry columns that are read as absent here, so it is reported
# rather than silently rendered as if it were this version.
CURRENT_EXPORT_FORMAT_VERSION = 1
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

# Cache reuse is a ratio, so one model could in principle carry it; but a model
# seen on a couple of calls tells you nothing stable about its cache behaviour.
MIN_CALLS_FOR_CACHE_REUSE = 5

# Date-range presets offered in the toolbar. Ranges are relative to the latest
# date in the loaded exports, not to today, so a stale CSV still reads sensibly.
DATE_PRESETS = [
    ("7", "7 days"),
    ("14", "14 days"),
    ("30", "30 days"),
    ("90", "90 days"),
    ("all", "All time"),
]
DEFAULT_DATE_PRESET = "all"

# A dashboard whose newest data is older than this many days shows a banner
# prompting a re-export. The age is measured against the viewer's clock, so a
# stale CSV reads as stale whenever it is opened.
DEFAULT_STALE_AFTER_DAYS = 14

# Embedded row counts at which the page warns that the payload is getting
# heavy: the client re-slices every row on each filter change, so past these
# sizes the page is slow to load and filters feel sluggish.
ROW_WARN_ROWS = 10_000
ROW_WARN_ROWS_HIGH = 50_000

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
    "Cache reuse": "Share of prompt tokens served from cache instead of being "
                   "resent. Calculated as cache read ÷ (input + cache read). "
                   "A higher percentage means more context was reused across "
                   "turns. Models need enough calls to show stable reuse rates.",
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
DEFAULT_EXPORT_DIR = Path("exports")


def provider_colors(providers: Iterable[str]) -> dict[str, str]:
    """Assign each provider a fixed hue, in alphabetical order.

    Keying colour by name rather than by rank keeps a provider's colour stable
    when the set of providers changes or the selection changes.
    """
    return {
        name: CATEGORICAL_COLORS[index % len(CATEGORICAL_COLORS)]
        for index, name in enumerate(sorted(providers))
    }


# The provider column is a routing gateway (openrouter, azure, opencode), not the
# company whose model answered. The real vendor sits inside the model path: an
# openrouter row is `openrouter/<vendor>/<model>`, an azure row names the vendor
# in the deployment. Charting by gateway collapses Google, Anthropic and OpenAI
# into one "openrouter" slice, so the ranking and efficiency charts infer a
# vendor from the model string instead. Where no vendor can be inferred the
# gateway is used, which is honest to the data rather than a guess.
VENDOR_PREFIXES = (
    ("claude", "Anthropic"), ("anthropic", "Anthropic"),
    ("gpt", "OpenAI"), ("openai", "OpenAI"), ("o1", "OpenAI"),
    ("o3", "OpenAI"), ("o4", "OpenAI"),
    ("gemini", "Google"), ("gemma", "Google"), ("google", "Google"),
    ("deepseek", "DeepSeek"),
    ("llama", "Meta"), ("meta", "Meta"),
    ("mistral", "Mistral"), ("codestral", "Mistral"),
    ("qwen", "Alibaba"), ("qwq", "Alibaba"),
    ("nemotron", "NVIDIA"), ("nvidia", "NVIDIA"),
    ("grok", "xAI"), ("xai", "xAI"),
    ("command", "Cohere"), ("cohere", "Cohere"),
)


def infer_vendor(model: str, gateway: str) -> str:
    """Best-effort vendor for a `gateway/model` string.

    `openrouter/google/gemma-4-31b-it` -> Google (vendor is the path segment
    after the gateway). `azure/DeepSeek-V3.2` -> DeepSeek (matched on the model
    name). Anything unrecognised falls back to the gateway, which is truthful
    about what the export actually recorded.
    """
    remainder = model.split("/", 1)[1] if "/" in model else model
    head = remainder.split("/", 1)[0]
    lower_head = head.lower()
    # A path segment that is itself a vendor name is authoritative.
    if "/" in remainder:
        for prefix, vendor in VENDOR_PREFIXES:
            if lower_head == prefix.lower() or lower_head.startswith(prefix.lower()):
                return vendor
    # Otherwise match a known vendor anywhere in the leading model name.
    for prefix, vendor in VENDOR_PREFIXES:
        if lower_head.startswith(prefix.lower()) or (
            len(lower_head) > 3 and prefix.lower() in lower_head
        ):
            return vendor
    return gateway


def vendor_colors(data: pd.DataFrame) -> tuple[dict[str, str], dict[str, str]]:
    """Map each model to a vendor, and each vendor to a stable hue.

    Returns `(model_to_vendor, vendor_palette)`. Colour is keyed by vendor name
    in alphabetical order so a vendor keeps its hue when the selection changes,
    matching how provider colours are assigned.
    """
    model_to_vendor = {
        row.model: infer_vendor(row.model, row.provider)
        for row in data.itertuples()
    }
    vendors = sorted(set(model_to_vendor.values()))
    palette = {
        name: CATEGORICAL_COLORS[index % len(CATEGORICAL_COLORS)]
        for index, name in enumerate(vendors)
    }
    return model_to_vendor, palette


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

# Page stylesheet and client render layer. They live in static/ so editors and
# formatters can work on them as plain CSS/JS, and are inlined at build time so
# the generated HTML stays self-contained and offline.
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_static(name: str) -> str:
    """Read a bundled asset that is inlined into the generated HTML."""
    return (STATIC_DIR / name).read_text(encoding="utf-8")


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
    ("sec-rows", "Rows",
     "The individual export rows behind every chart above, under the same "
     "filters. Sort any column, search across all of them, or download the "
     "current selection as CSV."),
]


def _pattern_includes_directory(pattern: str) -> bool:
    candidate = Path(pattern)
    return (
        candidate.is_absolute()
        or candidate.parent != Path(".")
        or ("/" in pattern)
        or ("\\" in pattern)
    )


def _resolve_input_paths(pattern: str) -> tuple[list[Path], list[str]]:
    attempted = [pattern]
    paths = sorted(Path(path) for path in glob.glob(pattern))
    if not paths and Path(pattern).is_file():
        paths = [Path(pattern)]
    if paths:
        return paths, attempted
    if not _pattern_includes_directory(pattern):
        fallback = str(DEFAULT_EXPORT_DIR / pattern)
        attempted.append(fallback)
        paths = sorted(Path(path) for path in glob.glob(fallback))
        if paths:
            return paths, attempted
    return [], attempted


def load_data(pattern: str) -> pd.DataFrame:
    paths, attempted = _resolve_input_paths(pattern)
    if not paths:
        tried = "; ".join(attempted)
        raise FileNotFoundError(f"No input CSV files matched: {pattern} (tried: {tried})")
    frames = []
    frame_columns = []
    for path in paths:
        frame = pd.read_csv(path)
        frame_columns.append((path, set(frame.columns)))
        missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
        # Export metadata is optional: exports written before this project
        # tracked versions carry neither column and are still loadable.
        if "exported_at" in frame.columns:
            frame["exported_at"] = pd.to_datetime(
                frame["exported_at"], errors="raise", utc=True
            )
            if frame["exported_at"].isna().any():
                raise ValueError(f"{path} has a missing export timestamp")
        else:
            frame["exported_at"] = pd.NaT
        _warn_on_export_version(frame, path)
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
    _warn_on_column_diff(frame_columns)
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
        if data["exported_at"].notna().all():
            data = (
                data.sort_values("exported_at", kind="stable")
                .drop_duplicates(identity, keep="last")
            )
        else:
            # Without timestamps there is no principled "latest". Files are
            # loaded in sorted path order, which for date-suffixed exports is
            # chronological, so the last row seen wins - but say so rather than
            # implying the timestamps agreed.
            warnings.warn(
                "Some exports carry no export timestamp, so which overlapping "
                "row is newest cannot be determined; keeping the last row in "
                "file order instead.",
                RuntimeWarning,
            )
            data = data.drop_duplicates(identity, keep="last")
    return data


def _warn_on_column_diff(frame_columns: list[tuple[Path, set[str]]]) -> None:
    """Warn when input exports do not share the same columns.

    pandas aligns columns by name when concatenating and fills gaps with NaN, so
    a missing column does not fail the load - it silently becomes missing data.
    Say which file lacks what rather than letting that pass unnoticed.
    """
    if len(frame_columns) < 2:
        return
    compared = [
        (path, columns - OPTIONAL_METADATA_COLUMNS)
        for path, columns in frame_columns
    ]
    union = set().union(*(columns for _, columns in compared))
    if all(columns == union for _, columns in compared):
        return
    details = []
    for path, columns in compared:
        missing = sorted(union - columns)
        if missing:
            details.append(f"{path} is missing: {', '.join(missing)}")
    warnings.warn(
        "Exports do not all carry the same columns; rows are combined and "
        "missing values are filled in.\n  - " + "\n  - ".join(details),
        RuntimeWarning,
    )


def _warn_on_export_version(frame: pd.DataFrame, path: Path) -> None:
    """Report an export written in a format this dashboard does not know.

    A missing version column means the export predates versioning, which is
    expected and loads normally. A *newer* version means the file may carry
    columns this build never sees, so rendering it as if it were version 1
    would misrepresent the data.
    """
    if "export_format_version" not in frame.columns:
        return
    raw = frame["export_format_version"].dropna().astype(str).str.strip()
    raw = raw[raw.ne("")]
    if raw.empty:
        return
    versions = set(raw)
    if len(versions) > 1:
        warnings.warn(
            f"{path} mixes export format versions "
            f"({', '.join(sorted(versions))}); reading all rows as "
            f"version {CURRENT_EXPORT_FORMAT_VERSION}.",
            RuntimeWarning,
        )
        versions = {str(CURRENT_EXPORT_FORMAT_VERSION)}
    for value in sorted(versions):
        if not value.lstrip("-").isdigit():
            warnings.warn(
                f"{path} has an unreadable export format version {value!r}; "
                "reading its rows as if they were version "
                f"{CURRENT_EXPORT_FORMAT_VERSION}.",
                RuntimeWarning,
            )
            continue
        if int(value) > CURRENT_EXPORT_FORMAT_VERSION:
            warnings.warn(
                f"{path} was written in export format {value}, newer than the "
                f"version {CURRENT_EXPORT_FORMAT_VERSION} this dashboard "
                "understands; any columns it adds will be ignored.",
                RuntimeWarning,
            )


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


def _escape(value: object) -> str:
    """Escape a value from the data before interpolating it into HTML.

    Project and model names come from disk; without this a name containing
    markup would be injected into the page.
    """
    return html.escape(str(value), quote=True)


def _json_for_script(value: object) -> str:
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


def _client_rows(data: pd.DataFrame) -> list[dict[str, object]]:
    """The rows the page needs, trimmed to the columns it actually uses."""
    columns = [
        "date", "project", "provider", "model", "calls", "input_tokens",
        "output_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "total_tokens", "cost_usd", "session_id",
    ]
    has_variant = "variant" in data.columns
    rows: list[dict[str, object]] = []
    for record in data.to_dict("records"):
        row: dict[str, object] = {column: record[column] for column in columns}
        row["date"] = pd.Timestamp(record["date"]).date().isoformat()
        row["variant"] = record.get("variant") or "n/a" if has_variant else "n/a"
        rows.append(row)
    return rows


def _client_config(data: pd.DataFrame, rows: list[dict[str, object]], generated_at: str,
                   stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> dict[str, object]:
    """Everything the client needs that is not per-row data."""
    model_to_vendor, vendor_palette = vendor_colors(data)
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
        "minCallsForCacheReuse": MIN_CALLS_FOR_CACHE_REUSE,
        "datePresets": [{"id": p[0], "label": p[1]} for p in DATE_PRESETS],
        "defaultDatePreset": DEFAULT_DATE_PRESET,
        "modelVendor": model_to_vendor,
        "vendorColors": vendor_palette,
        "filterPage": FILTER_PAGE,
        "userCount": int(data["user"].nunique()) if "user" in data.columns else 1,
        "generatedAt": generated_at,
        "rowCount": len(rows),
        "staleAfterDays": stale_after_days,
        "rowWarnRows": ROW_WARN_ROWS,
        "rowWarnRowsHigh": ROW_WARN_ROWS_HIGH,
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


FILTER_GROUPS = [
    ("project", "Projects"),
    ("model", "Models"),
    ("provider", "Providers"),
]

# Items shown before "Show more" is needed.
FILTER_PAGE = 8


def _filter_panel(group: str, title: str) -> str:
    """One filter group. The item list is filled in by the client."""
    return (
        f'<div class="side-panel" data-filter-group="{group}">'
        f"<h2>{title}</h2>"
        f'<input class="filter-search" type="search" autocomplete="off"'
        f' placeholder="Search {title.lower()}" data-search-for="{group}">'
        f'<div class="filter-list" data-list-for="{group}"></div>'
        '<div class="filter-actions">'
        f'<button type="button" data-action="all" data-group="{group}">Select all</button>'
        f'<button type="button" data-action="none" data-group="{group}">Select none</button>'
        f'<button type="button" data-action="more" data-group="{group}" hidden>Show more</button>'
        "</div></div>"
    )


def _sidebar_html() -> str:
    panels = "".join(
        _filter_panel(group, title) for group, title in FILTER_GROUPS
    )
    return (
        '<aside class="sidebar-shell">'
        '<button type="button" class="sidebar-toggle" id="sidebar-toggle"'
        ' aria-expanded="true" aria-controls="sidebar">'
        '<span aria-hidden="true">&#9662;</span>'
        '<span id="sidebar-toggle-label">Hide filters</span>'
        "</button>"
        f'<div class="sidebar" id="sidebar">{panels}</div>'
        "</aside>"
    )


def _toolbar_html() -> str:
    """Date-range presets and the ranking metric toggle.

    Both are segmented button groups. The pressed state is driven by the client
    from stored preferences, so the markup here only defines the options.
    """
    date_buttons = "".join(
        f'<button type="button" data-date-preset="{preset}"'
        f' aria-pressed="{"true" if preset == DEFAULT_DATE_PRESET else "false"}"'
        f">{html.escape(label)}</button>"
        for preset, label in DATE_PRESETS
    )
    metric_buttons = "".join(
        f'<button type="button" data-metric="{metric}"'
        f' aria-pressed="{"true" if metric == "tokens" else "false"}"'
        f">{label}</button>"
        for metric, label in (("tokens", "Tokens"), ("cost", "Cost"))
    )
    return (
        '<div class="toolbar">'
        '<div class="seg"><span class="seg-label">Date range</span>'
        f'<div class="seg-group" role="group" aria-label="Date range">{date_buttons}</div></div>'
        '<div class="seg"><span class="seg-label">Rank by</span>'
        f'<div class="seg-group" role="group" aria-label="Ranking metric">{metric_buttons}</div>'
        '<span class="seg-note" id="metric-notice" hidden></span></div>'
        "</div>"
    )


def _glossary_html() -> str:
    items = "".join(
        f'<dl class="glossary-item'
        f'{" highlight" if name in ("Cache read", "Cache reuse") else ""}">'
        f"<dt>{html.escape(name)}</dt><dd>{html.escape(body)}</dd></dl>"
        for name, body in CATEGORY_EXPLAINERS.items()
    )
    return (
        '<details class="explain"><summary>What do these token '
        f'categories mean?</summary><div class="glossary-grid">{items}</div>'
        '<p class="explain-note">Cache read and reasoning are tracked as '
        "independent counters, so they are not part of the input + output "
        "headline. Cache reuse measures how much of the prompt was served from cache.</p></details>"
    )


def redact_data(
    data: pd.DataFrame,
    exclude_project: bool = False,
    exclude_session_id: bool = False,
) -> pd.DataFrame:
    """Strip identifying free-text from a frame before it is embedded.

    The generated HTML embeds its source rows, so a file meant for sharing can
    otherwise carry local directory names and store identifiers. `user` and any
    `session_title` column never reach the payload at all (see `_client_rows`),
    so they need no flag here.

    Excluded session IDs are relabelled to stable sequential tokens rather than
    dropped, which keeps the Sessions KPI and the rows table honest without
    exposing the original identifiers.
    """
    if not (exclude_project or exclude_session_id):
        return data
    data = data.copy()
    if exclude_project:
        data["project"] = "(excluded)"
    if exclude_session_id:
        labels = {
            session: f"session-{index}"
            for index, session in enumerate(sorted(data["session_id"].unique()))
        }
        data["session_id"] = data["session_id"].map(labels)
    return data


def build_dashboard(
    data: pd.DataFrame,
    out_path: Path,
    exclude_project: bool = False,
    exclude_session_id: bool = False,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
) -> None:
    plotly_template()
    data = redact_data(data, exclude_project, exclude_session_id)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = _client_rows(data)
    config = _client_config(data, rows, generated_at, stale_after_days)
    # Two passes so the reported size includes the size field itself. The
    # difference is a few bytes, and the figure is a guide rather than a
    # measurement of the final HTML.
    config["payloadBytes"] = 0
    payload = _json_for_script({"rows": rows, "config": config})
    config["payloadBytes"] = len(payload.encode("utf-8"))
    payload = _json_for_script({"rows": rows, "config": config})

    sections_html = []
    for anchor, name, desc in SECTIONS:
        if anchor == "sec-overview":
            body = (
                f'<div class="insight-bar" id="insights"></div>'
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
        elif anchor == "sec-rows":
            body = (
                '<div class="rows-controls">'
                '<input class="rows-search" type="search" id="rows-search"'
                ' placeholder="Search all columns..."'
                ' autocomplete="off" aria-label="Search rows">'
                '<span class="rows-meta" id="rows-meta"></span>'
                '<button type="button" class="rows-download" id="rows-download">'
                "Download CSV</button>"
                "</div>"
                '<div class="rows-scroll">'
                '<table class="rows-table" id="rows-table">'
                "<thead></thead><tbody></tbody></table></div>"
                '<div class="rows-pager">'
                '<button type="button" id="rows-prev">Previous</button>'
                '<span class="rows-meta" id="rows-page"></span>'
                '<button type="button" id="rows-next">Next</button>'
                "</div>"
            )
        else:
            body = (
                '<div class="grid">'
                f'{_slot("composition")}{_slot("providers")}'
                "</div>"
                + _slot("cache-reuse-notice", "full")
                + _slot("cache-reuse", "full")
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

    page_css = _load_static("dashboard.css")
    client_js = _load_static("dashboard.js")

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>OpenCode Usage Dashboard</title>
<style>{page_css}</style>
<script>{get_plotlyjs()}</script>
</head><body>
<div class="hero">
<h1>OpenCode Usage Dashboard</h1>
<p class="subtitle" id="subtitle"></p>
<nav class="nav-pills">{nav_html}</nav>
</div>
<div class="page">
{_toolbar_html()}
<div class="data-signals" id="data-signals"></div>
<div class="filter-actions top">
<button type="button" id="reset-filters">Reset filters</button>
</div>
<p class="selection-summary" id="selection"></p>
<div class="kpi-row" id="kpis"></div>
<div class="layout" id="layout">
{_sidebar_html()}
<main class="main">
<div id="charts">
{"".join(sections_html)}
</div>
<footer class="footer-note" id="footer-notes"></footer>
</main>
</div>
</div>
<script>window.__DASHBOARD__ = {payload};</script>
<script>{client_js}</script>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--in", dest="input_pattern", default="opencode_usage_*.csv",
                        help="Input CSV path or glob pattern; when no directory is provided, exports/<pattern> is also checked")
    parser.add_argument("--out", type=Path, default=Path("usage_dashboard.html"),
                        help="HTML output path")
    parser.add_argument("--exclude-project", action="store_true",
                        help="Replace project names with '(excluded)' before embedding rows")
    parser.add_argument("--exclude-session-id", action="store_true",
                        help="Replace session IDs with sequential tokens before embedding rows")
    parser.add_argument("--stale-after-days", type=int, default=DEFAULT_STALE_AFTER_DAYS,
                        help="Show a stale-export banner when the newest data is older than this "
                             "many days (default: %(default)s; 0 disables)")
    args = parser.parse_args(argv)
    data = load_data(args.input_pattern)
    build_dashboard(
        data, args.out,
        exclude_project=args.exclude_project,
        exclude_session_id=args.exclude_session_id,
        stale_after_days=args.stale_after_days,
    )
    note = ""
    if args.exclude_project or args.exclude_session_id:
        redacted = [
            label for label, on in (
                ("projects", args.exclude_project),
                ("session IDs", args.exclude_session_id),
            ) if on
        ]
        note = " (redacted: " + ", ".join(redacted) + ")"
    print(f"Wrote dashboard to {args.out}{note} ({len(data):,} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())