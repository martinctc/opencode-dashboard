import json
import re
import warnings

import pandas as pd
import pytest

from dashboard import build_dashboard, load_data, main, redact_data


def sample_data():
    return pd.DataFrame([{
        "user": "test-user",
        "date": "2026-10-06",
        "project": "sample-project",
        "provider": "openai",
        "model": "openai/gpt-test",
        "calls": 1,
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read_tokens": 10,
        "cache_write_tokens": 2,
        "reasoning_tokens": 5,
        "total_tokens": 140,
        "cost_usd": 0.25,
        "session_id": "session-1",
    }])


def test_build_dashboard_writes_offline_html(tmp_path):
    output = tmp_path / "dashboard.html"

    build_dashboard(sample_data(), output)
    html = output.read_text(encoding="utf-8")

    assert "OpenCode Usage Dashboard" in html
    assert "140" in html
    # Token categories are labelled for display rather than by raw column name.
    assert "Input" in html and "Output" in html
    assert not re.search(r'<script[^>]+src=["\']https://cdn\.plot\.ly', html)


def test_cli_writes_a_dashboard(tmp_path, capsys):
    """The command line entry point must actually produce a file.

    The other tests call build_dashboard() directly, so a regression that only
    breaks the CLI - a missing main(), an unimported name - would pass the whole
    suite while `python dashboard.py` silently did nothing.
    """
    source = sample_data()
    source["variant"] = "n/a"
    source["exported_at"] = "2026-10-06T10:00:00+00:00"
    csv_path = tmp_path / "usage.csv"
    source.to_csv(csv_path, index=False)
    output = tmp_path / "out.html"

    result = main(["--in", str(csv_path), "--out", str(output)])

    assert result == 0
    assert output.exists()
    assert output.stat().st_size > 0
    assert str(output) in capsys.readouterr().out
    assert "OpenCode Usage Dashboard" in output.read_text(encoding="utf-8")


def test_load_data_requires_usage_columns(tmp_path):
    path = tmp_path / "bad.csv"
    pd.DataFrame([{"user": "test-user"}]).to_csv(path, index=False)

    with pytest.raises(ValueError, match="missing required columns"):
        load_data(str(path))


def test_load_data_keeps_latest_overlapping_export(tmp_path):
    older = sample_data()
    older["variant"] = "high"
    older["exported_at"] = "2026-10-06T10:00:00+00:00"
    newer = older.copy()
    newer["total_tokens"] = 160
    newer["exported_at"] = "2026-10-06T11:00:00+00:00"
    older.to_csv(tmp_path / "older.csv", index=False)
    newer.to_csv(tmp_path / "newer.csv", index=False)

    with pytest.warns(RuntimeWarning, match="overlapping export"):
        data = load_data(str(tmp_path / "*.csv"))

    assert len(data) == 1
    assert data.iloc[0]["total_tokens"] == 160


def test_load_data_reads_export_without_metadata(tmp_path):
    """Exports predating versioning have no exported_at or version column.

    They are the reason export metadata is not a required column: failing here
    would lock the dashboard out of its own earlier files.
    """
    legacy = sample_data()
    legacy["variant"] = "high"
    legacy = legacy.drop(columns=["exported_at"], errors="ignore")
    legacy.to_csv(tmp_path / "legacy.csv", index=False)

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        data = load_data(str(tmp_path / "legacy.csv"))

    assert len(data) == 1
    assert data["exported_at"].isna().all()


def test_load_data_warns_on_newer_export_format(tmp_path):
    """A newer format may add columns this build never sees, so say so."""
    future = sample_data()
    future["variant"] = "high"
    future["exported_at"] = "2026-10-06T10:00:00+00:00"
    future["export_format_version"] = "99"
    future.to_csv(tmp_path / "future.csv", index=False)

    with pytest.warns(RuntimeWarning, match="export format 99"):
        load_data(str(tmp_path / "future.csv"))


def test_load_data_warns_when_overlap_ordering_is_uncertain(tmp_path):
    """Without timestamps there is no principled 'latest', so the file-order
    fallback must not be implied to be timestamp-based."""
    stamped = sample_data()
    stamped["variant"] = "high"
    stamped["exported_at"] = "2026-10-06T10:00:00+00:00"
    unstamped = stamped.copy()
    unstamped["total_tokens"] = 160
    unstamped = unstamped.drop(columns=["exported_at"])
    stamped.to_csv(tmp_path / "stamped.csv", index=False)
    unstamped.to_csv(tmp_path / "unstamped.csv", index=False)

    with pytest.warns(RuntimeWarning, match="no export timestamp"):
        data = load_data(str(tmp_path / "*.csv"))

    # Falls back to file order: "unstamped.csv" sorts last, so it wins.
    assert len(data) == 1
    assert data.iloc[0]["total_tokens"] == 160


def _two_row_frame() -> pd.DataFrame:
    """A two-row frame with distinct project and session ids, complete for load_data."""
    base = sample_data().copy()
    frame = pd.concat([base, base], ignore_index=True)
    frame["variant"] = "high"
    frame.loc[0, "session_id"] = "alpha"
    frame.loc[1, "session_id"] = "beta"
    return frame


def test_redact_data_replaces_sessions_with_stable_tokens(tmp_path):
    """Excluded session ids become stable sequential tokens, regardless of order."""
    frame = _two_row_frame()
    frame.to_csv(tmp_path / "src.csv", index=False)
    data = load_data(str(tmp_path / "src.csv"))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        first = redact_data(data, exclude_session_id=True)
        second = redact_data(first, exclude_session_id=True)

    assert first["session_id"].nunique() == second["session_id"].nunique()
    assert len(set(first["session_id"])) == 2
    for value in first["session_id"]:
        assert value.startswith("session-")


def test_redact_data_drops_excluded_project(tmp_path):
    """Excluded projects drop their rows and read as '(excluded)'."""
    frame = _two_row_frame()
    frame["project"] = "proj-redact"
    frame.to_csv(tmp_path / "src.csv", index=False)
    data = load_data(str(tmp_path / "src.csv"))

    redacted = redact_data(data, exclude_project=True)

    assert set(redacted["project"]) == {"(excluded)"}
    assert all(redacted["project"] == "(excluded)")


def test_build_dashboard_redaction_leaves_no_original_identifiers(tmp_path):
    """The generated HTML carries neither excluded project names nor raw session ids."""
    frame = _two_row_frame()
    frame["project"] = "proj-redact"
    frame.to_csv(tmp_path / "src.csv", index=False)
    data = load_data(str(tmp_path / "src.csv"))

    html = tmp_path / "out.html"
    build_dashboard(
        data,
        html,
        exclude_project=True,
        exclude_session_id=True,
    )
    content = html.read_text(encoding="utf-8")

    # the project name never reaches the page at all
    assert "proj-redact" not in content

    # session ids are relabelled before embedding, so the originals never enter the
    # embedded rows JSON - only the stable tokens do. Searching for the quoted
    # values ("project":"(excluded)" and "session_id":"...") matches our data only;
    # Plotly's own code does not use those keys, so bare "alpha"/"beta" would match
    # Plotly internals and give a false positive.
    assert content.count('"project":"(excluded)"') == len(data)
    assert '"session_id":"alpha"' not in content
    assert '"session_id":"beta"' not in content
    # the relabelled ids are present and stable across the payload
    assert '"session-0"' in content
    assert '"session-1"' in content
