# OpenCode Usage Dashboard

A local, read-only tool for exporting usage recorded by OpenCode and exploring
tokens, cost, projects, providers, and models in a self-contained HTML dashboard.

The exporter reads OpenCode's local SQLite database. Its schema is an
implementation detail and can change between releases. The exporter validates
the tables and columns it needs and reports incompatible schemas rather than
silently producing incomplete data.

## Quick start

Requirements: Python 3.9 or later and OpenCode session data on this machine.

```powershell
python -m pip install -r requirements.txt
python extract_usage.py
python dashboard.py --in "opencode_usage_*.csv" --out usage_dashboard.html
```

Open `usage_dashboard.html` in a browser. The exporter discovers the default
database location when possible. Pass `--db` to select a custom location:

```powershell
python extract_usage.py --db "C:\path\to\opencode.db"
```

Use `--include-session-title` only when you want free-text session titles in
the CSV and dashboard. Titles can contain sensitive project or task details.

## Metrics

The export uses one row per session, day, provider, model, and variant.
`total_tokens` is input plus output tokens, matching the definition used by the
Copilot CLI Usage Dashboard. Reasoning and cache tokens remain separate
counters and are not added to that headline total. Cost is the amount recorded
by OpenCode, not a recalculated estimate or an invoice.

When multiple CSVs contain the same session/model/day, the dashboard keeps the
row from the latest export timestamp and warns that it found overlapping
records. This avoids adding repeated snapshots of the same local history.

Provider is OpenCode's provider ID. Model is stored as `provider/model` so
models with the same name from different providers remain distinct. Project
names are reduced to the last directory segment to avoid exporting full local
paths.

## Privacy

The database is opened read-only and copied to a temporary SQLite snapshot
before extraction so the live OpenCode process is not modified or locked for
the duration of the read. The CSV and generated HTML can contain usernames,
project names, model names, dates, and optional session titles. Review files
before sharing them. Generated data files are excluded by `.gitignore`.

## Limitations

- OpenCode's local database schema is not a stable public API. Check
  [Compatibility](docs/compatibility.md) when OpenCode changes.
- Reported costs depend on the provider and pricing information available to
  OpenCode. The dashboard displays recorded cost and does not imply that it
  equals a provider invoice.
- Provider IDs identify the provider configured in OpenCode, which may be a
  gateway or service rather than the model vendor.
- The headline token total is deliberately input plus output. Compare cache
  and reasoning totals separately.

## Development

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

## Licence

MIT. See [LICENSE](LICENSE).
