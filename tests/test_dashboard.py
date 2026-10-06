import re

import pandas as pd
import pytest

from dashboard import build_dashboard, load_data


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
    assert "input_tokens" in html
    assert not re.search(r'<script[^>]+src=["\']https://cdn\.plot\.ly', html)


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
