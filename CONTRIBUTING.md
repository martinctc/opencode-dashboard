# Contributing to OpenCode Usage Dashboard

Thanks for your interest in improving the dashboard. This project is a local,
read-only tool for exploring OpenCode usage, and contributions of all sizes are
welcome — bug reports, documentation fixes, and new features alike.

## Development setup

Requirements: Python 3.9 or later, plus OpenCode session data if you want to
exercise the exporter against real data (the tests and sample data do not need
it).

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

Most tests are plain unit tests. `tests/test_dashboard_ux.py` additionally
drives the generated dashboard in a real browser and skips itself when
Playwright is unavailable:

```powershell
python -m pip install playwright
playwright install chromium
python -m pytest tests/test_dashboard_ux.py -q
```

## Working with sample data

A real export is usually too small to exercise the interactive features, so use
the deterministic synthetic generator while iterating on the dashboard:

```powershell
python tools/make_sample_data.py --out sample_usage.csv
python dashboard.py --in sample_usage.csv --out sample_dashboard.html
```

To regenerate the screenshots in `docs/images` after a layout change:

```powershell
python tools/make_screenshots.py --html sample_dashboard.html --out-dir docs/images
```

## Project layout

- `extract_usage.py` — reads OpenCode's local SQLite database (via a read-only
  snapshot) and writes a CSV export.
- `dashboard.py` — renders one or more CSV exports into a self-contained HTML
  dashboard.
- `tools/` — sample-data generator and screenshot tooling.
- `tests/` — unit and browser tests.
- `docs/` — the GitHub Pages documentation site.

## Pull requests

- Keep changes focused; one logical change per pull request.
- Add or update tests for behaviour changes, and run `python -m pytest` before
  opening a pull request.
- Match the surrounding code style and comment the "why" for non-obvious
  decisions, as the existing code does.
- Update `README.md`, `docs/parity.md`, or `docs/compatibility.md` when your
  change affects documented behaviour or the gap with the upstream tool.
- If you change the dashboard layout, regenerate the screenshots with
  `tools/make_screenshots.py`.

## Attribution and licensing

This project began as an adaptation of
[github.com/microsoft/ghc-cli-dashboard](https://github.com/microsoft/ghc-cli-dashboard),
which is also MIT licensed. Substantial parts of `extract_usage.py`,
`dashboard.py` and the test suite are derived from it, and Microsoft
Corporation's copyright is retained in `LICENSE` and in the headers of the
derived files, as the MIT licence requires. Please preserve those headers in
any further derived work.

## Code of conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
