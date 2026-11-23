---
layout: default
title: Parity with upstream
nav_order: 3
---

# Parity with microsoft/ghc-cli-dashboard

This project is an adaptation of
[github.com/microsoft/ghc-cli-dashboard](https://github.com/microsoft/ghc-cli-dashboard)
(the Copilot CLI usage dashboard, which is also MIT licensed). That page
is the maintained tool for GitHub Copilot CLI; this one is the equivalent for
OpenCode. This file records what the two do not yet agree on, so the gap is
visible rather than implied. See
[Attribution](https://github.com/martinctc/opencode-dashboard#attribution-and-related-projects)
for how the two relate.

Scoping note: the upstream project targets Copilot CLI's session store. Items
that depend on data only Copilot CLI records are marked as such and are not
worth porting unless OpenCode exposes the same thing.

## Done here

| Area | Status |
| --- | --- |
| SQLite extractor, read-only snapshot | yes |
| CSV export, overlap resolution by export timestamp | yes |
| Self-contained offline HTML, Plotly bundled inline | yes |
| Token category definitions kept separate from the headline | yes |
| Design tokens, page shell, responsive layout | yes |
| Okabe-Ito / Viridis chart palette | yes |
| Cost by model, pricing efficiency, reasoning effort | yes, data-gated |
| Insights derived from the data | yes |
| Client-side render layer (re-render without a rebuild) | yes |
| Static CSS/JS extracted and inlined at build time | yes, formatted with Prettier |
| Project / model / provider filters | yes |
| Search, Only, Select all/none, Show more, Reset | yes |
| Provider exclusion cascading to its models | yes |
| Sidebar collapse persisted to localStorage | yes |
| Synthetic sample data, screenshot tooling, browser tests | yes |
| Date range presets (7 / 14 / 30 / 90 / All) | yes, anchored to latest export date |
| Data-freshness and scale signals (stale banner, row-count warning) | yes, 14-day default, `--stale-after-days` |
| Chart metric toggle for model ranking (tokens / cost) | yes, gated behind MIN_COST_MODELS so free exports show insufficient data |
| Vendor-level model colouring inferred from the model path | yes, ranking and cache-reuse charts |
| Cache reuse by model, shown where the data supports a rate | yes, 5-call minimum |
| Week-over-week KPI deltas against the previous window | yes, finite presets only, suppressed when the previous window is truncated by the earliest export date |
| Sortable, searchable rows table with CSV download | yes, paginated |
| Build-time redaction (`--exclude-project`, `--exclude-session-id`) | yes |
| Export format versioning, legacy loads tolerated | yes, version 1, warns above and on uncertain ordering |
| Continuous integration on push | yes, unit + browser jobs |
| GitHub Pages documentation site | yes, docs plus a synthetic sample dashboard |
| Community health files (CONTRIBUTING, SECURITY, SUPPORT, CODE_OF_CONDUCT) | yes |
| Issue templates, Discussions, repository topics | yes |

## Not yet done

Ordered roughly by how much it would matter for this data.

### Interaction

- **Trend granularity toggle** (Day / Week / Month). The other interaction items
  are done; this one still needs the trend chart to aggregate on the client and
  re-derive its axis ticks.

### Classification

- Nothing outstanding. Vendor colouring is inferred from the model path, with
  the gateway `provider` column kept separate as the honest grouping key in the
  filters.

### Publishing and sharing

- Nothing outstanding for sharing a file. Redaction happens at build time and
  the redacted rows never enter the HTML payload.

### Repository

- Nothing outstanding. `CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md` and
  `CODE_OF_CONDUCT.md` are present, along with issue templates, Discussions, and
  repository topics.

### Data this project does not collect

Not gaps to close unless OpenCode starts recording the same thing.

- **Task summaries and work-pattern views.** Upstream reads per-task summaries
  out of the Copilot CLI store and derives work themes and modes. OpenCode's
  store is not read for this.
- **Reasoning-effort defaults per model.** Upstream knows each model's default
  effort so an unset value can be inferred. Here `variant` is `n/a` unless
  explicitly recorded, which is why the reasoning-effort section usually shows
  its insufficient-data state.

## Structural limits worth knowing

These are properties of the current design, not backlog items, but they bound
what future work can do.

- **Filter granularity.** `extract_usage.py` writes one row per
  (session, day, provider, model, variant). Client-side filters re-slice at that
  grain and no finer, so session-level drill-down needs the extractor to emit
  finer rows. That multiplies row count, which interacts with the next point.
- **Page size grows with rows.** The embedded payload was 1.26MB for 3,811
  rows, about 21% of a 5.89MB file, on top of a ~4.85MB bundled Plotly. A long
  export history will push on this; the dashboard now warns at 10,000 and
  50,000 embedded rows, but a hard row cap or dropping the columns filters do
  not need remains the lever if the warning is not enough.
- **Unexported usage.** The dashboard shows whatever the CSVs contain. The
  extractor runs against a point-in-time snapshot, so a stale CSV renders stale
  data; the dashboard shows a data-through date and, when the newest data is
  older than the staleness threshold (14 days by default, `--stale-after-days`),
  a banner prompting a re-export.