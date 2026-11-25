# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-23

### Added

- Initial release of the OpenCode usage dashboard.
- Read-only SQLite extractor (`extract_usage.py`) that writes one row per
  session, day, provider, model, and variant to CSV.
- Self-contained, offline HTML dashboard (`dashboard.py`) with project, model,
  and provider filters, date-range presets, and token and cost rankings.
- Cost by model, pricing efficiency, reasoning effort, and cache-reuse sections,
  shown where the data supports them.
- Build-time redaction (`--exclude-project`, `--exclude-session-id`).
- Data-freshness and scale signals: a stale-export banner and a row-count
  warning.
- Community health files, issue templates, and a GitHub Pages documentation
  site with a synthetic sample dashboard.