#!/usr/bin/env python3
"""Capture dashboard screenshots from a generated HTML file.

Used to document layout changes without any real usage data entering the
repository. Requires Playwright with Chromium:

    python -m pip install playwright
    playwright install chromium

Usage:
    python tools/make_screenshots.py --html sample_dashboard.html --out-dir docs/images
"""

import argparse
from pathlib import Path

# (filename suffix, viewport width). Each width is captured full page so a
# regression in the responsive breakpoints shows up as a layout change.
VIEWPORTS = [
    ("desktop", 1440),
    ("mobile", 390),
]

# Section id -> screenshot name, so each section can be captured on its own.
SECTIONS = [
    ("sec-overview", "overview"),
    ("sec-cost", "cost"),
    ("sec-composition", "composition"),
]


def capture(html: Path, out_dir: Path, suffix: str, width: int) -> list:
    from playwright.sync_api import sync_playwright

    written = []
    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 1000})
        page.goto(html.resolve().as_uri())
        page.wait_for_selector(".plotly-graph-div .main-svg", timeout=30_000)
        page.wait_for_timeout(1500)

        target = out_dir / f"dashboard-{suffix}.png"
        page.screenshot(path=str(target), full_page=True)
        written.append(target)

        if width >= 1000:
            for anchor, name in SECTIONS:
                element = page.query_selector(f"#{anchor}")
                if element is None:
                    continue
                target = out_dir / f"{name}-{suffix}.png"
                element.screenshot(path=str(target))
                written.append(target)

        browser.close()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=Path("docs/images"))
    args = parser.parse_args()

    if not args.html.exists():
        parser.error(f"HTML not found: {args.html} (generate it with dashboard.py)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for suffix, width in VIEWPORTS:
        for target in capture(args.html, args.out_dir, suffix, width):
            print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())