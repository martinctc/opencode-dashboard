#!/usr/bin/env python3
"""Export token and cost usage from OpenCode's local SQLite database."""

import argparse
import csv
import getpass
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


EXPORT_FORMAT_VERSION = "1"
REQUIRED_SCHEMA = {
    "session": {"id", "project_id", "directory", "title"},
    "message": {"id", "session_id", "data"},
    "project": {"id", "name"},
}
OUTPUT_COLUMNS = [
    "source", "user", "date", "project", "provider", "model", "variant",
    "calls", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens", "cost_usd",
    "session_id", "export_format_version", "exported_at",
]
def default_db_path() -> Path:
    """Resolve the conventional OpenCode data directory, with env overrides."""
    if os.environ.get("OPENCODE_DATA_DIR"):
        return Path(os.environ["OPENCODE_DATA_DIR"]).expanduser() / "opencode.db"
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / "opencode" / "opencode.db"
    return Path.home() / ".local" / "share" / "opencode" / "opencode.db"


def validate_schema(conn: sqlite3.Connection, db_path: Path) -> None:
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    problems = []
    for table, required_columns in REQUIRED_SCHEMA.items():
        if table not in tables:
            problems.append(f"missing table '{table}'")
            continue
        present = {
            row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')
        }
        missing = sorted(required_columns - present)
        if missing:
            problems.append(f"table '{table}' is missing column(s): {', '.join(missing)}")
    if problems:
        details = "\n  - ".join(problems)
        raise ValueError(
            f"{db_path} does not match the OpenCode database schema this "
            f"extractor expects:\n  - {details}\n"
            "The local schema can change between OpenCode releases."
        )


class ReadOnlySnapshot:
    """Read a point-in-time SQLite backup without modifying the source."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.temp_dir = None
        self.connection = None

    def __enter__(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(f"OpenCode database not found: {self.db_path}")
        self.temp_dir = tempfile.mkdtemp(prefix="opencode_usage_")
        snapshot_path = Path(self.temp_dir) / "snapshot.db"
        source = None
        destination = None
        try:
            source = sqlite3.connect(
                f"{self.db_path.resolve().as_uri()}?mode=ro", uri=True
            )
            source.execute("SELECT 1")
            destination = sqlite3.connect(snapshot_path)
            source.backup(destination)
            destination.close()
            destination = None
            self.connection = sqlite3.connect(
                f"{snapshot_path.resolve().as_uri()}?mode=ro", uri=True
            )
            return self.connection
        except sqlite3.Error as exc:
            self._cleanup()
            raise RuntimeError(
                f"Could not read OpenCode database {self.db_path}: {exc}"
            ) from exc
        finally:
            if source is not None:
                source.close()
            if destination is not None:
                destination.close()

    def _cleanup(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = None

    def __exit__(self, exc_type, exc, tb):
        self._cleanup()
        return False


def _nonnegative_number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid {label} in OpenCode message data: {value!r}")
    if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
        raise ValueError(f"Invalid or negative {label} in OpenCode message data: {value!r}")
    return value


def _project_name(directory: str, name: str) -> str:
    value = name or directory or ""
    value = value.replace("\\", "/").rstrip("/")
    return value.rsplit("/", 1)[-1] or "(unknown)"


def read_usage(conn: sqlite3.Connection, user_label: str, include_title: bool):
    query = """
        SELECT m.id, m.session_id, m.data, s.directory, s.title, p.name
        FROM message AS m
        JOIN session AS s ON s.id = m.session_id
        LEFT JOIN project AS p ON p.id = s.project_id
        ORDER BY m.id
    """
    grouped = {}
    for row in conn.execute(query):
        message_id, session_id, raw_data, directory, title, project = row
        try:
            message = json.loads(raw_data)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON for OpenCode message {message_id}") from exc
        if not isinstance(message, dict):
            raise ValueError(f"Invalid OpenCode message data for {message_id}")
        if message.get("role") != "assistant":
            continue
        tokens = message.get("tokens")
        if not isinstance(tokens, dict):
            raise ValueError(f"Missing token data for OpenCode message {message_id}")
        cache = tokens.get("cache")
        if not isinstance(cache, dict):
            raise ValueError(f"Missing cache token data for OpenCode message {message_id}")
        provider = message.get("providerID")
        model_id = message.get("modelID")
        if not provider or not model_id:
            raise ValueError(f"Missing provider or model ID for OpenCode message {message_id}")
        variant = message.get("variant") or "n/a"
        timestamp = (message.get("time") or {}).get("created")
        timestamp = _nonnegative_number(timestamp, f"timestamp for message {message_id}")
        day = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).date().isoformat()
        identity = (session_id, day, provider, model_id, variant)
        if identity not in grouped:
            record = {
                "source": "OpenCode",
                "user": user_label,
                "date": day,
                "project": _project_name(directory or "", project or ""),
                "provider": provider,
                "model": f"{provider}/{model_id}",
                "variant": variant,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "session_id": session_id,
                "export_format_version": EXPORT_FORMAT_VERSION,
            }
            if include_title:
                record["session_title"] = title or ""
            grouped[identity] = record
        record = grouped[identity]
        input_tokens = _nonnegative_number(tokens.get("input"), "input tokens")
        output_tokens = _nonnegative_number(tokens.get("output"), "output tokens")
        record["calls"] += 1
        record["input_tokens"] += input_tokens
        record["output_tokens"] += output_tokens
        record["cache_read_tokens"] += _nonnegative_number(
            cache.get("read"), "cache read tokens"
        )
        record["cache_write_tokens"] += _nonnegative_number(
            cache.get("write"), "cache write tokens"
        )
        record["reasoning_tokens"] += _nonnegative_number(
            tokens.get("reasoning"), "reasoning tokens"
        )
        record["total_tokens"] += input_tokens + output_tokens
        record["cost_usd"] += _nonnegative_number(message.get("cost"), "cost")
    return list(grouped.values())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help="OpenCode database path (default: detected user data directory)")
    parser.add_argument("--out", type=Path, default=None, help="CSV output path")
    parser.add_argument("--user-label", default=None, help="Label for this export (default: OS username)")
    parser.add_argument("--include-session-title", action="store_true",
                        help="Include free-text session titles, which may contain sensitive information")
    args = parser.parse_args(argv)
    out_path = args.out or Path(
        f"opencode_usage_{args.user_label or getpass.getuser()}_"
        f"{datetime.now().strftime('%Y-%m-%d')}.csv"
    )
    try:
        with ReadOnlySnapshot(args.db) as conn:
            validate_schema(conn, args.db)
            rows = read_usage(conn, args.user_label or getpass.getuser(), args.include_session_title)
    except (FileNotFoundError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    exported_at = datetime.now(timezone.utc).isoformat()
    columns = OUTPUT_COLUMNS + (["session_title"] if args.include_session_title else [])
    with out_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            row["exported_at"] = exported_at
            writer.writerow(row)
    print(f"Wrote {len(rows)} rows to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
