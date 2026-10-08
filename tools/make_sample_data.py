#!/usr/bin/env python3
"""Generate synthetic OpenCode usage CSVs for development and documentation.

The interactive work (filters, date ranges, metric toggles) cannot be evaluated
against a real export: six days of data with seven projects makes every control
look broken regardless of whether it works. This generator produces a
deterministic stand-in with the same column schema, a realistic mix of free and
paid models, and enough days for the date controls to mean something.

Project names use the fictitious companies Microsoft uses in documentation, so
no real project name can reach a published screenshot.

The random seed, date range and export timestamp are all fixed, so two runs
produce byte-identical output.

Usage:
    python tools/make_sample_data.py --out sample_usage.csv
"""

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

EXPORT_FORMAT_VERSION = "1"
SOURCE = "OpenCode"
FIXED_EXPORTED_AT = "2026-10-07T18:00:00+00:00"
LAST_DAY = date(2026, 10, 6)

PROJECTS = [
    ("contoso/insights-sample-code", 1.00),
    ("contoso/insights-toolkit-py", 0.62),
    ("northwind/site-analytics", 0.48),
    ("fabrikam/billing-api", 0.41),
    ("adventure-works/catalog", 0.30),
    ("tailwind-traders/storefront", 0.24),
    ("woodgrove-bank/ledger", 0.18),
    ("projit/plan-render", 0.12),
]

# (model, gateway, reach, unit_usd_per_mtok, free_tier)
# unit cost is USD per million input+output tokens; 0.0 marks a free model, whose
# rows must record a genuine zero cost rather than a missing value.
MODELS = [
    ("opencode/space-bunny-alpha", "opencode", 1.00, 0.0, True),
    ("opencode/mimo-v2.6-flash-free", "opencode", 0.78, 0.0, True),
    ("opencode/nemotron-3-ultra-free", "opencode", 0.46, 0.0, True),
    ("opencode/big-pickle", "opencode", 0.31, 0.0, True),
    ("openrouter/google/gemma-4-31b-it:free", "openrouter", 0.42, 0.0, True),
    ("openrouter/qwen/qwen3.8-27b:free", "openrouter", 0.27, 0.0, True),
    ("azure-personal/DeepSeek-V3.2", "azure-personal", 0.52, 0.0, True),
    ("openrouter/anthropic/claude-sonnet-5", "openrouter", 0.61, 3.00, False),
    ("openrouter/anthropic/claude-opus-5", "openrouter", 0.23, 15.00, False),
    ("openrouter/openai/gpt-5.6-sol", "openrouter", 0.38, 12.00, False),
    ("openrouter/openai/gpt-5.4", "openrouter", 0.19, 8.00, False),
    ("openrouter/google/gemini-3.5-flash", "openrouter", 0.26, 1.50, False),
    ("openrouter/xai/grok-4.5", "openrouter", 0.11, 7.00, False),
    ("openrouter/nvidia/nemotron-3.5-lightning:paid", "openrouter", 0.17, 0.60, False),
    ("azure/deepseek-v4-flash", "azure", 0.21, 0.45, False),
    ("azure/gpt-5.4-deployment", "azure", 0.13, 9.50, False),
]

# Reasoning effort is a per-model setting, so only the models that declare one
# ever produce a variant. Everything else records "n/a" like a real export.
EFFORT_MODELS = {
    "openrouter/anthropic/claude-sonnet-5": ["low", "medium", "high"],
    "openrouter/anthropic/claude-opus-5": ["medium", "high"],
    "openrouter/openai/gpt-5.6-sol": ["low", "high"],
    "azure/gpt-5.4-deployment": ["medium"],
}
EFFORT_TOKENS_MULTIPLIER = {"low": 0.6, "medium": 1.0, "high": 1.9}

FIELDS = [
    "source", "user", "date", "project", "provider", "model", "variant",
    "calls", "input_tokens", "output_tokens", "cache_read_tokens",
    "cache_write_tokens", "reasoning_tokens", "total_tokens", "cost_usd",
    "session_id", "export_format_version", "exported_at",
]


def build_rows(days: int, seed: int) -> list:
    rng = random.Random(seed)
    rows = []
    session = 0

    for offset in range(days):
        day = LAST_DAY - timedelta(days=days - 1 - offset)
        # Day-level intensity drives the trend chart. Without a smooth factor
        # each day's total swings by an order of magnitude purely from the
        # per-row coin flips, which reads as noise rather than as usage.
        progress = offset / max(days - 1, 1)
        trend = 0.75 + 0.50 * progress
        # Real usage drops at weekends; without this the trend looks automated.
        weekday = 0.45 if day.weekday() >= 5 else 1.0
        day_scale = trend * weekday * rng.uniform(0.88, 1.12)

        for project, project_reach in PROJECTS:
            # Presence is decoupled from weight: a busy project shows up on most
            # days and a quiet one on about half. Tying presence directly to
            # reach makes quiet days so empty that the daily totals swing by
            # more than an order of magnitude.
            if rng.random() > 0.30 + 0.60 * project_reach:
                continue
            for model, gateway, reach, unit_cost, free in MODELS:
                if rng.random() > 0.40 + 0.50 * reach:
                    continue
                session += 1
                calls = rng.randint(2, 40)
                scale = project_reach * reach * day_scale

                effort = "n/a"
                if model in EFFORT_MODELS and rng.random() < 0.7:
                    effort = rng.choice(EFFORT_MODELS[model])

                input_tokens = int(rng.randint(4000, 42000) * calls * scale)
                output_tokens = int(input_tokens * rng.uniform(0.07, 0.21))
                # Prompt caching dominates repeat turns, so cache read runs well
                # above the conversation itself - this mirrors real exports.
                cache_read = int(input_tokens * rng.uniform(6.0, 22.0))
                cache_write = int(input_tokens * rng.uniform(0.02, 0.30))
                reasoning = int(
                    output_tokens
                    * EFFORT_TOKENS_MULTIPLIER.get(effort, 1.0)
                    * rng.uniform(0.0, 0.6)
                )
                total = input_tokens + output_tokens

                if free:
                    cost = 0.0
                else:
                    cost = round(total / 1_000_000 * unit_cost, 6)

                rows.append({
                    "source": SOURCE,
                    "user": "sample-user",
                    "date": day.isoformat(),
                    "project": project,
                    "provider": gateway,
                    "model": model,
                    "variant": effort,
                    "calls": calls,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_read_tokens": cache_read,
                    "cache_write_tokens": cache_write,
                    "reasoning_tokens": reasoning,
                    "total_tokens": total,
                    "cost_usd": cost,
                    "session_id": "sample-%06d" % session,
                    "export_format_version": EXPORT_FORMAT_VERSION,
                    "exported_at": FIXED_EXPORTED_AT,
                })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("sample_usage.csv"))
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--seed", type=int, default=20261006)
    args = parser.parse_args()

    rows = build_rows(args.days, args.seed)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    priced = sum(1 for row in rows if row["cost_usd"] > 0)
    print(f"Wrote {len(rows)} rows to {args.out}")
    print(f"  {priced} priced ({priced / len(rows):.1%}), "
          f"{len({row['model'] for row in rows})} models, "
          f"{len({row['project'] for row in rows})} projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())