import csv
import json
import sqlite3

import pytest

from extract_usage import main, read_usage, validate_schema


@pytest.fixture
def usage_db(tmp_path):
    path = tmp_path / "opencode.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE session (
            id TEXT PRIMARY KEY, project_id TEXT, directory TEXT, title TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        INSERT INTO project VALUES ('p1', 'sample-project');
        INSERT INTO session VALUES ('s1', 'p1', 'C:\\\\work\\\\sample-project', 'Private title');
    """)
    def message(message_id, role, **extra):
        data = {
            "role": role,
            "time": {"created": 1791288000000},
            "providerID": "openai",
            "modelID": "gpt-test",
            "variant": "high",
            "cost": 0.25,
            "tokens": {
                "input": 100, "output": 40, "reasoning": 5,
                "cache": {"read": 10, "write": 2},
            },
        }
        data.update(extra)
        conn.execute(
            "INSERT INTO message VALUES (?, 's1', 1791288000000, ?)",
            (message_id, json.dumps(data)),
        )
    message("m1", "assistant")
    message("m2", "assistant")
    message("m3", "user")
    conn.commit()
    yield conn
    conn.close()


def test_extract_groups_assistant_messages_and_keeps_token_categories(usage_db):
    validate_schema(usage_db, "fixture")
    rows = read_usage(usage_db, "test-user", include_title=False)

    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "OpenCode"
    assert row["user"] == "test-user"
    assert row["project"] == "sample-project"
    assert row["provider"] == "openai"
    assert row["model"] == "openai/gpt-test"
    assert row["calls"] == 2
    assert row["input_tokens"] == 200
    assert row["output_tokens"] == 80
    assert row["total_tokens"] == 280
    assert row["reasoning_tokens"] == 10
    assert row["cache_read_tokens"] == 20
    assert row["cache_write_tokens"] == 4
    assert row["cost_usd"] == 0.5
    assert "session_title" not in row


def test_session_title_is_opt_in(usage_db):
    row = read_usage(usage_db, "test-user", include_title=True)[0]
    assert row["session_title"] == "Private title"


def test_schema_validation_reports_missing_tables(tmp_path):
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError, match="missing table 'session'"):
        validate_schema(conn, tmp_path / "bad.db")
    conn.close()


def test_cli_exports_from_read_only_snapshot(usage_db, tmp_path):
    db_path = tmp_path / "opencode.db"
    output = tmp_path / "usage.csv"

    assert main([
        "--db", str(db_path), "--out", str(output), "--user-label", "test-user",
    ]) == 0

    with output.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == "280"
    assert rows[0]["cost_usd"] == "0.5"
