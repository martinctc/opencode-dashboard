"""Browser layout and rendering checks for the generated dashboard.

These are the only tests that prove the dashboard actually works: the unit
tests assert on strings in the emitted HTML, which cannot catch a chart that
renders blank, a label clipped by its card, or a stylesheet that overflows on a
narrow screen.

Playwright is optional. The whole module skips when it, or its Chromium
browser, is unavailable, so the suite still runs in a bare environment:

    python -m pip install playwright
    playwright install chromium
"""

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import sync_playwright  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dashboard import build_dashboard, load_data  # noqa: E402

WIDTHS = [390, 768, 1440]


def _load_generator():
    path = Path(__file__).resolve().parent.parent / "tools" / "make_sample_data.py"
    spec = importlib.util.spec_from_file_location("make_sample_data", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sample_html(tmp_path_factory) -> Path:
    """A dashboard built from synthetic data.

    Real exports cover too few days to exercise date handling, and record no
    cost for free-tier models, so the cost and effort sections would render
    their insufficient-data states instead of their charts.
    """
    generator = _load_generator()
    csv_path = tmp_path_factory.mktemp("data") / "sample_usage.csv"
    html_path = tmp_path_factory.mktemp("html") / "sample_dashboard.html"
    rows = generator.build_rows(days=90, seed=20261006)
    _write_csv(csv_path, rows, generator.FIELDS)
    build_dashboard(load_data(str(csv_path)), html_path)
    return html_path


def _write_csv(path: Path, rows, fields) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture(scope="session")
def sparse_html(tmp_path_factory) -> Path:
    """A dashboard shaped like a real free-tier export.

    One priced model out of three, and no reasoning effort recorded. The gates
    must close here: a single priced model is a stray bar, not a ranking.
    """
    models = [
        ("opencode/free-model-a", "opencode", 0.0),
        ("openrouter/paid/model-b", "openrouter", 3.0),
        ("azure/free-model-c", "azure", 0.0),
    ]
    rows = []
    for offset, day in enumerate(("2026-10-05", "2026-10-06")):
        for index, (model, gateway, unit_cost) in enumerate(models):
            calls = 5 + offset + index
            total = 100_000 * (index + 1) * (offset + 1)
            rows.append({
                "source": "OpenCode", "user": "u", "date": day,
                "project": f"proj-{index % 2}", "provider": gateway,
                "model": model, "variant": "n/a", "calls": calls,
                "input_tokens": int(total * 0.9),
                "output_tokens": int(total * 0.1),
                "cache_read_tokens": total * 10, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "total_tokens": total,
                "cost_usd": round(total / 1_000_000 * unit_cost, 6),
                "session_id": f"s-{model}-{day}",
                "export_format_version": "1",
                "exported_at": "2026-10-06T10:00:00+00:00",
            })
    fields = list(rows[0])
    csv_path = tmp_path_factory.mktemp("sparse") / "sparse.csv"
    html_path = tmp_path_factory.mktemp("sparse") / "sparse_dashboard.html"
    _write_csv(csv_path, rows, fields)
    build_dashboard(load_data(str(csv_path)), html_path)
    return html_path


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as driver:
        try:
            instance = driver.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"chromium unavailable: {exc}")
        yield instance
        instance.close()


@pytest.fixture
def page(browser, sample_html):
    context = browser.new_context(viewport={"width": WIDTHS[0], "height": 900})
    handle = context.new_page()
    handle.goto(sample_html.resolve().as_uri())
    handle.wait_for_selector(".js-plotly-plot .main-svg", timeout=30_000)
    yield handle
    context.close()


def test_sample_data_is_deterministic(tmp_path):
    generator = _load_generator()
    first = generator.build_rows(days=30, seed=42)
    second = generator.build_rows(days=30, seed=42)
    assert first == second
    assert generator.build_rows(days=30, seed=43) != first


@pytest.mark.parametrize("width", WIDTHS)
def test_page_does_not_overflow_horizontally(browser, sample_html, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    handle = context.new_page()
    handle.goto(sample_html.resolve().as_uri())
    handle.wait_for_selector(".js-plotly-plot", timeout=30_000)
    overflow = handle.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    context.close()
    # A pixel of rounding is fine; a scrollbar's worth means something escapes.
    assert overflow <= 1, f"content overflows by {overflow}px at {width}px"


@pytest.mark.parametrize("width", WIDTHS)
def test_every_chart_renders_with_a_size(browser, sample_html, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    handle = context.new_page()
    handle.goto(sample_html.resolve().as_uri())
    handle.wait_for_selector(".js-plotly-plot .main-svg", timeout=30_000)
    handle.wait_for_timeout(800)
    boxes = handle.evaluate(
        """() => Array.from(document.querySelectorAll('.js-plotly-plot')).map(el => {
               const r = el.getBoundingClientRect();
               const svg = el.querySelector('.main-svg');
               return {w: Math.round(r.width), h: Math.round(r.height),
                       drew: svg ? svg.children.length : 0};
           })"""
    )
    context.close()
    assert boxes, "no charts found"
    for index, box in enumerate(boxes):
        assert box["w"] > 100, f"chart {index} collapsed to {box['w']}px wide"
        assert box["h"] > 100, f"chart {index} collapsed to {box['h']}px tall"
        assert box["drew"] > 0, f"chart {index} rendered no svg content"


def test_kpi_cards_are_present(page):
    labels = page.eval_on_selector_all(".kpi-label", "els => els.map(e => e.textContent)")
    values = page.eval_on_selector_all(".kpi-value", "els => els.map(e => e.textContent)")
    assert len(labels) >= 4
    assert all(value.strip() for value in values)


def test_nav_pills_target_existing_sections(page):
    hrefs = page.eval_on_selector_all(".nav-pills a", "els => els.map(e => e.getAttribute('href'))")
    assert hrefs
    for href in hrefs:
        assert page.query_selector(href) is not None, f"{href} has no target"


def test_sample_data_opens_cost_and_effort_sections(page):
    """The gates must open on data that supports them.

    Guards against a gate that never fires, which would look correct on a real
    free-tier export and stay empty forever.
    """
    assert page.query_selector("#sec-cost") is not None
    text = page.inner_text("body")
    assert "Estimated cost by model" in text
    assert "Pricing efficiency by model" in text
    assert "Tokens by reasoning effort" in text
    assert "Not enough priced usage to rank yet" not in text
    assert "No reasoning effort to compare" not in text


def test_insight_bars_are_rendered(page):
    insights = page.eval_on_selector_all(".insight", "els => els.map(e => e.textContent)")
    assert insights
    assert any("Cache read" in text for text in insights)


@pytest.fixture
def sparse_page(browser, sparse_html):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    handle = context.new_page()
    handle.goto(sparse_html.resolve().as_uri())
    handle.wait_for_selector(".plot-slot .main-svg", timeout=30_000)
    yield handle
    context.close()


def test_insufficient_data_closes_cost_and_effort_gates(sparse_page):
    """A single priced model must not be charted as a ranking.

    The companion to the gate-open test: without this, a gate that is never
    applied looks correct on a rich export and quietly regresses on a sparse one.
    """
    text = sparse_page.inner_text("body")
    assert "Not enough priced usage to rank yet" in text
    assert "No reasoning effort to compare" in text
    assert "Estimated cost by model" not in text
    assert "Pricing efficiency by model" not in text
    assert "Tokens by reasoning effort" not in text


def test_gated_cards_are_hidden_not_blank(sparse_page):
    """A gated chart must be hidden, and its explanation shown once."""
    for chart_id in ("cost-by-model", "efficiency", "effort"):
        assert not sparse_page.is_visible(f'[data-slot="{chart_id}"]')
    assert sparse_page.is_visible('[data-slot="cost-notice"]')
    assert sparse_page.is_visible('[data-slot="effort-notice"]')

    cost_message = sparse_page.inner_text('[data-slot="cost-notice"]')
    body = sparse_page.inner_text("body")
    assert body.count("Not enough priced usage to rank yet") == 1
    assert body.count("No reasoning effort to compare") == 1
    assert "1 of 3 models" in cost_message


def test_page_loads_without_external_requests(browser, sample_html):
    """The dashboard must open with no network access.

    This is the offline guarantee, checked the way a user experiences it
    rather than by grepping the HTML for a CDN URL.
    """
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    handle = context.new_page()
    external: list[str] = []
    handle.on(
        "request",
        lambda request: external.append(request.url)
        if not request.url.startswith("file:")
        else None,
    )
    handle.goto(sample_html.resolve().as_uri())
    handle.wait_for_selector(".plot-slot .main-svg", timeout=30_000)
    context.close()
    assert not external, f"page requested external resources: {external}"


def _tokens(handle) -> str:
    return handle.inner_text(".kpi-value")


def test_filter_panel_lists_every_item(page):
    """Each group is ranked by tokens, and paginated past the first page."""
    for group, expected in (("project", 8), ("provider", 4)):
        shown = page.eval_on_selector_all(
            f'[data-filter-group="{group}"] .filter-item',
            "els => els.map(e => e.dataset.value)",
        )
        assert len(shown) == expected, f"{group} listed {len(shown)} of {expected}"
    models = page.eval_on_selector_all(
        '[data-filter-group="model"] .filter-item', "els => els.length"
    )
    # Models paginate: only the first page shows until "Show more" is used.
    assert models == 8
    summary = page.inner_text("#selection")
    assert "of 8 projects" in summary and "of 16 models" in summary


def test_excluding_a_project_reduces_totals(page):
    before = _tokens(page)
    page.click('[data-filter-group="project"] .filter-item:first-child input')
    page.wait_for_timeout(500)
    after = _tokens(page)
    assert int(after.replace(",", "")) < int(before.replace(",", ""))
    assert "7 of 8 projects" in page.inner_text("#selection")


def test_only_selects_a_single_project(page):
    page.click('[data-filter-group="project"] .filter-item:nth-child(3) .filter-only')
    page.wait_for_timeout(500)
    summary = page.inner_text("#selection")
    assert "1 of 8 projects" in summary
    checked = page.eval_on_selector_all(
        '[data-filter-group="project"] input:checked', "els => els.length"
    )
    assert checked == 1


def test_reset_restores_everything(page):
    baseline = _tokens(page)
    page.click('[data-filter-group="project"] .filter-item:first-child input')
    page.wait_for_timeout(400)
    assert _tokens(page) != baseline
    page.click("#reset-filters")
    page.wait_for_timeout(500)
    assert _tokens(page) == baseline
    summary = page.inner_text("#selection")
    assert "8 of 8 projects" in summary
    assert "16 of 16 models" in summary


def test_search_filters_the_list_not_the_usage(page):
    """Search narrows which options are listed, never what is counted."""
    baseline = _tokens(page)
    page.fill('[data-search-for="model"]', "gpt")
    page.wait_for_timeout(500)
    shown = page.eval_on_selector_all(
        '[data-filter-group="model"] .filter-item', "els => els.map(e => e.dataset.value)"
    )
    assert shown and all("gpt" in name for name in shown)
    assert _tokens(page) == baseline, "search changed the reported usage"

    page.fill('[data-search-for="model"]', "zzzz-no-such-model")
    page.wait_for_timeout(400)
    assert page.inner_text('[data-list-for="model"]').strip() == "No matches"


def test_show_more_reveals_further_items(page):
    visible = page.eval_on_selector_all(
        '[data-filter-group="model"] .filter-item', "els => els.length"
    )
    assert visible == 8
    page.click('[data-action="more"][data-group="model"]')
    page.wait_for_timeout(400)
    assert page.eval_on_selector_all(
        '[data-filter-group="model"] .filter-item', "els => els.length"
    ) == 16


def test_select_none_applies_to_hidden_items_too(page):
    """Select none covers items hidden by search, not just visible ones."""
    page.fill('[data-search-for="model"]', "gpt")
    page.wait_for_timeout(300)
    page.click('[data-action="none"][data-group="model"]')
    page.wait_for_timeout(500)
    summary = page.inner_text("#selection")
    assert "0 of 16 models" in summary
    assert page.query_selector(".no-rows") is not None


def test_excluding_a_provider_excludes_its_models(page):
    page.click('[data-filter-group="provider"] .filter-item:first-child input')
    page.wait_for_timeout(600)
    blocked = page.eval_on_selector_all(
        '[data-filter-group="model"] .provider-excluded', "els => els.length"
    )
    disabled = page.eval_on_selector_all(
        '[data-filter-group="model"] input[disabled]', "els => els.length"
    )
    assert blocked > 0
    assert blocked == disabled
    assert "Excluded by provider" in page.inner_text('[data-filter-group="model"]')

    # Re-including the gateway restores the models the user had chosen.
    page.click('[data-filter-group="provider"] .filter-item:first-child input')
    page.wait_for_timeout(500)
    assert "of 16 models" in page.inner_text("#selection")


def test_empty_selection_explains_itself(page):
    page.click('[data-action="none"][data-group="project"]')
    page.click('[data-action="none"][data-group="model"]')
    page.click('[data-action="none"][data-group="provider"]')
    page.wait_for_timeout(600)
    note = page.query_selector(".no-rows")
    assert note is not None
    assert "Reset filters" in note.inner_text()
    drawn = page.eval_on_selector_all(
        ".plot-slot:not([hidden]) .main-svg", "els => els.length"
    )
    assert drawn == 0


def test_sidebar_collapse_is_remembered(browser, sample_html):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    handle = context.new_page()
    handle.goto(sample_html.resolve().as_uri())
    handle.wait_for_selector(".plot-slot .main-svg", timeout=30_000)

    handle.click("#sidebar-toggle")
    handle.wait_for_timeout(400)
    assert "sidebar-collapsed" in handle.get_attribute("#layout", "class")
    stored = handle.evaluate(
        '() => window.localStorage.getItem("opencode-dashboard-filters")'
    )
    assert stored and '"collapsed":true' in stored

    handle.reload()
    handle.wait_for_selector(".plot-slot .main-svg", timeout=30_000)
    assert "sidebar-collapsed" in handle.get_attribute("#layout", "class")
    # Charts must still size correctly once the sidebar is gone.
    assert handle.eval_on_selector_all(
        ".plot-slot:not([hidden]) .main-svg", "els => els.length"
    ) > 0
    context.close()