# Compatibility and data format

## Database compatibility

The exporter reads `session`, `message`, and `project` tables from OpenCode's
local SQLite database. It needs session IDs, project IDs, directories and
titles; message IDs, session IDs and JSON data containing assistant-message
timestamps; and project IDs and names. The exact schema can change between
OpenCode releases.

The extractor opens the source read-only, creates a consistent SQLite backup,
and validates required columns before reading. If validation fails, it stops
with the missing table or columns listed in the error.

Use `--db` to provide the database path explicitly. Otherwise, the extractor
checks `OPENCODE_DATA_DIR`, `XDG_DATA_HOME`, and the default per-user OpenCode
data directory for `opencode.db`. Verify the detected path against your
installed OpenCode configuration if you have customised its storage location.

## CSV format

The CSV includes a `source` column and one row per session, date, provider,
model, and variant. It records input, output, cache-read, cache-write, and
reasoning token counters separately. `total_tokens` is input plus output.
`cost_usd` is the recorded OpenCode cost for the row.

`session_title` is omitted unless `--include-session-title` is passed. Session
IDs are retained to support identifying repeated records when reviewing
exports. Do not share exports without checking for sensitive information.

## Source schema

The extractor relies on the `message.data` JSON object containing assistant
message fields (`role`, `time.created`, `providerID`, `modelID`, `variant`,
`cost`, and `tokens`) and on `session`/`project` metadata for project
identification. This reflects the current OpenCode implementation and may
change without notice.
