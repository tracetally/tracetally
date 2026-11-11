# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from tests.support.synthetic import write_examples
from tracetally._cta import AUDIT_CTA_TEXT, AUDIT_CTA_URL
from tracetally.cli import EXIT_INPUT, EXIT_OK, EXIT_UNPRICED, app

# GitHub Actions sets GITHUB_ACTIONS, and Typer then forces a color terminal.
# Rich's option highlighter styles each dash on its own, so "--format" is not
# a contiguous substring until the ANSI codes are removed.
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
runner = CliRunner()


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_summary_and_analyze_keep_json_stdout_parseable(tmp_path: Path) -> None:
    write_examples(tmp_path)
    path = tmp_path / "synthetic.jsonl"
    summary = runner.invoke(app, ["summary", str(path), "--json", "--offline"])
    assert summary.exit_code == EXIT_OK, summary.output
    payload = json.loads(summary.stdout)
    assert payload["totals"]["calls"] > 0
    assert AUDIT_CTA_URL not in summary.stdout
    assert f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}" in (summary.stderr or "")

    text = runner.invoke(app, ["summary", str(path), "--offline"])
    assert text.exit_code == EXIT_OK, text.output
    assert text.stdout.rstrip().endswith(f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}")

    analyzed = runner.invoke(app, ["analyze", str(path), "--json", "--offline"])
    assert analyzed.exit_code == EXIT_OK, analyzed.output
    parsed = json.loads(analyzed.stdout)
    assert parsed["findings"]
    assert AUDIT_CTA_URL not in analyzed.stdout
    assert f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}" in (analyzed.stderr or "")


def test_summary_analyze_and_report_strip_control_names(tmp_path: Path) -> None:
    source = tmp_path / "calls.jsonl"
    source.write_text(
        json.dumps(
            {
                "model": "gpt-4o-mini",
                "provider": "openai",
                "endpoint": "edge\x1b[2J",
                "start_time": "2026-10-01T00:00:00Z",
                "input_tokens": 10,
                "output_tokens": 1,
                "http_status": 500,
                "duration_ms": 20,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    summary = runner.invoke(app, ["summary", str(source), "--offline"])
    assert summary.exit_code == EXIT_OK, summary.output
    assert "\x1b" not in summary.stdout
    assert "gpt-4o-mini" in summary.stdout
    analyzed = runner.invoke(app, ["analyze", str(source), "--offline"])
    assert analyzed.exit_code == EXIT_OK, analyzed.output
    assert "\x1b" not in analyzed.stdout
    out = tmp_path / "report"
    reported = runner.invoke(app, ["report", str(source), "--offline", "--out", str(out)])
    assert reported.exit_code == EXIT_OK, reported.output
    assert "\x1b" not in reported.stdout
    assert reported.stdout.rstrip().endswith(f"{AUDIT_CTA_TEXT}: {AUDIT_CTA_URL}")


def test_summary_on_jsonl_and_otel(tmp_path: Path) -> None:
    write_examples(tmp_path)
    jsonl = tmp_path / "synthetic.jsonl"
    otel = tmp_path / "synthetic_otel.json"
    text = runner.invoke(app, ["summary", str(otel), "--offline"])
    assert text.exit_code == 0, text.output
    assert "gpt-4o-mini" in text.output
    assert "support.reply" in text.output
    assert "0.292410" in text.output
    assert "No telemetry." in text.output

    machine = runner.invoke(app, ["summary", str(jsonl), "--json", "--offline"])
    assert machine.exit_code == 0, machine.output
    payload = json.loads(machine.stdout or machine.output)
    assert payload["totals"]["cost_usd"] == "0.29241"
    assert payload["totals"]["calls"] == 235
    assert payload["error_rates"]["n_429"] == 11
    assert payload["error_rates"]["rate_429"] == f"{11 / 235:.6f}"

    both = runner.invoke(app, ["summary", str(jsonl), str(otel), "--json", "--offline"])
    assert both.exit_code == 0
    merged = json.loads(both.stdout or both.output)
    assert merged["totals"]["calls"] == 470


def test_strict_missing_file_and_unpriced(tmp_path: Path) -> None:
    messy = tmp_path / "bad.jsonl"
    messy.write_text("not-json\n", encoding="utf-8")
    strict = runner.invoke(
        app, ["summary", str(messy), "--format", "jsonl", "--strict", "--offline"]
    )
    assert strict.exit_code == EXIT_INPUT
    assert "malformed JSON" in (strict.stderr or strict.output)

    missing = runner.invoke(app, ["summary", str(tmp_path / "missing.jsonl"), "--offline"])
    assert missing.exit_code == EXIT_INPUT

    unknown = tmp_path / "unknown.jsonl"
    unknown.write_text(
        '{"model":"no-such-model","provider":"openai","start_time":"2026-10-01T00:00:00Z",'
        '"input_tokens":1,"output_tokens":1}\n',
        encoding="utf-8",
    )
    unpriced = runner.invoke(app, ["summary", str(unknown), "--json", "--offline"])
    assert unpriced.exit_code == EXIT_UNPRICED
    body = json.loads(unpriced.stdout or unpriced.output)
    assert body["totals"]["cost_usd"] is None
    assert body["by_model"][0]["model"] == "no-such-model"
    assert any("unpriced model" in warning for warning in body["warnings"])


def test_prices_override_and_commands(tmp_path: Path) -> None:
    prices = tmp_path / "tracetally.toml"
    prices.write_text(
        """
[prices."no-such-model"]
provider = "openai"
input = "2"
output = "3"
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
        [
            "summary",
            str(unknown),
            "--offline",
            "--pricing-config",
            str(prices),
            "--endpoint-attr",
            "endpoint",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout or result.output)
    assert payload["price_table_id"] == "litellm:bundled+toml"
    assert payload["price_source"] == "bundled"
    assert payload["totals"]["cost_usd"] == "2"
    assert payload["by_endpoint_model"][0]["endpoint"] == "custom"

    listed = runner.invoke(app, ["prices", "list", "--json", "--offline"])
    assert listed.exit_code == 0
    models = json.loads(listed.stdout or listed.output)["models"]
    assert any(
        row["model"] == "gpt-4o-mini" and row["source_url"].startswith("https://") for row in models
    )

    checked = runner.invoke(app, ["prices", "check", "--offline"])
    assert checked.exit_code == 0
    assert "source=bundled" in checked.output

    readers = runner.invoke(app, ["readers"])
    assert readers.exit_code == 0
    assert "jsonl:" in readers.output
    assert "otel:" in readers.output
    assert "langfuse:" in readers.output
    assert "openai_csv:" in readers.output

    version = runner.invoke(app, ["version"])
    assert version.exit_code == 0
    assert version.output.strip() == "0.1.0.dev0"

    help_text = runner.invoke(app, ["--help"])
    assert help_text.exit_code == 0
    assert "network" in help_text.output.lower()


def test_prices_without_a_subcommand_uses_the_blocked_fetch_fallback(
    allow_price_fetch: None,
) -> None:
    listed_default = runner.invoke(app, ["prices"])
    assert listed_default.exit_code == 0
    assert "gpt-4o-mini" in listed_default.output
    assert "reasoning" in listed_default.output


def test_summary_help_lists_the_milestone_flags() -> None:
    result = runner.invoke(app, ["summary", "--help"])
    assert result.exit_code == 0
    help_text = _plain(result.output)
    for flag in ("--format", "--endpoint-attr", "--offline", "--json", "--strict"):
        assert flag in help_text
