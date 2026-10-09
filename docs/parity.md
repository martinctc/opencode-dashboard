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
| Project / model / provider filters | yes |
| Search, Only, Select all/none, Show more, Reset | yes |
| Provider exclusion cascading to its models | yes |
| Sidebar collapse persisted to localStorage | yes |
| Synthetic sample data, screenshot tooling, browser tests | yes |

## Not yet done

Ordered roughly by how much it would matter for this data.

### Interaction

- **Chart metric toggle** (Tokens / Estimated cost). Every chart would need the
  other value column, its formatter and its axis title. Currently worth little:
  exports are almost entirely free tier, so the cost view is near-empty. It
  becomes valuable once paid models are in use.
- **Date range presets** (7 / 14 / 30 / 90 days / All time), computed relative
  to the latest date in the loaded exports rather than today. Low value at six
  days of history, useful on a longer export.
- **Trend granularity toggle** (Day / Week / Month). Depends on the above.
- **Sortable, filterable rows table** over the embedded rows, and a CSV
  download. The rows are already in the page, so this is mostly table plumbing.

### Classification

- **Vendor-level model colouring.** Upstream infers a vendor from the model
  name prefix (`claude*` -> Anthropic, `gpt*` -> OpenAI, and so on) and colours
  models by vendor, shared across the ranking, efficiency and mix charts. Here
  the `provider` column is a routing gateway (`openrouter`, `opencode`,
  `azure`), and the real vendor sits inside the model string
  (`openrouter/google/gemma-4-31b-it`). Deciding between colouring by gateway
  (honest to the data, four colours) and inferring a vendor from the model path
  (better looking, needs its own mapping table) is still open. Doing it before
  chart code moves around again is cheapest.

### Publishing and sharing

- **Build-time redaction.** Upstream's `dashboard.py` accepts
  `--exclude-project`, `--omit-task-summaries`, `--exclude-default` and
  `--exclude-default-models`, so a dashboard can be stripped before sharing.
  Here the only lever is `--include-session-title` at extract time, which is
  opt-in but cannot remove content afterwards. The generated HTML embeds its
  source rows either way, so this matters to anyone sharing a file.
- **Export format versioning.** Upstream tracks format versions 1-3 and still
  loads legacy exports that predate `exported_at` / `export_format_version`,
  warning when ordering is uncertain. This project writes version 1 and assumes
  the metadata columns exist.

### Repository

- **Continuous integration.** No workflow runs the suite on push.
- **`CONTRIBUTING.md`, `SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`** are
  absent; upstream has all four.
- **Documentation site.** `_config.yml`, `index.md` and front matter on the
  guides are in place, using the same `just-the-docs` remote theme as upstream.
  It publishes documentation only, never a generated dashboard, because those
  embed source rows.

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
  export history will push on this; a row cap with a warning, or dropping the
  columns filters do not need, is the obvious lever.
- **Unexported usage.** The dashboard shows whatever the CSVs contain. The
  extractor runs against a point-in-time snapshot, so a stale CSV renders stale
  data with a data-through date and nothing else to signal it.