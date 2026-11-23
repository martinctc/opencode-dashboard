# Security Policy

## Supported versions

This project is pre-1.0 and maintained on the `main` branch. Fixes are applied
to `main` and released as they become available. If you are using an older
release, please upgrade to the latest before reporting an issue.

## Reporting a vulnerability

Please report security issues privately rather than in a public issue:

1. Use GitHub's private vulnerability reporting: **Security → Advisories →
   Report a vulnerability** on the
   [repository](https://github.com/martinctc/opencode-dashboard/security/advisories/new).
2. Alternatively, email **martinchan53@gmail.com**.

Please include:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- the affected version or commit, and your operating system and Python version.

You can expect an acknowledgement within a few days. Please do not disclose the
issue publicly until a fix is available.

## A note on sensitive data

This tool is local and read-only: it opens OpenCode's SQLite database through a
read-only snapshot and never modifies the live database. That said, the CSV
exports and generated HTML it produces can contain usernames, project names,
model names, dates, and optional session titles. Treat generated files as
sensitive and review them before sharing.

A bug in how the tool handles or redacts that data (for example, redaction that
fails to remove an excluded project) should be reported through the private
channels above.
