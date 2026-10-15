# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tests.support.synthetic import write_examples
from tracetally.cli import EXIT_INPUT, EXIT_UNPRICED, app

# Rich colors option dashes separately when a TTY or FORCE_COLOR is present,
# which splits "--format" across ANSI sequences. Pin the runner to plain text.
runner = CliRunner(env={"NO_COLOR": "1", "FORCE_COLOR": None})


def test_summary_on_jsonl_and_otel(tmp_path: Path) -> None:
    write_examples(tmp_path)
    jsonl = tmp_path / "synthetic.jsonl"
    otel = tmp_path / "synthetic_otel.json"
    text = runner.invoke(app, ["summary", str(otel)])
    assert text.exit_code == 0, text.output
    assert "gpt-4o-mini" in text.output
    assert "support.reply" in text.output
    assert "0.08665" in text.output
    assert "Offline. No network calls." in text.output

    machine = runner.invoke(app, ["summary", str(jsonl), "--json"])
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout or machine.output)
    assert payload["totals"]["cost_usd"] == "0.08665"
    assert payload["totals"]["calls"] == 31
    assert payload["error_rates"]["rate_429"] == "0.032258"

    both = runner.invoke(app, ["summary", str(jsonl), str(otel), "--json"])
    assert both.exit_code == 0
    merged = json.loads(both.stdout or both.output)
    assert merged["totals"]["calls"] == 62


def test_strict_missing_file_and_unpriced(tmp_path: Path) -> None:
    messy = tmp_path / "bad.jsonl"
    messy.write_text("not-json\n", encoding="utf-8")
    strict = runner.invoke(app, ["summary", str(messy), "--format", "jsonl", "--strict"])
    assert strict.exit_code == EXIT_INPUT
    assert "malformed JSON" in (strict.stderr or strict.output)

    missing = runner.invoke(app, ["summary", str(tmp_path / "missing.jsonl")])
    assert missing.exit_code == EXIT_INPUT

    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(
        '{"model":"no-such-model","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1,"output_tokens":1}\n',
        encoding="utf-8",
    )
    unpriced = runner.invoke(app, ["summary", str(unknown), "--json"])
    assert unpriced.exit_code == EXIT_UNPRICED
    body = json.loads(unpriced.stdout or unpriced.output)
    assert body["totals"]["cost_usd"] is None
    assert body["by_model"][0]["model"] == "no-such-model"
    assert any("unpriced model" in warning for warning in body["warnings"])


def test_prices_override_and_commands(tmp_path: Path) -> None:
    prices = tmp_path / "mine.yaml"
    prices.write_text(
        """
schema_version: 1
table_id: "mine"
currency: USD
unit: per_1m_tokens
retrieved_on: "2026-10-15"
providers:
  openai:
    models:
      no-such-model:
        input: "2"
        output: "3"
        source_url: "https://example.com/mine"
        retrieved_on: "2026-10-15"
""",
        encoding="utf-8",
    )
    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(
        '{"model":"no-such-model","provider":"openai","endpoint":"custom",'
        '"start_time":"2026-10-01T00:00:00Z","input_tokens":1000000,"output_tokens":0,'
        '"team":"ignored"}\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["summary", str(unknown), "--prices", str(prices), "--endpoint-attr", "endpoint", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout or result.output)
    assert payload["price_table_id"] == "2026-10+mine"
    assert payload["totals"]["cost_usd"] == "2"
    assert payload["by_endpoint_model"][0]["endpoint"] == "custom"

    listed = runner.invoke(app, ["prices", "list", "--json"])
    assert listed.exit_code == 0
    models = json.loads(listed.stdout or listed.output)["models"]
    assert any(
        row["model"] == "gpt-4o-mini" and row["source_url"].startswith("https://") for row in models
    )

    checked = runner.invoke(app, ["prices", "check"])
    assert checked.exit_code == 0
    assert "ok 2026-10" in checked.output

    readers = runner.invoke(app, ["readers"])
    assert readers.exit_code == 0
    assert "jsonl:" in readers.output
    assert "otel:" in readers.output

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert version.output.strip() == "0.1.0.dev0"

    help_text = runner.invoke(app, ["--help"])
    assert help_text.exit_code == 0
    assert "network" in help_text.output.lower()


def test_summary_help_lists_the_milestone_flags() -> None:
    result = runner.invoke(app, ["summary", "--help"])
    assert result.exit_code == 0
    for flag in ("--format", "--endpoint-attr", "--prices", "--json", "--strict"):
        assert flag in result.output
